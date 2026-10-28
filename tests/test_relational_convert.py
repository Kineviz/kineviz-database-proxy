"""
Rows and a manifest back into a graph — including the rows that cannot become one.

The counts are the point of half of these tests. A relationship that cannot be placed and
a database that never had it look identical on a canvas, so the only honest version of a
smaller graph is a smaller graph that says what it left out.
"""

from __future__ import annotations

import datetime as _datetime
import decimal
import uuid
from typing import Any, Dict, List

from kineviz_database_proxy.drivers.relational.compile import EdgeEntry, Manifest, VertexEntry
from kineviz_database_proxy.drivers.relational.convert import (
    MAX_CELL_CHARS,
    GraphConversion,
    convert_rows,
    json_safe,
    rows_to_graph,
    rows_to_table,
)

PERSON = VertexEntry(
    var="p",
    label="Person",
    alias="v0",
    key_column="__gx_v0_k0",
    key_name="person_id",
    key_type="bigint",
    properties={"__gx_v0_p0": "name", "__gx_v0_p1": "hired"},
)
ORGANIZATION = VertexEntry(
    var="o",
    label="Organization",
    alias="v1",
    key_column="__gx_v1_k0",
    key_name="organization_id",
    key_type="bigint",
    properties={"__gx_v1_p0": "name"},
)
EMPLOYMENT = EdgeEntry(
    var="r",
    type="EMPLOYMENT",
    alias="e0",
    key_column="__gx_e0_k0",
    key_name="employment_id",
    key_type="bigint",
    start_var="p",
    end_var="o",
    start_label="Person",
    end_label="Organization",
    properties={"__gx_e0_p0": "since"},
)
GRAPH = Manifest(vertices=(PERSON, ORGANIZATION), edges=(EMPLOYMENT,))


def row(person=1001, organization=2001, employment=11, name="Avery", since=None) -> Dict[str, Any]:
    return {
        "__gx_v0_k0": person,
        "__gx_v0_p0": name,
        "__gx_v0_p1": _datetime.date(1984, 3, 1),
        "__gx_v1_k0": organization,
        "__gx_v1_p0": "Acme",
        "__gx_e0_k0": employment,
        "__gx_e0_p0": since if since is not None else _datetime.datetime(2024, 1, 31, 8, 30),
    }


def rows(*items: Dict[str, Any], manifest: Manifest = GRAPH) -> GraphConversion:
    return convert_rows(list(items), manifest)


# -- what a row becomes --


def test_one_row_of_a_two_node_pattern_becomes_two_nodes_and_the_line_between_them():
    result = rows(row())
    nodes, edges = result.graph.nodes, result.graph.relationships
    assert [node.id for node in nodes] == ["Person:1001", "Organization:2001"]
    assert [node.labels for node in nodes] == [["Person"], ["Organization"]]
    assert len(edges) == 1
    assert edges[0].id == "EMPLOYMENT:11" and edges[0].type == "EMPLOYMENT"
    assert (edges[0].startNodeId, edges[0].endNodeId) == ("Person:1001", "Organization:2001")
    assert result.summary == {}


def test_the_key_is_carried_as_a_property_as_well_as_inside_the_id():
    result = rows(row())
    assert result.graph.nodes[0].properties["person_id"] == 1001
    assert result.graph.relationships[0].properties["employment_id"] == 11


def test_a_temporal_cell_goes_out_in_iso_8601_and_a_number_stays_a_number():
    result = rows(row())
    properties = result.graph.nodes[0].properties
    assert properties["hired"] == "1984-03-01"
    assert properties["name"] == "Avery"
    assert result.graph.relationships[0].properties["since"] == "2024-01-31T08:30:00"


def test_the_same_node_in_two_rows_is_one_node():
    """
    A fan-out re-reads a seed from every chunk that touches it.

    Two entries for ``Person:1001`` on a canvas is a duplicate the person has to notice and
    explain to themselves, and nothing in the response says which one is right.
    """
    result = rows(row(employment=11), row(employment=12))
    assert len(result.graph.nodes) == 2
    assert [edge.id for edge in result.graph.relationships] == ["EMPLOYMENT:11", "EMPLOYMENT:12"]


def test_two_rows_holding_the_same_edge_twice_answer_one_edge():
    result = rows(row(), row())
    assert len(result.graph.relationships) == 1


# -- what a row cannot become --


def test_a_null_key_drops_the_element_rather_than_naming_it_the_empty_string():
    """``Person:`` would be one id shared by every keyless row in the table."""
    result = rows(row(person=None))
    assert [node.id for node in result.graph.nodes] == ["Organization:2001"]
    assert result.dropped_nodes == 1
    # and the edge that hung off it is said out loud too, not just implied by its absence
    assert result.summary == {"droppedNodes": "1", "unplacedRelationships": "1"}


def test_an_edge_whose_other_end_was_dropped_is_counted_not_silently_missing():
    result = rows(row(organization=None))
    assert len(result.graph.nodes) == 1
    assert result.graph.relationships == []
    assert result.summary == {"droppedNodes": "1", "unplacedRelationships": "1"}


