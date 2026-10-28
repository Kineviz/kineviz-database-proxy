# -*- coding: utf-8 -*-
"""
Worker rows -> ``QueryData``, for both engines' spelling of the same shape.

The records here are copied from real results: Kuzu 0.11.3 returns ``_id`` and
``_label``, Ladybug 0.19.1 returns ``_ID`` and ``_LABEL``. One mapper has to read
both, because a driver that knew only one spelling would see a graph as a table of
dicts and put nothing on the canvas.
"""

from __future__ import annotations

import pytest

from kineviz_database_proxy.drivers.embedded.kuzu_mapping import (
    is_node,
    is_relationship,
    node_id,
    relationship_id,
    result_to_query_data,
    rows_hold_graph,
    rows_to_graph,
    rows_to_table,
)

KEYS = {"Person": "name", "City": "cid"}


def kuzu_row():
    """Verbatim from ``MATCH (p:Person)-[e:LivesIn]->(c:City) RETURN p, e, c`` on 0.11.3."""
    return [
        {"_id": {"offset": 0, "table": 0}, "_label": "Person", "name": "Alice", "age": 30},
        {
            "_src": {"offset": 0, "table": 0},
            "_dst": {"offset": 0, "table": 1},
            "_label": "LivesIn",
            "_id": {"offset": 0, "table": 2},
            "since": 2020,
        },
        {"_id": {"offset": 0, "table": 1}, "_label": "City", "cid": 1, "name": "Toronto"},
    ]


def ladybug_row():
    """The same result from Ladybug 0.19.1, which uppercases every meta key."""
    return [
        {"_ID": {"offset": 0, "table": 0}, "_LABEL": "Person", "name": "Alice", "age": 30},
        {
            "_SRC": {"offset": 0, "table": 0},
            "_DST": {"offset": 0, "table": 1},
            "_LABEL": "LivesIn",
            "_ID": {"offset": 0, "table": 2},
            "since": 2020,
        },
        {"_ID": {"offset": 0, "table": 1}, "_LABEL": "City", "cid": 1, "name": "Toronto"},
    ]


ROWS = pytest.mark.parametrize("row", [kuzu_row(), ladybug_row()], ids=["kuzu", "ladybug"])


# -- recognition ------------------------------------------------------------


@ROWS
def test_nodes_and_relationships_are_recognised_in_either_casing(row):
    node, relationship, other = row
    assert is_node(node) and is_node(other)
    assert is_relationship(relationship)
    # A relationship also carries an id and a label; the endpoints are what tell
    # them apart, so the order of the checks matters.
    assert not is_node(relationship)


def test_an_ordinary_dict_is_not_a_graph_entity():
    assert not is_node({"name": "Alice"})
    assert not is_relationship({"since": 2020})
    assert not rows_hold_graph([[1, "text", {"a": 1}]])


@ROWS
def test_a_result_is_a_graph_when_it_holds_entities(row):
    assert rows_hold_graph([row])


def test_entities_are_found_inside_lists_and_maps():
    nested = [[{"path": [kuzu_row()[0]]}]]
    assert rows_hold_graph(nested)


def test_a_recursive_relationship_is_unpacked():
    # What `-[r*1..2]-` returns. The dialect does not emit it, but a hand-written
    # query can.
    node, relationship, other = kuzu_row()
    recursive = {"_nodes": [other], "_rels": [relationship]}
    data, _dropped = rows_to_graph([[node, recursive]], KEYS)
    assert {n.id for n in data.data.nodes} == {"Person:Alice", "City:1"}
    assert len(data.data.relationships) == 1


# -- ids --------------------------------------------------------------------


@ROWS
def test_a_node_id_is_its_label_and_primary_key(row):
    # The only form the dialect's own predicate can match again: ID(n) has no
    # writable literal, since `n._id` is rejected as reserved.
    assert node_id(row[0], KEYS) == "Person:Alice"
    assert node_id(row[2], KEYS) == "City:1"


