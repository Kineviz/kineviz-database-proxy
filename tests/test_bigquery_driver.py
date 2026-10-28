"""
The BigQuery driver's pure parts: the statement rewrite, the result mapping and
the property-graph metadata mapping.

No BigQuery client is constructed — what is under test is the translation between
GQL and the contract, which is where every BigQuery-specific decision lives.
"""

from __future__ import annotations

import datetime
import decimal

import pytest

from kineviz_database_proxy.drivers.bigquery import (
    InvalidIdentifierError,
    as_graph_elements,
    json_safe,
    map_graph_metadata,
    parse_graph_rows,
    parse_table_rows,
    pattern_variables,
    rewrite_graph_query,
    validate_identifier,
)
from kineviz_database_proxy.drivers.dialect import BIGQUERY_DIALECT, NodeRef, build_expand, build_pull_category


class Row(dict):
    """Stands in for a ``google.cloud.bigquery.Row``: indexable and iterable by name."""


# ---------------------------------------------------------------------------
# identifiers
# ---------------------------------------------------------------------------


def test_a_plain_dataset_or_graph_name_is_accepted():
    assert validate_identifier("my_dataset", "dataset") == "my_dataset"
    assert validate_identifier("graph-1", "graph name") == "graph-1"


@pytest.mark.parametrize("name", ["", None, "has space", "back`tick", "semi;colon", "1leading"])
def test_a_name_that_could_change_the_statement_is_rejected(name):
    # The GRAPH clause has no parameter form, so these are the only names that can
    # be interpolated safely.
    with pytest.raises(InvalidIdentifierError):
        validate_identifier(name, "dataset")


# ---------------------------------------------------------------------------
# rewrite_graph_query
# ---------------------------------------------------------------------------


def test_a_bare_match_gains_the_graph_clause_a_projection_and_a_limit():
    statement, is_graph = rewrite_graph_query("MATCH (n) RETURN n", "ds.g", max_results=500)
    assert is_graph
    assert statement == "GRAPH ds.g\nMATCH (n)\nRETURN TO_JSON(n) AS n\nLIMIT 500"


def test_return_star_projects_every_variable_the_pattern_binds():
    statement, is_graph = rewrite_graph_query("MATCH (n)-[r]->(m) RETURN * LIMIT 5", "ds.g")
    assert is_graph
    assert "RETURN TO_JSON(n) AS n, TO_JSON(r) AS r, TO_JSON(m) AS m" in statement


def test_an_explicit_variable_list_is_respected_rather_than_widened():
    # The expand statement returns only the far side on purpose; projecting the
    # seeds again would double the payload of every expansion.
    statement, _ = rewrite_graph_query("MATCH (n)-[r]->(m) RETURN r,m LIMIT 5", "ds.g")
    assert "RETURN TO_JSON(r) AS r, TO_JSON(m) AS m" in statement
    assert "TO_JSON(n)" not in statement


def test_the_generated_expand_statement_survives_the_rewrite():
    seeds = [NodeRef(internal_id="101"), NodeRef(internal_id="102")]
    (generated,) = build_expand(BIGQUERY_DIALECT, seeds, direction="all", limit=25)
    statement, is_graph = rewrite_graph_query(generated, "ds.g")
    assert is_graph
    assert statement.startswith("GRAPH ds.g\nMATCH (n)-[r]-(m) FILTER WHERE")
    assert "RETURN TO_JSON(r) AS r, TO_JSON(m) AS m" in statement
    # The dialect's own pagination is carried through untouched.
    assert statement.endswith("SKIP 0 LIMIT 25")


def test_the_generated_pull_statement_survives_the_rewrite():
    (generated,) = build_pull_category(BIGQUERY_DIALECT, "Person", limit=25)
    statement, is_graph = rewrite_graph_query(generated, "ds.g")
    assert is_graph
    assert "MATCH (n:`Person`)" in statement
    assert "RETURN TO_JSON(n) AS n" in statement


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n) RETURN n.name LIMIT 5",
        "MATCH (n) RETURN count(*)",
        "MATCH (n) RETURN n AS person",
        "MATCH (n) RETURN TO_JSON(n)",
    ],
)
def test_a_projection_is_a_table_and_is_not_rewritten(query):
    statement, is_graph = rewrite_graph_query(query, "ds.g")
    assert is_graph is False
    # Still gets its graph namespace, so it parses; only the projection is left alone.
    assert statement.startswith("GRAPH ds.g")
    assert "TO_JSON(n) AS n" not in statement


def test_plain_sql_is_left_entirely_alone():
    statement, is_graph = rewrite_graph_query("SELECT * FROM `ds.tbl` LIMIT 3", "ds.g")
    assert (statement, is_graph) == ("SELECT * FROM `ds.tbl` LIMIT 3", False)


def test_a_statement_that_names_its_own_graph_keeps_it():
    statement, is_graph = rewrite_graph_query("GRAPH other.graph MATCH (n) RETURN *", "ds.g")
    assert is_graph
    assert statement.startswith("GRAPH other.graph")
    assert "ds.g" not in statement