def test_a_row_with_no_edge_information_is_reported_once_per_edge():
    manifest = Manifest(vertices=(PERSON, ORGANIZATION), edges=(EMPLOYMENT,))
    result = convert_rows([row(person=None, organization=None)], manifest)
    assert result.dropped_nodes == 2
    assert result.unplaced_relationships == 1


# -- an edge with no key of its own --

KEYLESS = Manifest(
    vertices=(PERSON, ORGANIZATION),
    edges=(
        EdgeEntry(
            var="r",
            type="KNOWS",
            alias="e0",
            key_column=None,
            key_name=None,
            key_type=None,
            start_var="p",
            end_var="o",
            start_label="Person",
            end_label="Organization",
            properties={},
        ),
    ),
)


def test_a_keyless_edge_is_named_by_its_two_endpoints():
    result = rows(row(), manifest=KEYLESS)
    assert [edge.id for edge in result.graph.relationships] == ["KNOWS:1001|2001"]


def test_a_keyless_edge_keeps_the_same_id_whichever_way_the_pattern_ran():
    """
    An undirected expand fans out per orientation, and a line that flips id per orientation
    is the same line drawn twice.
    """
    forward = rows(row(), manifest=KEYLESS).graph.relationships[0].id
    reversed_endpoints = rows(row(person=2001, organization=1001), manifest=KEYLESS).graph.relationships[0].id
    assert forward == reversed_endpoints


def test_a_keyless_edge_is_still_placed_when_its_own_key_column_is_null():
    """It never had a key column: the two ends are the identity, and both are here."""
    assert [edge.id for edge in rows(row(employment=None), manifest=KEYLESS).graph.relationships] == ["KNOWS:1001|2001"]


def test_a_keyless_edge_with_no_ends_to_place_it_between_is_counted():
    result = rows(row(person=None, organization=None), manifest=KEYLESS)
    assert result.graph.relationships == []
    assert result.summary == {"droppedNodes": "2", "unplacedRelationships": "1"}


# -- cells --


def test_a_decimal_becomes_a_number_only_when_reading_it_back_gives_the_same_number():
    """
    More digits than a double has stay text; a trailing zero is not a lost digit.

    The failure this guards is the silent one: a ledger total that comes back 1e-19 away
    from the value the column holds, with nothing in the response saying which is which.
    """
    assert json_safe(decimal.Decimal("10.5")) == 10.5
    assert json_safe(decimal.Decimal("1000.50")) == 1000.5
    assert json_safe(decimal.Decimal("1.0000000000000000001")) == "1.0000000000000000001"
    assert json_safe(decimal.Decimal("12345678901234567890123")) == "12345678901234567890123"
    # a finite number with no finite double: text, because 0 and inf are both wrong answers
    assert json_safe(decimal.Decimal("1e999")) == "1E+999"
    assert json_safe(decimal.Decimal("NaN")) is None


def test_bytes_go_out_as_base64_and_a_non_finite_float_as_null():
    """NaN is not JSON, and a client that receives the word cannot parse the response."""
    assert json_safe(b"\x01\x02\x03") == "AQID"
    assert json_safe(float("nan")) is None
    assert json_safe(float("inf")) is None
    assert json_safe(float("-inf")) is None
    assert json_safe(1.5) == 1.5


def test_a_uuid_is_text_and_a_duration_is_seconds():
    value = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert json_safe(value) == "12345678-1234-5678-1234-567812345678"
    assert json_safe(_datetime.timedelta(minutes=90)) == 5400.0
    assert json_safe(_datetime.time(8, 30)) == "08:30:00"


def test_a_value_with_no_json_shape_is_truncated_text_rather_than_a_blob():
    class _Cell:
        def __str__(self) -> str:
            return "x" * (MAX_CELL_CHARS + 900)

    text = json_safe(_Cell())
    assert len(text) == MAX_CELL_CHARS + 3 and text.endswith("...")


def test_a_container_is_walked_rather_than_stringified():
    assert json_safe({"a": decimal.Decimal("1.5"), "b": [b"\x00", None]}) == {"a": 1.5, "b": ["AA==", None]}
    assert json_safe([1, 2]) == [1, 2]


def test_booleans_and_integers_are_themselves():
    assert json_safe(True) is True and json_safe(False) is False
    assert json_safe(7) == 7 and json_safe("7") == "7"
    assert json_safe(None) is None


def test_a_table_answer_is_a_two_dimensional_array_with_the_header_first():
    columns = ("o.name", "count_all")
    table = rows_to_table([{ "o.name": "Acme", "count_all": 3 }, {"o.name": "Globex", "count_all": 1}], columns)
    assert table == [["o.name", "count_all"], ["Acme", 3], ["Globex", 1]]


def test_a_missing_column_is_a_null_cell_and_not_a_short_row():
    table: List[List[Any]] = rows_to_table([{"o.name": "Acme"}], ("o.name", "count_all"))
    assert table[1] == ["Acme", None]


def test_the_graph_alone_is_available_for_a_caller_that_has_no_use_for_the_counts():
    graph = rows_to_graph([row()], GRAPH)
    assert len(graph.nodes) == 2 and len(graph.relationships) == 1
