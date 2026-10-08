"""
The four graph-intent routes a GraphXR client calls once it is in intent mode, pinned
end to end for a relational project: ``/expand``, ``/pullCategory``, ``/pullRelationship``
and ``/search``, answered by the shipped drivers over a pool that connects to nothing.

What is new here is the *whole path*. The statements are pinned where they are built
(``tests/test_relational_dialect_record.py``), the SQL where it is compiled
(``tests/test_relational_pipeline.py``, ``tests/test_relational_compile.py``), the graph
where a row is converted (``tests/test_relational_convert.py``) and the capability record
where it is declared (``tests/test_relational_capability_record.py``). Nothing between
them was: no test in the suite has ever POSTed an intent route, for any backend, so a
route that resolved its seeds wrong, dropped a page on the floor in Python, or answered a
shape the vendored contract does not carry would have passed.

The one invariant worth more than the rest: a relational node id is ``<Label>:<key>``,
minted from the key rather than read off the edge, so **an edge is only placeable if both
its end nodes are in the same answer**. The contract models accept a relationship whose
``startNodeId`` names nothing — it is not a shape error, it is an edge a canvas cannot
draw — so that check has to be made here and not by ``QueryResponse`` validation.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from graphxr_database_proxy.api import database as database_module
from graphxr_database_proxy.drivers.relational.driver import RelationalDriver
from graphxr_database_proxy.drivers.relational.ids import decode
from graphxr_database_proxy.main import app
from tests.test_relational_capability_record import RELATIONAL_BACKENDS, relational_project
from tests.test_relational_endpoints import ScriptedProjects
from tests.test_relational_pool import FakeEngine, FakeSpelling, access_for, graph_rows
from tests.test_relational_reflect import SPEC, FakeInspector

#: One project per relational engine, named after it, so every case below runs against
#: all three drivers rather than against one and a promise about the others.
BACKENDS = pytest.mark.parametrize(
    "backend", RELATIONAL_BACKENDS, ids=lambda one: one.value
)

SEED = "Person:1001"
PERSON = RELATIONAL_BACKENDS[0]


class IntentServer:
    """
    The seam a relational route reaches, swapped once per test.

    ``RelationalDriver.make_access`` is patched rather than the driver, because the route
    builds a fresh driver per request — which is the point: a ``primary-key`` backend has
    to find its category keys again on every call, and a fixture that handed it a warm
    driver would hide exactly that. One fake engine answers every driver a test builds, so
    ``queries()`` is the list of statements the server would actually have been handed.
    """

    def __init__(self) -> None:
        # One key per entity, and this box has to carry the widest statement anyone routes
        # through it: a one-hop expand joins v0 (the seed) to v1 and one link, while the legacy
        # two-hop a ``tests/test_relational_query_route.py`` posts chains v0 -> v1 -> v2 over
        # e0 and e1. A repeated key is not a shorter answer but a different graph: the middle
        # of a chain would take the seed's identity, and the seed would end up managing itself.
        self.engine = FakeEngine(row_factory=graph_rows([1001, 1002, 1003], edge_keys=[7, 8]))
        self.inspector = FakeInspector(SPEC)
        self._patch: Any = None

    def install(self) -> None:
        holder = self
        self._patch = patch.object(
            RelationalDriver,
            "make_access",
            lambda driver: access_for(
                driver.project,
                # The engine's own spelling compiles the statement; the fake only answers
                # the session setup, the version probe and the row-count statistics, none
                # of which this box has a DBAPI for.
                spelling=FakeSpelling(driver.project.database_type.value),
                engine=holder.engine,
                inspector=holder.inspector,
            ),
        )
        self._patch.start()

    def uninstall(self) -> None:
        self._patch.stop()

    def queries(self) -> List[str]:
        """Each graph statement the server was handed, as text, in the order it was handed."""
        return [str(one).replace("\n", " ") for one in self.engine.selects if "__gx" in str(one)]

    def bounds(self) -> List[Dict[str, Any]]:
        """The values bound inside those statements — where a page and a seed actually live."""
        return [
            dict(one.compile().params) for one in self.engine.selects if "__gx" in str(one)
        ]

    def forget(self) -> None:
        self.engine.selects.clear()


@pytest.fixture
def server():
    made = IntentServer()
    made.install()
    yield made
    made.uninstall()


@pytest.fixture
def client(server):
    """The real routes, over the three shipped relational projects and no credentials."""
    projects = ScriptedProjects()
    for backend in RELATIONAL_BACKENDS:
        projects.add(relational_project(backend))
    app.dependency_overrides[database_module.get_project_service] = lambda: projects
    app.dependency_overrides[database_module.verify_api_key_or_admin] = lambda: None
    with TestClient(app) as made:
        yield made
    app.dependency_overrides.pop(database_module.get_project_service, None)
    app.dependency_overrides.pop(database_module.verify_api_key_or_admin, None)


def url(backend: Any, route: str) -> str:
    return "/api/{}/{}{}".format(backend.value, backend.value, route)


def graph(client: Any, backend: Any, route: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """An intent answer, unwrapped — and a refusal that is not a 200 is a test failure here."""
    reply = client.post(url(backend, route), json=body)
    assert reply.status_code == 200, reply.text
    answer = reply.json()
    assert answer["success"] is True, answer["error"]
    assert answer["data"]["type"] == "GRAPH"
    return answer["data"]["data"]


def ids_of(nodes: List[Dict[str, Any]]) -> List[str]:
    return [one["id"] for one in nodes]


def unplaceable(data: Dict[str, Any]) -> List[str]:
    """Every link whose ends the same answer does not carry — an edge no canvas can draw."""
    present = set(ids_of(data["nodes"]))
    return [
        one["id"]
        for one in data["relationships"]
        if one["startNodeId"] not in present or one["endNodeId"] not in present
    ]


# ---------------------------------------------------------------------------
# /expand
# ---------------------------------------------------------------------------


@BACKENDS
def test_an_expand_of_a_seeded_node_answers_every_link_with_both_of_its_ends_in_the_same_answer(
    client, server, backend
):
    """
    The invariant this whole surface stands on, at the route a client calls.

    ``convert.py`` already refuses to place such an edge (``tests/test_relational_convert.py``)
    and the pipeline proves it against real rows (``test_reverse_edge_endpoint_ids_still_match_their_nodes``),
    but an id minted from a key rather than read off the edge can still come back whole at the
    route only if the projection, the manifest and the merge all keep both records — which is
    why the dialect returns ``n, r, m`` and not ``r, m``.
    """
    data = graph(client, backend, "/expand", {"nodeIds": [SEED], "limit": 10})

    assert data["relationships"], "a seeded expand that answers no link proves nothing"
    assert unplaceable(data) == []
    assert SEED in ids_of(data["nodes"]), "the seed the client sent has to come back"
    assert set(ids_of(data["nodes"])) - {SEED}, "the neighbourhood is the answer, not the seed"


@BACKENDS
def test_an_expand_asked_for_every_direction_answers_a_link_that_points_at_the_seed(
    client, server, backend
):
    """
    ``directions: ["all", "from", "to"]`` is a promise, and ``all`` is the one a key cannot keep.

    A ``primary-key`` predicate pins one end of a traversal — the record withholds
    ``internalRelationships`` for exactly that reason — so ``all`` has to be served by fanning
    the orientations out per link rather than by one statement that can only look one way. An
    ``all`` expand that answered only outgoing links would still be a 200 with a graph in it.
    """
    data = graph(client, backend, "/expand", {"nodeIds": [SEED], "limit": 10, "direction": "all"})

    incoming = [
        one
        for one in data["relationships"]
        if one["endNodeId"] == SEED and one["startNodeId"] != SEED
    ]
    assert incoming, "FK_ORDER_CUSTOMER runs Order -> Person, so a Person has links pointing at it"
    assert unplaceable(data) == []


@BACKENDS
def test_an_expand_asked_for_incoming_links_only_leaves_out_the_ones_that_point_away(
    client, server, backend
):
    """
    The other half of the direction table: ``to`` must not quietly mean ``all``.

    Ignoring a direction the client asked for is invisible on a canvas — the graph is just
    bigger than the person asked — so it is asserted against a mapping that has both kinds:
    EMPLOYMENT leaves a Person, FK_ORDER_CUSTOMER arrives at one.
    """
    data = graph(client, backend, "/expand", {"nodeIds": [SEED], "limit": 10, "direction": "to"})

    assert {one["type"] for one in data["relationships"]} == {"MANAGER", "FK_ORDER_CUSTOMER"}
    assert all(one["endNodeId"] == SEED for one in data["relationships"])
    assert unplaceable(data) == []


@BACKENDS
def test_every_node_id_an_expand_answers_is_accepted_as_a_seed_by_the_next_expand(
    client, server, backend
):
    """
    Identity has to round-trip through the wire, not just through the compiler.

    The row-level half is pinned by ``test_a_minted_id_selects_the_row_it_was_minted_from``;
    this is the half a client actually depends on: the id string the browser was given splits
    into a label and a key, that label's table is the end the next statement pins, and the key
    is bound rather than written into the text. A seed that resolves to nothing answers an
    empty graph, which on a canvas reads as a database with no more data in it.
    """
    first = graph(client, backend, "/expand", {"nodeIds": [SEED], "limit": 10})
    seeds = ids_of(first["nodes"])
    server.forget()

    again = graph(client, backend, "/expand", {"nodeIds": seeds, "limit": 10})

    statements = server.queries()
    bindings = [set(one.values()) for one in server.bounds()]
    assert again["nodes"], "every seed came back as nothing — the ids do not round-trip"
    for one in seeds:
        label, key = decode(one)
        assert label and key, one
        assert any('FROM dbo."{}" AS v0'.format(label) in text.replace('"dbo"', 'dbo') for text in statements), (
            "{} was sent as a seed and no statement pins its table".format(one)
        )
        assert any(int(key) in bound for bound in bindings), "{} lost its key".format(one)


@BACKENDS
def test_an_expand_trimmed_to_a_page_too_small_for_a_link_keeps_no_link_whose_end_did_not_fit(
    client, server, backend
):
    """
    A fan-out is several statements, and one answer is the page the caller asked for.

    ``_execute`` stops as soon as the rows are paid for and ``_combine`` then trims the merged
    nodes to the limit — dropping the links whose ends did not survive. Nothing in the suite
    covered that trim (the only relationship count anywhere is ``test_relational_driver.py``'s
    schema-level drop of an edge that names no record), and the contract cannot see the
    difference: a relationship pointing at a node the same answer left out serialises fine and
    draws nothing.
    """
    data = graph(client, backend, "/expand", {"nodeIds": [SEED], "limit": 1})

    assert len(data["nodes"]) <= 1
    assert unplaceable(data) == []
    assert data["relationships"] == [], "one node cannot carry a link: its other end did not fit the page"


# ---------------------------------------------------------------------------
# /pullCategory
# ---------------------------------------------------------------------------


@BACKENDS
def test_a_pull_category_reads_the_whole_label_and_pages_where_the_rows_are(
    client, server, backend
):
    """
    The canvas's "load more" for one label, with the limit and the offset in the statement.

    The repo rule is that a collection is paged at the source, and the SQL shapes are pinned
    per engine already (``test_each_engine_paginates_the_way_its_grammar_demands``). What was
    unpinned is that the *intent* route passes the caller's page through at all: an intent
    that dropped the ``SKIP`` would hand the client the first page twice and look like a
    stalled canvas, and the numbers have to be inside the statement the server was handed
    rather than applied to the rows afterwards.
    """
    server.forget()
    data = graph(client, backend, "/pullCategory", {"category": "Person", "limit": 5, "skip": 2})

    assert data["nodes"] and all("Person" in one["labels"] for one in data["nodes"])
    assert len(server.queries()) == 1, "one label is one statement"
    text = server.queries()[0]
    assert "WHERE" not in text.split("ORDER BY")[0], "a pull is not a seed lookup"
    bound = server.bounds()[0]
    assert 5 in bound.values() and 2 in bound.values(), bound


@BACKENDS
def test_a_pull_category_leaves_out_the_nodes_the_canvas_already_holds(
    client, server, backend
):
    """
    ``pull.excludeLoaded: true`` says the proxy can do the excluding, so it has to ask for it.

    The fake engine answers the same row whatever the predicate, so this pins the request and
    not the result: the loaded ids' keys become a ``NOT IN`` bound on the label's own key
    column, in the statement, on the server side. Excluding them in Python after fetching the
    whole label would be both the repo's forbidden read and a page that shrinks per call.
    """
    server.forget()
    graph(
        client,
        backend,
        "/pullCategory",
        {"category": "Person", "limit": 5, "skip": 0, "loadedNodeIds": ["Person:1001", "Person:1002"]},
    )

    text = server.queries()[0]
    assert "NOT IN" in text, text
    assert {1001, 1002} <= set(server.bounds()[0].values())


# ---------------------------------------------------------------------------
# /pullRelationship
# ---------------------------------------------------------------------------


@BACKENDS
def test_a_pull_relationship_answers_the_link_and_the_two_records_it_joins(
    client, server, backend
):
    """
    Loading more of one edge type brings its endpoints with it, paged as it goes.

    A relationship type here is a table, so the statement is type-specific without a
    ``type(r)`` predicate; what the route has to keep is the ``n, r, m`` projection and the
    caller's page. The endpoints are asserted by label, not just by presence, because an
    ``EMPLOYMENT`` row joined onto itself would still validate.
    """
    server.forget()
    data = graph(client, backend, "/pullRelationship", {"relationship": "EMPLOYMENT", "limit": 3, "skip": 1})

    assert [one["type"] for one in data["relationships"]] == ["EMPLOYMENT"]
    assert unplaceable(data) == []
    labels = {one["labels"][0] for one in data["nodes"]}
    assert labels == {"Person", "Organization"}, labels
    bound = server.bounds()[0]
    assert 3 in bound.values() and 1 in bound.values(), bound
    assert len(server.queries()) == 1


# ---------------------------------------------------------------------------
# /search — the refusal has to be the record's own answer
# ---------------------------------------------------------------------------


@BACKENDS
def test_a_backend_with_no_index_refuses_search_by_name_instead_of_answering_an_empty_canvas(
    client, server, backend
):
    """
    A withheld capability is refused, and refused the same way the record withheld it.

    ``fulltextSearch.supported`` is false for all three engines because none of them has an
    index the contract could point at without the customer creating one, and the ``search``
    intent is not declared — so the route has to answer 501 naming the intent. An empty graph
    here is the bad answer: it is what a canvas shows as "no matches" while the database was
    never asked, and the 501 path in ``_require_intent`` had no test at all.
    """
    reply = client.post(url(backend, "/search"), json={"keyword": "Avery", "limit": 5})

    assert reply.status_code == 501
    assert "search" in reply.json()["detail"]

    document = client.get(url(backend, "/capabilities")).json()
    assert "search" not in document["intents"]
    assert document["fulltextSearch"]["supported"] is False


@BACKENDS
def test_the_graph_schema_offers_the_labels_and_links_the_intents_just_answered(
    client, server, backend
):
    """
    ``/graphSchema`` is what a canvas draws its legend from; the intents are what fill it.

    A category the intents cannot answer, or an edge type that never arrives, is a control
    that offers nothing — and the seed predicate depends on the first: ``_resolve_refs``
    drops an id whose label carries no key, so a category listed with an empty ``keys``
    would answer every expand of it with an empty graph. The driver-level shape is pinned in
    ``tests/test_relational_capability_record.py``; this is the pair of answers agreeing.
    """
    data = graph(client, backend, "/expand", {"nodeIds": [SEED], "limit": 10})
    schema = client.get(url(backend, "/graphSchema")).json()
    assert schema["success"] is True, schema["error"]

    categories = {one["name"]: one for one in schema["data"]["categories"]}
    relationships = {one["name"]: one for one in schema["data"]["relationships"]}

    for node in data["nodes"]:
        for label in node["labels"]:
            assert label in categories, "{} names a category the schema does not offer".format(node["id"])
            assert categories[label]["keys"], "a label with no key can never be a seed again"
    for one in data["relationships"]:
        assert one["type"] in relationships, one["id"]
        ends = relationships[one["type"]]
        assert (ends["startCategory"], ends["endCategory"]) == (
            one["startNodeId"].split(":", 1)[0],
            one["endNodeId"].split(":", 1)[0],
        ), "{} is drawn the wrong way round".format(one["id"])
