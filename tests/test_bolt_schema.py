"""
The bolt family's schema probes.

Both loaders are driven through a scripted ``run``, so what is asserted is the
sequence of statements each backend sends and how it folds the answers — including
the two routes Memgraph has to try, and the fact that a failing probe is an answer
rather than a fault.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

import pytest

from kineviz_database_proxy.drivers.bolt_schema import (
    EDGE_INVENTORY_STATEMENT,
    NODE_INVENTORY_STATEMENT,
    NODE_TYPE_PROPERTIES_STATEMENT,
    REL_TYPE_PROPERTIES_STATEMENT,
    SHOW_SCHEMA_INFO_STATEMENT,
    TRIPLE_FANOUT_LIMIT,
    VISUALIZATION_STATEMENT,
    Inventory,
    Triple,
    build_label_props_statement,
    build_relationship_props_statement,
    build_schema,
    load_memgraph_schema,
    load_neo4j_schema,
    parse_inventory,
    parse_node_type_properties,
    parse_props_table,
    parse_rel_type_properties,
    parse_schema_info,
    parse_visualization,
)
from kineviz_database_proxy.models.project import GraphData, Node, QueryData, RelationshipData


class ScriptedRun:
    """Answers a statement from a script, remembering what it was asked."""

    def __init__(self, answers: Dict[str, Optional[QueryData]]):
        self.answers = answers
        self.asked: List[str] = []

    async def __call__(self, statement: str) -> Optional[QueryData]:
        self.asked.append(statement)
        for prefix, answer in self.answers.items():
            if statement.startswith(prefix) or prefix in statement:
                return answer
        return None


def table(header, *rows) -> QueryData:
    return QueryData(type="TABLE", data=[list(header), *[list(row) for row in rows]])


def visualization() -> QueryData:
    return QueryData(
        type="GRAPH",
        data=GraphData(
            nodes=[
                Node(id="1", labels=["Person"], properties={}),
                Node(id="2", labels=["Company"], properties={}),
                Node(id="3", labels=[], properties={}),
            ],
            relationships=[
                RelationshipData(id="9", type="WORKS_AT", startNodeId="1", endNodeId="2", properties={}),
                # An edge with an endpoint that has no label is dropped.
                RelationshipData(id="10", type="ORPHAN", startNodeId="1", endNodeId="3", properties={}),
            ],
        ),
    )


# ---------------------------------------------------------------------------
# parse_visualization
# ---------------------------------------------------------------------------


def test_the_visualization_graph_becomes_labels_and_triples():
    inventory = parse_visualization(visualization())
    assert inventory.labels == ["Person", "Company"]
    assert inventory.triples == [Triple("WORKS_AT", "Person", "Company")]


def test_a_missing_or_tabular_visualization_is_an_empty_inventory():
    assert parse_visualization(None).labels == []
    assert parse_visualization(table(["a"], [1])).triples == []


# ---------------------------------------------------------------------------
# property sampling
# ---------------------------------------------------------------------------


def test_the_label_props_statement_samples_one_record_per_label():
    statement = build_label_props_statement(["Person", "Co mpany"])
    assert statement.count(" UNION ") == 1
    assert "MATCH (n:`Person`) RETURN \"Person\" as label" in statement
    # A label with a space is backticked in the pattern and quoted in the projection.
    assert "MATCH (n:`Co mpany`)" in statement
    assert statement.count("LIMIT 1") == 2


def test_the_relationship_statement_collapses_to_one_branch_per_type_when_dense():
    triples = [Triple("R", f"A{i}", f"B{i}") for i in range(TRIPLE_FANOUT_LIMIT + 1)]
    statement = build_relationship_props_statement(triples)
    # Past the fan-out limit only one branch per relationship name survives, which
    # is what keeps a dense schema from producing a UNION with hundreds of branches.
    assert " UNION " not in statement
    assert statement.count("MATCH") == 1


def test_props_are_read_by_column_name_and_typed_from_the_sampled_values():
    result = table(
        ["props", "label", "values"],
        [["name", "age"], "Person", {"name": "Ada", "age": 36}],
    )
    parsed = parse_props_table(result)
    assert parsed["Person"].props == ["age", "name"]
    assert parsed["Person"].props_types == {"name": "STRING", "age": "INT64"}


def test_a_property_that_was_null_in_one_sample_takes_the_type_another_saw():
    result = table(
        ["relationship", "props", "values"],
        ["R", ["since"], {"since": None}],
        ["R", ["since", "note"], {"since": 2020, "note": "x"}],
    )
    parsed = parse_props_table(result)
    assert parsed["R"].props == ["note", "since"]
    assert parsed["R"].props_types == {"since": "INT64", "note": "STRING"}


def test_an_absent_props_table_yields_nothing_rather_than_raising():
    assert parse_props_table(None) == {}
    assert parse_props_table(table(["props", "values"])) == {}


# ---------------------------------------------------------------------------
# build_schema
# ---------------------------------------------------------------------------


def test_one_relationship_entry_per_name_even_when_several_triples_share_it():
    inventory = Inventory(
        labels=["Person"],
        triples=[Triple("R", "Person", "Person"), Triple("R", "Person", "Company")],
    )
    schema = build_schema(inventory, {}, {})
    assert [relationship.name for relationship in schema.relationships] == ["R"]
    # The contract carries one pair per relationship, so the first triple wins.
    assert schema.relationships[0].endCategory == "Person"


# ---------------------------------------------------------------------------
# Neo4j loader
# ---------------------------------------------------------------------------


#: The columns neo4j 5.26 actually returns, captured from a live server. The node
#: procedure is keyed by a label *set*; the relationship one quotes its token.
NODE_TYPE_PROPERTIES_HEADER = ["nodeType", "nodeLabels", "propertyName", "propertyTypes", "mandatory"]
REL_TYPE_PROPERTIES_HEADER = ["relType", "propertyName", "propertyTypes", "mandatory"]


async def test_neo4j_reads_the_property_types_from_the_schema_procedures():
    run = ScriptedRun(
        {
            VISUALIZATION_STATEMENT: visualization(),
            NODE_TYPE_PROPERTIES_STATEMENT: table(
                NODE_TYPE_PROPERTIES_HEADER,
                [":`Person`", ["Person"], "name", ["String"], True],
                [":`Person`", ["Person"], "age", ["Long"], False],
                [":`Company`", ["Company"], "founded", ["Long"], True],
            ),
            REL_TYPE_PROPERTIES_STATEMENT: table(
                REL_TYPE_PROPERTIES_HEADER,
                [":`WORKS_AT`", "since", ["Long"], False],
                [":`WORKS_AT`", "role", ["String"], False],
            ),
        }
    )
    schema = await load_neo4j_schema(run)

    assert run.asked[0] == VISUALIZATION_STATEMENT
    # The sampling statements cost a scan per label; they must not be sent as well.
    assert not any("MATCH (n:" in statement for statement in run.asked)

    assert schema.categories[0].propsTypes == {"name": "STRING", "age": "INT64"}
    assert schema.categories[1].propsTypes == {"founded": "INT64"}
    assert schema.relationships[0].props == ["role", "since"]
    assert schema.relationships[0].propsTypes == {"since": "INT64", "role": "STRING"}


def test_a_label_set_gives_its_properties_to_every_label_in_it():
    # A node labelled both is reported once, under the pair.
    parsed = parse_node_type_properties(
        table(
            NODE_TYPE_PROPERTIES_HEADER,
            [":`Person`:`Employee`", ["Person", "Employee"], "badge", ["String"], True],
        )
    )
    assert parsed["Person"].props == ["badge"]
    assert parsed["Employee"].props == ["badge"]


def test_a_label_with_no_properties_is_still_reported():
    # The procedure emits one row with a null propertyName for such a label.
    parsed = parse_node_type_properties(
        table(NODE_TYPE_PROPERTIES_HEADER, [":`Tag`", ["Tag"], None, [], False])
    )
    assert parsed["Tag"].props == [] and parsed["Tag"].props_types == {}


def test_the_relationship_token_is_unquoted():
    # `:`KNOWS`` is how the procedure spells it; the schema says `KNOWS`.
    parsed = parse_rel_type_properties(
        table(REL_TYPE_PROPERTIES_HEADER, [":`KNOWS`", "since", ["Long"], False])
    )
    assert list(parsed) == ["KNOWS"]


def test_a_list_valued_property_is_reported_as_a_list():
    # The procedure names it by element type: a list of strings is a "StringArray".
    parsed = parse_node_type_properties(
        table(NODE_TYPE_PROPERTIES_HEADER, [":`Person`", ["Person"], "tags", ["StringArray"], False])
    )
    assert parsed["Person"].props_types == {"tags": "LIST"}


def test_a_property_written_two_ways_takes_the_first_type_reported():
    parsed = parse_node_type_properties(
        table(NODE_TYPE_PROPERTIES_HEADER, [":`Person`", ["Person"], "id", ["Long", "String"], False])
    )
    assert parsed["Person"].props_types == {"id": "INT64"}


async def test_a_server_without_the_procedures_falls_back_to_sampling():
    run = ScriptedRun(
        {
            VISUALIZATION_STATEMENT: visualization(),
            "MATCH (n:`Person`)": table(
                ["label", "props", "values"], ["Person", ["name"], {"name": "Ada"}]
            ),
            "-[r:`WORKS_AT`]->": table(
                ["relationship", "props", "values"], ["WORKS_AT", ["since"], {"since": 2020}]
            ),
        }
    )
    schema = await load_neo4j_schema(run)

    assert run.asked[0] == VISUALIZATION_STATEMENT
    assert [category.name for category in schema.categories] == ["Person", "Company"]
    assert schema.categories[0].propsTypes == {"name": "STRING"}
    assert schema.relationships[0].propsTypes == {"since": "INT64"}


async def test_an_empty_visualization_stops_before_the_sampling_statements():
    run = ScriptedRun({VISUALIZATION_STATEMENT: None})
    schema = await load_neo4j_schema(run)
    assert schema.categories == [] and schema.relationships == []
    assert run.asked == [VISUALIZATION_STATEMENT]


async def test_a_store_that_refuses_the_property_sample_still_yields_categories():
    run = ScriptedRun({VISUALIZATION_STATEMENT: visualization()})
    schema = await load_neo4j_schema(run)
    assert [category.name for category in schema.categories] == ["Person", "Company"]
    assert schema.categories[0].props == []


# ---------------------------------------------------------------------------
# Memgraph loader
# ---------------------------------------------------------------------------

SCHEMA_INFO = {
    "nodes": [
        {
            "labels": ["Person"],
            "properties": [
                {"key": "name", "types": [{"type": "String", "count": 10}]},
                {"key": "age", "types": [{"type": "Integer", "count": 8}, {"type": "String", "count": 1}]},
            ],
        }
    ],
    "edges": [
        {
            "type": "KNOWS",
            "start_node_labels": ["Person"],
            "end_node_labels": ["Person"],
            "properties": [{"key": "since", "types": [{"type": "Integer", "count": 3}]}],
        }
    ],
}


def test_show_schema_info_is_parsed_from_its_json_document():
    schema = parse_schema_info(table(["schema"], [json.dumps(SCHEMA_INFO)]))
    (category,) = schema.categories
    assert category.name == "Person"
    # Memgraph's spellings become GraphXR's, and the most-seen type wins.
    assert category.propsTypes == {"name": "STRING", "age": "INT64"}
    (relationship,) = schema.relationships
    assert relationship.propsTypes == {"since": "INT64"}


def test_an_unusable_schema_info_answer_means_fall_through_not_empty_schema():
    # A server started without --schema-info-enabled throws; the loader must try
    # the next route rather than report a graph with no categories.
    assert parse_schema_info(None) is None
    assert parse_schema_info(table(["x"], ["not json"])) is None
    assert parse_schema_info(table(["x"], [json.dumps({"nodes": [], "edges": []})])) is None


async def test_memgraph_prefers_show_schema_info_and_asks_nothing_else():
    run = ScriptedRun({SHOW_SCHEMA_INFO_STATEMENT: table(["schema"], [json.dumps(SCHEMA_INFO)])})
    schema = await load_memgraph_schema(run)
    assert run.asked == [SHOW_SCHEMA_INFO_STATEMENT]
    assert [category.name for category in schema.categories] == ["Person"]


async def test_memgraph_falls_back_to_the_inventory_probe():
    run = ScriptedRun(
        {
            SHOW_SCHEMA_INFO_STATEMENT: None,
            NODE_INVENTORY_STATEMENT: table(["labels"], [["Person", "Employee"]]),
            EDGE_INVENTORY_STATEMENT: table(
                ["relName", "startL", "endL"], ["KNOWS", ["Person"], ["Person"]]
            ),
            "MATCH (n:`Person`)": table(
                ["label", "props", "values"], ["Person", ["name"], {"name": "Ada"}]
            ),
        }
    )
    schema = await load_memgraph_schema(run)

    assert run.asked[:3] == [
        SHOW_SCHEMA_INFO_STATEMENT,
        NODE_INVENTORY_STATEMENT,
        EDGE_INVENTORY_STATEMENT,
    ]
    # Neo4j's own probe is never used: db.schema.visualization() does not exist here.
    assert VISUALIZATION_STATEMENT not in run.asked
    # A label set is exploded, so every label in it becomes a category.
    assert [category.name for category in schema.categories] == ["Person", "Employee"]


def test_an_edge_between_two_label_sets_becomes_one_triple_per_pair():
    inventory = parse_inventory(
        table(["labels"], [["A"]], [["B", "C"]]),
        table(["relName", "startL", "endL"], ["R", ["A"], ["B", "C"]]),
    )
    assert inventory.labels == ["A", "B", "C"]
    assert inventory.triples == [Triple("R", "A", "B"), Triple("R", "A", "C")]


@pytest.mark.parametrize("value", [None, [], [""]])
def test_a_node_with_no_labels_contributes_nothing(value):
    inventory = parse_inventory(table(["labels"], [value]), None)
    assert inventory.labels == []