def test_a_returning_statement_without_a_limit_gets_one():
    statement, _ = rewrite_graph_query("SELECT x FROM t", "ds.g", max_results=7)
    assert statement == "SELECT x FROM t"  # no RETURN, so nothing to cap

    statement, _ = rewrite_graph_query("MATCH (n) RETURN n.name", "ds.g", max_results=7)
    assert statement.endswith("LIMIT 7")


def test_pattern_variables_are_first_seen_order_and_deduplicated():
    assert pattern_variables("MATCH (n:`P`)-[r]->(m)-[r]->(n)") == ["n", "r", "m"]


# ---------------------------------------------------------------------------
# result mapping
# ---------------------------------------------------------------------------

NODE = {
    "kind": "node",
    "identifier": "n1",
    "labels": ["Person"],
    "properties": {"id": 1, "name": "Ada", "unused": None},
}
EDGE = {
    "kind": "edge",
    "identifier": "e1",
    "labels": ["KNOWS"],
    "properties": {"since": 2020},
    "source_node_identifier": "n1",
    "destination_node_identifier": "n2",
}


def test_graph_rows_become_nodes_and_relationships():
    result = parse_graph_rows([Row({"n": NODE, "r": EDGE})])
    assert result.type == "GRAPH"
    (node,) = result.data.nodes
    (edge,) = result.data.relationships
    assert (node.id, node.labels) == ("n1", ["Person"])
    assert (edge.id, edge.type, edge.startNodeId, edge.endNodeId) == ("e1", "KNOWS", "n1", "n2")


def test_null_properties_are_dropped():
    # A property graph is built over columns, so an element carries every column of
    # its table; the ones it does not use arrive as null.
    (node,) = parse_graph_rows([Row({"n": NODE})]).data.nodes
    assert node.properties == {"id": 1, "name": "Ada"}


def test_the_same_element_across_rows_is_kept_once():
    result = parse_graph_rows([Row({"n": NODE}), Row({"n": NODE})])
    assert len(result.data.nodes) == 1


def test_a_cell_holding_json_text_or_a_path_array_is_read_too():
    import json

    assert as_graph_elements(json.dumps(NODE)) == [NODE]
    assert as_graph_elements([NODE, EDGE]) == [NODE, EDGE]
    assert as_graph_elements({"kind": "not-an-element"}) == []
    assert as_graph_elements(None) == []


def test_table_rows_are_a_2d_array_with_a_header():
    result = parse_table_rows(["a", "b"], [Row({"a": 1, "b": "x"}), Row({"a": 2, "b": "y"})])
    assert result.type == "TABLE"
    assert result.data == [["a", "b"], [1, "x"], [2, "y"]]


@pytest.mark.parametrize(
    "value,expected",
    [
        (decimal.Decimal("1.5"), 1.5),
        (datetime.date(2026, 8, 27), "2026-08-27"),
        (datetime.time(9, 30), "09:30:00"),
        (b"hi", "aGk="),
        ({"a": decimal.Decimal("2")}, {"a": 2.0}),
        ([decimal.Decimal("3")], [3.0]),
        (None, None),
    ],
)
def test_cells_the_json_encoder_cannot_carry_are_converted(value, expected):
    assert json_safe(value) == expected


# ---------------------------------------------------------------------------
# property graph metadata
# ---------------------------------------------------------------------------

METADATA = {
    "nodeTables": [
        {
            "name": "person_table",
            "keyColumns": ["id"],
            "labelAndProperties": [
                {
                    "label": "Person",
                    "properties": [
                        {"name": "id", "dataType": {"typeKind": "INT64"}},
                        {"name": "name", "dataType": {"typeKind": "STRING"}},
                    ],
                }
            ],
        }
    ],
    "edgeTables": [
        {
            "name": "knows_table",
            "keyColumns": ["src", "dst"],
            "labelAndProperties": [
                {"label": "KNOWS", "properties": [{"name": "since", "dataType": {"typeKind": "INT64"}}]}
            ],
            "sourceNodeReference": {"nodeTable": "person_table"},
            "destinationNodeReference": {"nodeTable": "person_table"},
        }
    ],
}


def test_metadata_becomes_categories_and_relationships():
    schema = map_graph_metadata(METADATA)
    (category,) = schema.categories
    assert category.name == "Person"
    assert category.props == ["id", "name"]
    assert category.keys == ["id"]
    assert category.keysTypes == {"id": "INT64"}

    (relationship,) = schema.relationships
    assert relationship.name == "KNOWS"
    # The endpoints name the *table*, so they are resolved through the label map.
    assert (relationship.startCategory, relationship.endCategory) == ("Person", "Person")


def test_a_table_without_a_declared_label_falls_back_to_its_table_name():
    schema = map_graph_metadata({"nodeTables": [{"name": "orphan", "labelAndProperties": []}]})
    assert [category.name for category in schema.categories] == ["orphan"]


def test_empty_metadata_is_an_empty_schema_rather_than_an_error():
    schema = map_graph_metadata({})
    assert schema.categories == [] and schema.relationships == []
