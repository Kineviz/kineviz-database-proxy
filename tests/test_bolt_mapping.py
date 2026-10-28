"""
Bolt records -> ``QueryData``.

Real driver objects are built rather than stubbed: the mapping's trickiest rule
is that the driver names every relationship class after its *type*, so a `KNOWS`
edge is an instance of a class called `KNOWS`. A hand-rolled stub would have
hidden that.
"""

from __future__ import annotations

import pytest

from neo4j.graph import Graph, Node as BoltNode, Path as BoltPath
from neo4j.spatial import WGS84Point
from neo4j.time import Date, DateTime, Duration

from kineviz_database_proxy.drivers.bolt_mapping import (
    entity_id,
    graph_entities,
    infer_value_type,
    normalize_type_name,
    records_hold_graph,
    records_to_graph,
    records_to_table,
    table_rows,
    to_json_value,
)
from kineviz_database_proxy.models.project import QueryData


class Record(dict):
    """The two methods the mapping asks of a record."""

    def values(self):
        return list(super().values())

    def keys(self):
        return list(super().keys())


@pytest.fixture
def graph():
    store = Graph()
    ada = BoltNode(store, "4:x:1", 1, ("Person",), {"name": "Ada", "age": 36})
    bob = BoltNode(store, "4:x:2", 2, ("Person", "Employee"), {"name": "Bob"})
    knows = store.relationship_type("KNOWS")(store, "5:x:9", 9, {"since": 2020})
    knows._start_node = ada
    knows._end_node = bob
    return store, ada, bob, knows


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def test_a_nodes_id_is_its_bolt_internal_id(graph):
    _, ada, _, knows = graph
    # ID(n), not elementId(n): the dialect's predicate is ID(), so the id handed to
    # the client has to be the one that predicate can match again.
    assert entity_id(ada) == "1"
    assert entity_id(knows) == "9"


def test_a_server_that_withholds_the_legacy_id_falls_back_to_the_element_id():
    store = Graph()
    node = BoltNode(store, "4:x:7", None, ("Person",), {})
    assert entity_id(node) == "4:x:7"


# ---------------------------------------------------------------------------
# graph detection and extraction
# ---------------------------------------------------------------------------


def test_a_result_holding_entities_is_a_graph(graph):
    _, ada, bob, knows = graph
    assert records_hold_graph([Record(n=ada, r=knows, m=bob)])


def test_a_result_of_scalars_is_not_a_graph():
    assert records_hold_graph([Record(name="Ada", count=3)]) is False
    assert records_hold_graph([]) is False


def test_entities_are_found_inside_lists_and_maps(graph):
    _, ada, bob, _ = graph
    assert list(graph_entities([ada, [bob]])) == [ada, bob]
    assert list(graph_entities({"a": ada})) == [ada]


def test_a_path_contributes_its_nodes_and_relationships(graph):
    store, ada, bob, knows = graph
    path = BoltPath(ada, knows)
    assert records_hold_graph([Record(p=path)])
    result = records_to_graph([Record(p=path)])
    assert {node.id for node in result.data.nodes} == {"1", "2"}
    assert [edge.id for edge in result.data.relationships] == ["9"]


def test_records_become_a_de_duplicated_graph(graph):
    _, ada, bob, knows = graph
    result = records_to_graph([Record(n=ada, r=knows, m=bob), Record(n=ada, r=knows, m=bob)])
    assert result.type == "GRAPH"
    assert [node.id for node in result.data.nodes] == ["1", "2"]
    (edge,) = result.data.relationships
    assert (edge.type, edge.startNodeId, edge.endNodeId) == ("KNOWS", "1", "2")
    assert edge.properties == {"since": 2020}


def test_labels_are_sorted_so_a_frozenset_does_not_reorder_between_calls(graph):
    # The driver hands labels over as a frozenset, so the server's ordering is
    # already gone; sorting at least makes the answer the same on every call.
    _, _, bob, _ = graph
    (mapped,) = records_to_graph([Record(n=bob)]).data.nodes
    assert mapped.labels == ["Employee", "Person"]


# ---------------------------------------------------------------------------
# table mapping and value conversion
# ---------------------------------------------------------------------------


def test_table_records_become_a_2d_array_with_a_header():
    result = records_to_table([Record(a=1, b="x")], ["a", "b"])
    assert result.data == [["a", "b"], [1, "x"]]


def test_an_empty_result_still_reports_its_columns():
    assert records_to_table([], ["a", "b"]).data == [["a", "b"]]


def test_an_entity_in_a_table_cell_becomes_its_properties(graph):
    _, ada, _, _ = graph
    assert to_json_value(ada) == {"name": "Ada", "age": 36}


@pytest.mark.parametrize(
    "value,expected",
    [
        (Date(2026, 8, 27), "2026-08-27"),
        (DateTime(2026, 8, 27, 9, 30, 0), "2026-08-27T09:30:00.000000000"),
        (Duration(months=1), "P1M"),
        (b"hi", "aGk="),
        ({"a": 1}, {"a": 1}),
        ([1, "x"], [1, "x"]),
        (None, None),
    ],
)
def test_driver_types_are_converted_for_the_json_response(value, expected):
    assert to_json_value(value) == expected


def test_a_spatial_value_keeps_the_spelling_the_browser_has_always_shown():
    assert to_json_value(WGS84Point((1.5, 2.5))) == "point({srid:4326, x:1.5, y:2.5})"


# ---------------------------------------------------------------------------
# type inference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, "BOOLEAN"),
        (3, "INT64"),
        (3.5, "DOUBLE"),
        ("x", "STRING"),
        ([1], "LIST"),
        ({"a": 1}, "MAP"),
        (Date(2026, 1, 1), "DATE"),
        (Duration(days=1), "DURATION"),
        (WGS84Point((1.0, 2.0)), "POINT"),
        (None, ""),
    ],
)
def test_a_property_is_typed_from_the_value_the_sample_carried(value, expected):
    assert infer_value_type(value) == expected


def test_a_bool_is_not_reported_as_an_integer():
    # bool is a subclass of int in Python; checked before int for that reason.
    assert infer_value_type(False) == "BOOLEAN"


@pytest.mark.parametrize(
    "name,expected",
    [("Integer", "INT64"), ("INTEGER", "INT64"), ("Float", "DOUBLE"), ("String", "STRING")],
)
def test_backend_spellings_are_translated_to_graphxrs(name, expected):
    assert normalize_type_name(name) == expected


def test_null_is_the_absence_of_a_type_rather_than_a_type():
    assert normalize_type_name("NULL") == ""
    assert normalize_type_name(None) == ""


# ---------------------------------------------------------------------------
# table_rows
# ---------------------------------------------------------------------------


def test_a_table_is_read_by_column_name_not_by_position():
    # Memgraph does not preserve the RETURN order across a UNION, so the header is
    # the only reliable guide to which cell is which.
    result = QueryData(type="TABLE", data=[["props", "label"], [["a"], "Person"]])
    assert table_rows(result) == [{"props": ["a"], "label": "Person"}]


def test_a_header_only_or_non_table_result_yields_no_rows():
    assert table_rows(QueryData(type="TABLE", data=[["a"]])) == []
    assert table_rows(QueryData(type="GRAPH", data=None)) == []
    assert table_rows(None) == []
