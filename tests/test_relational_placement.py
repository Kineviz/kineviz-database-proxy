"""
Placement: a relational link sits between two records that the same answer also carries.

Node ids here are ``<Label>:<key>`` minted from the primary key, not read off the edge, so the
one symptom of a wrong endpoint is a graph that still validates: when a neighbour merges into
the seed, the edge's two ends both name a node that *is* present, ``unplaceable`` sees nothing,
and the contract models (``models/project.py``, ``RelationshipData``) carry no cross-field rule
about ends naming distinct records or about a page carrying both. Measured at ``fd56103``:
answer every vertex with the seed's key and 69 of the 72 cases in
``tests/test_relational_graph_intents.py`` and ``tests/test_relational_query_route.py`` still
pass — only the legacy two-hop bites. This file is the rest of what a merge must fail, at the
routes a canvas calls.

The stand-in makes each vertex answerable apart: ``graph_rows`` gives vertex *n* the key
``keys[n]`` and every non-key column its own name, so a projected property carries the
projection ordinal (``__gx_v1_p0-value``). A neighbour collapsed into the seed loses its ``v1``
value to first-seen dedup, and every case below asserts something the merge cannot reproduce.
"""

from __future__ import annotations

from typing import Any, Dict

from tests.test_relational_graph_intents import (
    BACKENDS,
    SEED,
    client,
    graph,
    ids_of,
    server,
    unplaceable,
)
from tests.test_relational_query_route import expand, posted_graph, seeds

#: The second Person in the stand-in's own answer: vertex 1 of every statement takes ``keys[1]``.
NEIGHBOUR = "Person:1002"