def test_a_node_id_falls_back_to_the_internal_id_when_the_key_is_unknown():
    # It still renders; it simply cannot be expanded from. Marked so it can never be
    # mistaken for a real key.
    identity = node_id(kuzu_row()[0], {})
    assert identity == "Person:#0:0"


@ROWS
def test_a_relationship_id_is_unique_without_being_matchable(row):
    assert relationship_id(row[1]) == "LivesIn:2:0"


def test_two_edges_of_one_type_between_the_same_pair_keep_separate_ids():
    node, relationship, other = kuzu_row()
    second = dict(relationship, _id={"offset": 1, "table": 2}, since=2021)
    data, _dropped = rows_to_graph([[node, relationship, other], [node, second, other]], KEYS)
    assert len(data.data.relationships) == 2


# -- graphs -----------------------------------------------------------------


@ROWS
def test_a_row_maps_to_a_graph_with_the_edge_between_its_two_nodes(row):
    data, dropped = rows_to_graph([row], KEYS)

    assert dropped == 0
    assert [n.id for n in data.data.nodes] == ["Person:Alice", "City:1"]
    edge = data.data.relationships[0]
    assert (edge.type, edge.startNodeId, edge.endNodeId) == ("LivesIn", "Person:Alice", "City:1")


@ROWS
def test_the_engines_bookkeeping_keys_are_not_handed_to_the_client(row):
    data, _dropped = rows_to_graph([row], KEYS)
    assert data.data.nodes[0].properties == {"name": "Alice", "age": 30}
    assert data.data.relationships[0].properties == {"since": 2020}


@ROWS
def test_entities_are_de_duplicated_across_rows(row):
    data, _dropped = rows_to_graph([row, row], KEYS)
    assert len(data.data.nodes) == 2
    assert len(data.data.relationships) == 1


def test_an_edge_finds_its_endpoints_in_a_different_row():
    node, relationship, other = kuzu_row()
    data, dropped = rows_to_graph([[node], [other], [relationship]], KEYS)
    assert dropped == 0
    assert data.data.relationships[0].startNodeId == "Person:Alice"


def test_an_edge_whose_endpoints_are_absent_is_reported_rather_than_invented():
    # `MATCH ()-[r]->() RETURN r` cannot be placed: an endpoint arrives as an
    # internal id, and turning that into a client id needs the node. Inventing a
    # placeholder would put something on the canvas that is not in the graph.
    _node, relationship, _other = kuzu_row()
    data, dropped = rows_to_graph([[relationship]], KEYS)
    assert dropped == 1
    assert data.data.relationships == []


def test_the_dropped_count_reaches_the_caller_in_the_summary():
    _node, relationship, _other = kuzu_row()
    data = result_to_query_data({"columns": ["r"], "rows": [[relationship]]}, KEYS)
    assert data.summary["droppedRelationships"] == "1"


def test_ladybug_multiple_labels_are_all_carried():
    # Ladybug added multiple labels per node after the fork; the id uses the first.
    node = {"_ID": {"offset": 0, "table": 0}, "_LABEL": ["Person", "Employee"], "name": "Alice"}
    data, _dropped = rows_to_graph([[node]], KEYS)
    assert data.data.nodes[0].labels == ["Person", "Employee"]
    assert data.data.nodes[0].id == "Person:Alice"


# -- tables -----------------------------------------------------------------


def test_a_table_result_is_a_2d_array_headed_by_its_columns():
    data = rows_to_table([[1, "a"], [2, "b"]], ["n", "label"])
    assert data.type == "TABLE"
    assert data.data == [["n", "label"], [1, "a"], [2, "b"]]


def test_an_empty_table_still_carries_its_header():
    assert rows_to_table([], ["n"]).data == [["n"]]


def test_a_catalog_result_is_a_table_not_a_graph():
    catalog = {
        "columns": ["id", "name", "type"],
        "rows": [[0, "Person", "NODE"], [3, "LivesIn", "REL"]],
    }
    assert result_to_query_data(catalog).type == "TABLE"


def test_a_truncated_result_says_so():
    data = result_to_query_data({"columns": ["n"], "rows": [[1]], "truncated": True})
    assert data.summary["truncated"] == "true"
