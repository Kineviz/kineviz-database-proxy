# -*- coding: utf-8 -*-
"""
LatticeDB worker rows -> ``QueryData``.

The rows here are what latticedb 0.14.0 actually answered, run through the
worker's flattening, so the shapes are recorded rather than imagined.

What makes this its own module is where the graph lives. Every other backend
sends entities as values; LatticeDB has no node or relationship value type at
all, so ``RETURN n`` answers a bare id and the projection is the only thing that
says what a row holds.
"""

from __future__ import annotations

from kineviz_database_proxy.drivers.embedded.lattice_mapping import (
    columns_hold_graph,
    entity_columns,
    result_to_query_data,
    rows_to_graph,
)

#: What the dialect projects for a one-hop expand, verbatim.
EXPAND_COLUMNS = [
    "id(n)", "labels(n)", "properties(n)",
    "id(r)", "type(r)", "properties(r)", "r_src", "r_dst",
    "id(m)", "labels(m)", "properties(m)",
]

EXPAND_ROWS = [
    [1, ["Person"], {"name": "Alice"}, 1, "KNOWS", {}, 1, 2, 2, ["Person"], {"name": "Bob"}],
    [1, ["Person"], {"name": "Alice"}, 2, "LIVES_IN", {}, 1, 4, 4, ["City"], {"name": "Portland"}],
]


def test_the_projection_is_what_says_a_row_holds_a_graph():
    assert columns_hold_graph(EXPAND_COLUMNS)
    # Values alone can never say it here, so a result with no projection is a table.
    assert not columns_hold_graph(["a", "b"])


def test_an_id_on_its_own_is_a_number_not_a_node():
    """``labels`` is what makes a node and ``type`` is what makes a relationship."""
    assert not columns_hold_graph(["id(n)"])
    assert columns_hold_graph(["id(n)", "labels(n)"])
    assert columns_hold_graph(["id(r)", "type(r)"])


def test_nodes_and_relationships_are_told_apart_by_labels_versus_type():
    kinds = {entity.variable: entity.is_node for entity in entity_columns(EXPAND_COLUMNS)}
    assert kinds == {"n": True, "r": False, "m": True}


def test_an_expand_result_becomes_the_graph_it_describes():
    data, dropped = rows_to_graph(EXPAND_COLUMNS, EXPAND_ROWS)

    assert dropped == 0
    assert [(node.id, node.labels) for node in data.data.nodes] == [
        ("1", ["Person"]),
        ("2", ["Person"]),
        ("4", ["City"]),
    ]
    assert [(r.id, r.type, r.startNodeId, r.endNodeId) for r in data.data.relationships] == [
        ("1", "KNOWS", "1", "2"),
        ("2", "LIVES_IN", "1", "4"),
    ]


def test_a_node_id_is_the_integer_the_engine_gave_because_it_matches_again():
    """
    No ``<Label>:<key>`` construction, unlike Kuzu: ``WHERE id(n) IN [1,2]`` is
    accepted here, so the id the client gets back is the one it can send.
    """
    data, _ = rows_to_graph(EXPAND_COLUMNS, EXPAND_ROWS)
    assert {node.id for node in data.data.nodes} == {"1", "2", "4"}


def test_an_edge_id_may_repeat_a_node_id_without_confusing_anything():
    # Node 1 and edge 1 both exist: one counter, two collections.
    data, _ = rows_to_graph(EXPAND_COLUMNS, EXPAND_ROWS)
    assert data.data.nodes[0].id == "1"
    assert data.data.relationships[0].id == "1"


def test_the_endpoint_columns_are_what_place_an_edge():
    """Reversing the arrow reverses the aliases, and the edge follows."""
    reversed_rows = [
        [2, ["Person"], {"name": "Bob"}, 1, "KNOWS", {}, 1, 2, 1, ["Person"], {"name": "Alice"}],
    ]
    data, _ = rows_to_graph(EXPAND_COLUMNS, reversed_rows)
    edge = data.data.relationships[0]
    assert (edge.startNodeId, edge.endNodeId) == ("1", "2")


def test_an_edge_whose_endpoints_were_not_projected_is_counted_not_invented():
    result = {
        "columns": ["id(r)", "type(r)", "properties(r)"],
        "rows": [[1, "KNOWS", {}]],
    }
    data = result_to_query_data(result)

    assert data.data.relationships == []
    assert data.summary["droppedRelationships"] == "1"


def test_a_node_with_no_labels_is_a_node():
    """Nothing in the store requires one, so an empty list is a real answer."""
    data, _ = rows_to_graph(
        ["id(n)", "labels(n)", "properties(n)"], [[9, [], {"orphan": True}]]
    )
    assert data.data.nodes[0].labels == []
    assert data.data.nodes[0].properties == {"orphan": True}


def test_a_multi_label_node_keeps_every_label():
    data, _ = rows_to_graph(
        ["id(n)", "labels(n)", "properties(n)"], [[1, ["Person", "Employee"], {}]]
    )
    assert data.data.nodes[0].labels == ["Person", "Employee"]


def test_a_variable_projected_twice_is_read_once():
    """
    A multi-hop projection names the same id bare and under an alias. First
    occurrence wins, because both hold the same value.
    """
    columns = ["id(n)", "labels(n)", "properties(n)", "id(n)"]
    data, _ = rows_to_graph(columns, [[1, ["Person"], {"a": 1}, 1]])
    assert len(data.data.nodes) == 1


def test_a_result_with_no_entities_is_a_table_with_its_headers():
    data = result_to_query_data({"columns": ["n.name", "count(n)"], "rows": [["Alice", 2]]})
    assert data.type == "TABLE"
    assert data.data == [["n.name", "count(n)"], ["Alice", 2]]


def test_truncation_is_reported_on_a_graph_as_well_as_a_table():
    graph = result_to_query_data(
        {"columns": EXPAND_COLUMNS, "rows": EXPAND_ROWS, "truncated": True}
    )
    table = result_to_query_data({"columns": ["a"], "rows": [[1]], "truncated": True})
    assert graph.summary["truncated"] == "true"
    assert table.summary["truncated"] == "true"


def test_an_empty_result_is_an_empty_graph_when_the_projection_says_graph():
    data = result_to_query_data({"columns": EXPAND_COLUMNS, "rows": []})
    assert data.type == "GRAPH"
    assert data.data.nodes == [] and data.data.relationships == []