def by_id(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {one["id"]: one for one in data["nodes"]}


# ---------------------------------------------------------------------------
# a same-label link between two different records
# ---------------------------------------------------------------------------


@BACKENDS
def test_an_expand_of_the_manager_type_lands_every_link_on_two_different_person_records(
    client, server, backend
):
    """
    ``MANAGER`` joins a Person to another Person, so a merge shows up as a *self-link*: both
    ends minted to ``Person:1001``. Nothing else in the suite asks the intent route for one
    same-label type — the general expand's other neighbours carry other labels, and their ids
    stay distinct however the keys collapse, which is exactly how the merge hid behind
    ``set(ids) - {SEED}`` in ``test_relational_graph_intents.py``.
    """
    data = graph(
        client, backend, "/expand", {"nodeIds": [SEED], "relationships": ["MANAGER"], "limit": 10}
    )

    assert data["relationships"], "a page with no Manager link would pin nothing"
    for one in data["relationships"]:
        assert one["type"] == "MANAGER", one
        assert one["startNodeId"] != one["endNodeId"], one
        assert {one["startNodeId"], one["endNodeId"]} == {SEED, NEIGHBOUR}, one
    assert unplaceable(data) == []
    assert set(ids_of(data["nodes"])) == {SEED, NEIGHBOUR}, ids_of(data["nodes"])


@BACKENDS
def test_the_neighbour_a_same_label_expand_answers_is_a_different_record_by_property_too(
    client, server, backend
):
    """
    Identity, not just presence: the neighbour has to arrive as *its* record.

    Both Persons share a label, so ``labels`` cannot tell them apart and the ids only separate
    what the keys already did. The projected property carries the projection ordinal the
    stand-in baked into the column name, and it is the one field a merged answer cannot keep —
    first-seen dedup in ``merge_graph_results`` leaves the seed's ``v0`` values on the single
    node and the neighbour's ``v1`` record never appears at all.
    """
    data = graph(
        client, backend, "/expand", {"nodeIds": [SEED], "relationships": ["MANAGER"], "limit": 10}
    )
    nodes = by_id(data)
    assert NEIGHBOUR in nodes, "the neighbour merged into the seed: {} is the only Person".format(SEED)
    seed, neighbour = nodes[SEED], nodes[NEIGHBOUR]

    assert seed["properties"]["full_name"] == "__gx_v0_p0-value", seed
    assert neighbour["properties"]["full_name"] == "__gx_v1_p0-value", neighbour
    assert seed["properties"]["person_id"] != neighbour["properties"]["person_id"]


@BACKENDS
def test_a_record_that_links_to_its_own_label_is_expanded_to_another_record_not_to_itself(
    client, server, backend
):
    """
    The other same-label shape: ``TreeNode.parent_id`` points at its own table, one FK row with
    a direction the schema can name. The seed is a TreeNode here, so both ends — seed and
    neighbour — are the very id the merge would collapse, and nothing else in the suite ever
    expands a self-referential record.
    """
    data = graph(client, backend, "/expand", {"nodeIds": ["TreeNode:1001"], "limit": 10})

    edges = [one for one in data["relationships"] if one["type"] == "FK_TREE_PARENT"]
    assert edges, "no self-FK link came back: {}".format(ids_of(data["nodes"]))
    for one in edges:
        assert one["startNodeId"] == "TreeNode:1001", one
        assert one["endNodeId"] == "TreeNode:1002", one
    assert unplaceable(data) == []


@BACKENDS
def test_a_pull_relationship_of_the_manager_type_places_its_edge_on_two_distinct_records(
    client, server, backend
):
    """
    ``/pullRelationship`` already has a case — for ``EMPLOYMENT``, whose ends are different
    labels and so stay different ids even when every vertex answers the seed's key. ``MANAGER``
    is the type that can come back pointing at itself, and it is the type whose whole answer is
    two Person records; which end is which is not pinned (the stand-in reads its keys by
    vertex, not by role), but that they are *two* is exactly what a merge takes away.
    """
    data = graph(client, backend, "/pullRelationship", {"relationship": "MANAGER", "limit": 3, "skip": 0})

    assert [one["type"] for one in data["relationships"]] == ["MANAGER"], data["relationships"]
    for one in data["relationships"]:
        assert one["startNodeId"] != one["endNodeId"], one
    assert unplaceable(data) == []
    assert len(by_id(data)) == 2, ids_of(data["nodes"])


@BACKENDS
def test_a_legacy_statement_restricted_to_the_self_label_type_arrives_between_two_records(
    client, server, backend
):
    """
    The same placement fact on the path a merged canvas takes when the capabilities probe
    fails: the client's own statement, filtered to ``MANAGER``, posted to ``/query``. Legacy
    mode is where a drifted profile reappears as a real bug, and the two-hop is currently the
    only legacy case anywhere that a merge would fail.
    """
    key_prop, key_type = seeds(client, backend)["Person"]
    data = posted_graph(
        client,
        backend,
        expand(
            backend, "Person", key_prop, key_type, "1001", relationships=["MANAGER"], direction="from"
        ),
    )

    assert data["relationships"], "a Manager-restricted legacy expand answered no link"
    for one in data["relationships"]:
        assert one["startNodeId"] != one["endNodeId"], one
        assert {one["startNodeId"], one["endNodeId"]} == {SEED, NEIGHBOUR}, one
    assert unplaceable(data) == []


# ---------------------------------------------------------------------------
# the page that is too small for a link
# ---------------------------------------------------------------------------


@BACKENDS
def test_a_page_cut_between_a_link_and_its_far_end_drops_the_link_rather_than_dangling_it(
    client, server, backend
):
    """
    What ``limit`` promises when both ends will not fit, pinned where the contract cannot see it.

    ``_combine`` (``driver_query.py``) trims nodes to the limit and keeps only the links whose
    ends survived, and the contract models serialise the other answer happily — a relationship
    naming a node the page left out is not a shape error, it is an edge no canvas can draw. The
    general expand already covers the count; what was never pinned is the same-label link: if
    the two ends merge into one record, a one-node page *does* carry both of them and the link
    survives the cut as a self-loop. So this case fails both ways — dangling (a trim that keeps
    the link and drops an end) and merged (a link that was never between two records at all).
    """
    server.forget()
    data = graph(
        client, backend, "/expand", {"nodeIds": [SEED], "relationships": ["MANAGER"], "limit": 1}
    )

    assert len(data["nodes"]) == 1, ids_of(data["nodes"])
    assert data["nodes"][0]["id"] == SEED, "the trim kept a neighbour and dropped the seed it sent"
    assert data["relationships"] == [], data["relationships"]
    assert "__gx_v1_k0" in server.queries()[0], (
        "the statement never projected the far end, so this page proved nothing about the cut"
    )
