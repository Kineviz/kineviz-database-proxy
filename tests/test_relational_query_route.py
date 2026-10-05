"""
``POST /api/{backend}/{project}/query`` handed a statement a GraphXR client wrote itself.

Legacy mode is what runs whenever the ``GET /capabilities`` probe fails, so this route is the
one graph surface that must read a statement this proxy never composed. Nothing else in the
suite had ever posted to it for any relational backend.

Every statement here is rendered by the builders
``tests/test_relational_dialect_record.py`` proves byte-for-byte equal to the client's own
profile (``proxy/dialect.ts``), with the seed's label, key column and key type read back off
``/graphSchema`` the way the adapter reads them — Cypher invented in this file would pin
nothing. What the intent routes cannot see and these cases can: names that have all drifted,
a seed literal that arrives as *text* rather than as a value this proxy chose to bind, and the
mapping gate at ``driver_query.py:81``, which stands between a client statement and a 500.
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Dict, Tuple

import pytest
from fastapi.testclient import TestClient

from graphxr_database_proxy.api import database as database_module
from graphxr_database_proxy.drivers.dialect import (
    DIALECTS,
    NodeRef,
    build_expand,
    build_pull_category,
)
from graphxr_database_proxy.drivers.intents import split_label_key_id
from graphxr_database_proxy.drivers.relational import query as query_layer
from graphxr_database_proxy.drivers.relational.catalog import propose
from graphxr_database_proxy.main import app
from graphxr_database_proxy.models.project import Project
from tests.test_relational_capability_record import RELATIONAL_BACKENDS, relational_project
from tests.test_relational_dialect_record import CLIENT_GOLDEN_ONE_HOP
from tests.test_relational_endpoints import ScriptedProjects
from tests.test_relational_graph_intents import (
    BACKENDS,
    IntentServer,
    client,
    ids_of,
    server,
    unplaceable,
    url,
)
from tests.test_relational_pool import config, draft_document
from tests.test_relational_reflect import SPEC, FakeInspector

SEED = "Person:1001"

#: The bolt-shaped seed the client's ``default`` branch emits when its profile has no case for
#: this backend — transcribed from ``tests/test_relational_dialect_record.py:406``.
BOLT_SEED = 'MATCH (n:`Person`)-[r]-(m) WHERE ID(n) IN ["Person:1001"] RETURN n, r, m'

#: ``:368``'s two-hop text, which the same file records for this side only.
BOLT_TWO_HOP = 'MATCH (n:`Person`)-[r]->(n1)-[r1]->(m) WHERE ID(n) IN ["Person:1001"] RETURN n, r, m, n1, r1'


def seeds(client: Any, backend: Any) -> Dict[str, Tuple[str, str]]:
    """``label -> (key column, key type)``, read off the route the adapter reads before it builds."""
    schema = client.get("/api/{}/{}{}".format(backend.value, backend.value, "/graphSchema")).json()
    assert schema["success"] is True, schema["error"]
    out: Dict[str, Tuple[str, str]] = {}
    for category in schema["data"]["categories"]:
        key = category["keys"][0]
        out[category["name"]] = (key, category["keysTypes"][key])
    return out


def expand(
    backend: Any, category: str, key_prop: str, key_type: str, key_value: str, **kwargs: Any
) -> str:
    """One statement, as the client's profile renders it for a seed of this mapping's own key."""
    return build_expand(
        DIALECTS[backend.value],
        [NodeRef(category=category, key_prop=key_prop, key_value=key_value)],
        key_types={category: key_type},
        limit=10,
        skip=0,
        **kwargs,
    )[0]


def posted_graph(client: Any, backend: Any, statement: str) -> Dict[str, Any]:
    """The graph ``/query`` answers for a client-built statement — a refusal fails the case."""
    reply = client.post(url(backend, "/query"), json={"query": statement})
    assert reply.status_code == 200, reply.text
    answer = reply.json()
    assert answer["success"] is True, answer["error"]
    assert answer["data"]["type"] == "GRAPH", answer["data"]
    return answer["data"]["data"]


def posted_refusal(client: Any, server: Any, backend: Any, statement: str) -> str:
    """The sentence ``/query`` answers a statement it cannot read with, and proof it asked nothing."""
    reply = client.post(url(backend, "/query"), json={"query": statement})
    # 500 would be the wrong shape of right answer: the client's own error handling keys on the
    # body it gets for a refusal, and a 500 mid-canvas is what an escaping exception becomes.
    assert reply.status_code == 200, reply.text
    answer = reply.json()
    assert answer["success"] is False, answer
    assert answer["data"] is None, "a refusal that also carries a graph is not a refusal"
    assert server.queries() == [], "something was handed to the engine anyway"
    return str(answer["error"])


# ---------------------------------------------------------------------------
# 1. a legacy one-hop expand comes back as a graph
# ---------------------------------------------------------------------------


@BACKENDS
def test_the_statement_under_test_is_the_one_the_clients_own_test_asserts(
    client, server, backend
):
    """
    The guard on every case below: the text a client emits for *this* mapping is the golden
    text of ``:322`` with this mapping's own key column in it and one seed instead of two. If
    the profile and the builder ever part company, these cases stop being legacy mode.
    """
    key_prop, key_type = seeds(client, backend)["Person"]
    expected = CLIENT_GOLDEN_ONE_HOP.replace("IN [1001,1002]", "IN [1001]").replace(
        "`id`", "`{}`".format(key_prop)
    )

    assert expand(backend, "Person", key_prop, key_type, "1001") == expected


@BACKENDS
def test_a_legacy_one_hop_expand_arrives_as_a_graph_with_every_link_placeable(client, server, backend):
    """
    ``RETURN n, r, m`` is a request for a graph, and a relational edge is only placeable if
    both of its ends are in the same answer — here for a statement this proxy did not compose.
    """
    key_prop, key_type = seeds(client, backend)["Person"]
    server.forget()
    data = posted_graph(client, backend, expand(backend, "Person", key_prop, key_type, "1001"))

    assert data["relationships"], "a seeded expand that answers no link proves nothing"
    assert unplaceable(data) == []
    assert SEED in ids_of(data["nodes"]), "the seed the client sent has to come back"
    assert set(ids_of(data["nodes"])) - {SEED}, "the neighbourhood is the answer, not the seed"
    # An untyped connector over this mapping fans out per link: four statements, one answer.
    assert len(server.queries()) == 4, server.queries()


@BACKENDS
def test_a_node_id_the_route_answered_selects_its_own_table_again_in_the_next_statement(
    client, server, backend
):
    """
    The round trip a canvas actually performs in legacy mode: read an id off the answer, split
    it on the first colon, and emit ``n.`<key>` IN [<literal>]`` for that label's own column.
    A neighbour that cannot be re-selected is a node the user can draw and never expand.
    """
    persons = seeds(client, backend)["Person"]
    first = posted_graph(client, backend, expand(backend, "Person", *persons, "1001"))
    neighbour = next(one for one in ids_of(first["nodes"]) if one != SEED)
    label, key = split_label_key_id(neighbour)
    assert label and key, neighbour
    server.forget()

    again = posted_graph(client, backend, expand(backend, label, *seeds(client, backend)[label], key))

    assert again["nodes"], "{} came back as nothing — the id does not round-trip".format(neighbour)
    assert any('FROM "{}" AS v0'.format(label) in text for text in server.queries()), (
        "{} was sent as a seed and no statement pins its table".format(neighbour)
    )
    assert any(str(one) == key for bound in server.bounds() for one in bound.values()), (
        "{} lost its key on the way to the statement".format(neighbour)
    )


# ---------------------------------------------------------------------------
# 2. a legacy two-hop chains in one row, and does not multiply
# ---------------------------------------------------------------------------


@BACKENDS
def test_a_legacy_two_hop_chains_in_one_row_because_the_pattern_shares_its_middle_variable(
    client, server, backend
):
    """
    ``:368``'s text, posted. ``n1`` is the end of the first hop *and* the start of the second,
    and ``tests/test_relational_plan.py:163`` says that shared variable is the only reason the
    two hops are not enumerated independently: per-hop choice would be 2 x 3 = 6 fan-outs,
    while the label-consistent chains in this mapping are two.
    """
    key_prop, key_type = seeds(client, backend)["Person"]
    statement = expand(backend, "Person", key_prop, key_type, "1001", direction="from", hops=2)
    assert "-[r]->(n1)-[r1]->(m)" in statement, statement
    server.forget()
    data = posted_graph(client, backend, statement)

    assert len(server.queries()) == 2, server.queries()
    for text in server.queries():
        # One row per path: three vertex tables and two link tables in a single FROM, which is
        # what a chained pattern means — two separate per-hop statements would be a join in
        # Python over a set the source never bounded.
        assert len(set(re.findall(r"\bAS (v\d+)\b", text))) == 3, text
        assert len(set(re.findall(r"\bAS (e\d+)\b", text))) == 2, text
    assert data["relationships"] and unplaceable(data) == []
    # Every hop variable is projected, or its edge has no end to be placed at (the fake answers
    # the same key for every vertex, so placement is what the projection has to keep whole).
    for text in server.queries():
        for alias in ("v0", "v1", "v2"):
            assert "__gx_{}_k0".format(alias) in text, text
    ids = ids_of(data["nodes"])
    assert len(ids) == len(set(ids)), "the merge let one node arrive twice"


# ---------------------------------------------------------------------------
# 3. RETURN *, and the names a pattern carries that the mapping does not
# ---------------------------------------------------------------------------


@BACKENDS
def test_a_return_star_pull_pages_where_the_rows_are(client, server, backend):
    """``MATCH (n:`Person`) RETURN * SKIP 7 LIMIT 5`` is a pull, and the page belongs in the SQL."""
    server.forget()
    data = posted_graph(
        client, backend, build_pull_category(DIALECTS[backend.value], "Person", limit=5, skip=7)[0]
    )

    assert len(server.queries()) == 1, "one label is one statement"
    assert all("Person" in one["labels"] for one in data["nodes"])
    assert data["relationships"] == [], "a pull of one label asks for no link"
    bound = server.bounds()[0]
    assert 5 in bound.values() and 7 in bound.values(), bound
    assert "OFFSET" in server.queries()[0].upper(), server.queries()[0]


@BACKENDS
def test_a_name_the_mapping_does_not_carry_is_refused_rather_than_answered_empty(client, server, backend):
    """
    Two drifted names, one decision each. ``:344``'s ``:KNOWS`|`ACTED IN```, and a label nobody
    declared: neither can match a row, and an empty canvas is what a person reads as "the
    database has nothing" — the exact confusion ``_run``'s no-branch answer exists to avoid.
    """
    key_prop, key_type = seeds(client, backend)["Person"]
    hidden = expand(
        backend,
        "Person",
        key_prop,
        "1001",
        key_type,
        relationships=["KNOWS", "ACTED IN"],
        exclude_relationship_types=["HATES"],
    )
    assert "-[r:`KNOWS`|`ACTED IN`]-(m)" in hidden, hidden

    assert "KNOWS" in posted_refusal(client, server, backend, hidden)
    ghost = build_pull_category(DIALECTS[backend.value], "GhostLabel", limit=10, skip=0)[0]
    assert "GhostLabel" in posted_refusal(client, server, backend, ghost)


@BACKENDS
def test_a_key_column_the_mapping_does_not_have_is_refused_naming_the_ones_it_has(
    client, server, backend
):
    """
    The client's golden text posted *verbatim* — ``n.`id```, which this mapping calls
    ``person_id``. A profile that names the wrong column has to be told what the label has,
    because the same text with a different key name is a query that matches nothing.
    """
    error = posted_refusal(client, server, backend, CLIENT_GOLDEN_ONE_HOP)

    assert "id" in error
    assert "person_id" in error, error


@BACKENDS
def test_a_hidden_relationship_type_is_excluded_by_the_source_not_in_python(client, server, backend):
    """
    ``AND NOT(type(r) IN [...])`` over an untyped connector: the excluded type's branch has to
    be contradicted in the statement it was compiled from, and the rest still carry the seed.
    Filtering link types in Python after fetching them is the read this repo forbids.
    """
    key_prop, key_type = seeds(client, backend)["Person"]
    statement = expand(
        backend, "Person", key_prop, key_type, "1001", exclude_relationship_types=["EMPLOYMENT"]
    )
    assert 'AND NOT(type(r) IN ["EMPLOYMENT"])' in statement, statement
    server.forget()
    posted_graph(client, backend, statement)

    texts = server.queries()
    assert len(texts) == 4, texts
    contradicted = [index for index, one in enumerate(texts) if "WHERE false" in one]
    assert len(contradicted) == 1, texts
    assert 'JOIN "Employment" AS e0' in texts[contradicted[0]], texts
    # The seed is not even bound where nothing can match; every other branch still carries it.
    # The exclusion is pinned on the statements rather than on the answer because the stand-in
    # database answers a row for every statement it is handed, `WHERE false` included.
    for index, bound in enumerate(server.bounds()):
        assert (1001 in bound.values()) is (index != contradicted[0]), (texts[index], bound)
    # An undeclared type behind NOT() is a no-op, not a refusal: nothing was asked for it.
    server.forget()
    posted_graph(
        client,
        backend,
        expand(backend, "Person", key_prop, key_type, "1001", exclude_relationship_types=["HATES"]),
    )
    assert len(server.queries()) == 4, server.queries()


@BACKENDS
def test_a_seed_with_no_link_type_at_all_answers_an_empty_graph_that_says_so(client, server, backend):
    """
    The boundary the empty-canvas law leaves standing: ``Session`` is declared and keyed, and
    no link table in this mapping reaches it, so zero branches is a fact about the graph rather
    than a wrong name. The body distinguishes it — ``relational.branches`` is ``0`` and nothing
    ran — which is what a client has to have to say "this label has no links".
    """
    token, token_type = seeds(client, backend)["Session"]
    server.forget()
    reply = client.post(
        url(backend, "/query"), json={"query": expand(backend, "Session", token, token_type, "A:1")}
    )
    answer = reply.json()

    assert reply.status_code == 200 and answer["success"] is True, answer
    assert answer["data"]["type"] == "GRAPH"
    assert answer["data"]["data"]["nodes"] == []
    assert answer["data"]["summary"]["relational.branches"] == "0"
    assert server.queries() == []


# ---------------------------------------------------------------------------
# 4. a statement built from the client's `default` branch is refused by name
# ---------------------------------------------------------------------------


@BACKENDS
def test_a_bolt_shaped_identity_predicate_is_refused_by_name_at_the_route(client, server, backend):
    """
    The defect the whole mirror exists to prevent, at the route a drifted client would reach.
    ``prepare`` raises ``RefusedQuery`` (a ``TranslateError``), ``execute_query`` catches it at
    ``driver_query.py:86`` and answers with the sentence — so no statement goes to the engine,
    no selected id is printed back, and an empty graph is not offered as an answer.
    """
    with pytest.raises(query_layer.RefusedQuery) as escaped:
        query_layer.prepare(BOLT_SEED)
    assert "ID(n) is not supported" in str(escaped.value)

    for statement in (BOLT_SEED, BOLT_TWO_HOP):
        error = posted_refusal(client, server, backend, statement)
        assert "ID(n) is not supported" in error, error
        assert "Person:1001" not in error or "IN" in error, error


# ---------------------------------------------------------------------------
# 5. a key the client quoted, a big one it did not, and a date one
# ---------------------------------------------------------------------------


@BACKENDS
def test_a_key_with_a_colon_in_it_stays_the_string_the_client_quoted(client, server, backend):
    """
    ``:327``'s ``"A:1"``, arriving as a literal rather than as a value this proxy bound: the
    seed splits on the *first* colon and the key keeps the rest, and a numeric column whose
    literal is quoted is a scan that matches nothing — so the bind is the whole string.
    """
    key_prop, key_type = seeds(client, backend)["Person"]
    statement = expand(backend, "Person", key_prop, key_type, "A:1")
    assert 'IN ["A:1"]' in statement, statement
    server.forget()
    posted_graph(client, backend, statement)

    for bound in server.bounds():
        assert bound["gx_p1"] == "A:1", bound


@BACKENDS
def test_a_key_above_two_to_the_fifty_three_arrives_as_digits_and_not_as_a_float(
    client, server, backend
):
    """
    A big numeric key is left bare by both sides' ``renderKeyLiteral``, and a bind that went
    through a float (or a JS ``Number``) comes back rounded: the expand then silently matches
    a different row, which no canvas can show as wrong.
    """
    key_prop, key_type = seeds(client, backend)["Person"]
    statement = expand(backend, "Person", key_prop, key_type, "9007199254740993")
    assert "IN [9007199254740993]" in statement, statement
    server.forget()
    posted_graph(client, backend, statement)

    for bound in server.bounds():
        value = bound["gx_p1"]
        assert str(value) == "9007199254740993", bound
        assert isinstance(value, int) and not isinstance(value, bool), bound


#: One extra table, because the shared spec has no date-keyed node: ``Visit``'s primary key is
#: a ``date``, so a client following ``:386`` quotes it and this proxy has to read it back.
DATE_SPEC: Dict[str, Any] = dict(
    SPEC,
    Visit={
        "columns": [("arrived_on", "date", False), ("person_id", "int", False)],
        "pk": ("PK_Visit", ["arrived_on"]),
        "fks": [("FK_Visit_Person", ["person_id"], "Person")],
    },
)


@pytest.fixture
def date_stack():
    """The three projects again, over a schema with a date key, under names nothing else caches."""
    server = IntentServer()
    server.inspector = FakeInspector(DATE_SPEC)
    server.install()
    document = propose(FakeInspector(DATE_SPEC)).model_dump(by_alias=True)
    projects = ScriptedProjects()
    for backend in RELATIONAL_BACKENDS:
        projects.add(
            Project(
                id="{}-date".format(backend.value),
                name="{}-date".format(backend.value),
                database_type=backend,
                database_config=config(type=backend, graph_mapping=document),
            )
        )
    app.dependency_overrides[database_module.get_project_service] = lambda: projects
    app.dependency_overrides[database_module.verify_api_key_or_admin] = lambda: None
    with TestClient(app) as made:
        yield made, server
    app.dependency_overrides.pop(database_module.get_project_service, None)
    app.dependency_overrides.pop(database_module.verify_api_key_or_admin, None)
    server.uninstall()


@BACKENDS
def test_a_date_key_the_client_quoted_reaches_the_bind_as_a_date(date_stack, backend):
    """
    The arrival half ``:386`` could only pin to the statement text. ``DATE`` is in neither
    language's numeric table, so a real client quotes it: correct Cypher, wrong SQL — and the
    route has to bind that literal with the column's own type, never paste it into the text.
    """
    made, server = date_stack
    statement = expand(backend, "Visit", "arrived_on", "DATE", "2020-01-02")
    assert 'WHERE n.`arrived_on` IN ["2020-01-02"]' in statement, statement
    server.forget()
    reply = made.post(
        "/api/{}/{}/query".format(backend.value, "{}-date".format(backend.value)),
        json={"query": statement},
    )
    answer = reply.json()
    assert reply.status_code == 200 and answer["success"] is True, answer["error"]
    assert answer["data"]["data"]["relationships"], "the date seed found no link"

    assert server.bounds(), "nothing was bound, so nothing was asked of the database"
    for bound in server.bounds():
        assert bound["gx_p1"] == datetime.date(2020, 1, 2), bound
        assert '"2020-01-02"' not in str(bound["gx_p1"]), bound
    assert "2020-01-02" not in server.queries()[0], server.queries()[0]


# ---------------------------------------------------------------------------
# 6. the mapping gate: a project whose mapping will not load
# ---------------------------------------------------------------------------


@pytest.fixture
def broken_stack():
    """
    A saved mapping the loader refuses. ``api/relational.py`` says an invalid mapping is still
    saved, so ``/query`` is where a person meets it — and ``driver_query.py:81`` is the only
    thing standing between that document and a compile against names that do not exist.
    """
    document = draft_document().model_dump(by_alias=True)
    for edge in document["edges"]:
        for endpoint in edge["endpoints"]:
            if endpoint["dst"]["label"] == "Organization":
                endpoint["dst"]["label"] = "Ogranization"
    server = IntentServer()
    server.install()
    projects = ScriptedProjects()
    for backend in RELATIONAL_BACKENDS:
        projects.add(
            Project(
                id="{}-broken".format(backend.value),
                name="{}-broken".format(backend.value),
                database_type=backend,
                database_config=config(type=backend, graph_mapping=document),
            )
        )
    app.dependency_overrides[database_module.get_project_service] = lambda: projects
    app.dependency_overrides[database_module.verify_api_key_or_admin] = lambda: None
    with TestClient(app) as made:
        yield made, server
    app.dependency_overrides.pop(database_module.get_project_service, None)
    app.dependency_overrides.pop(database_module.verify_api_key_or_admin, None)
    server.uninstall()


@BACKENDS
def test_a_project_whose_mapping_will_not_load_refuses_with_the_loaders_own_problems(
    broken_stack, backend
):
    """
    Not a 500, and not an empty graph: the loader's sentence, at the route. The same text the
    two schema reads gate on ``valid`` answer with, so one broken document has one explanation.
    """
    made, server = broken_stack
    statement = "MATCH (n:`Person`)-[r]-(m) WHERE n.`person_id` IN [1001] RETURN n, r, m SKIP 0 LIMIT 10"
    reply = made.post(
        "/api/{}/{}/query".format(backend.value, "{}-broken".format(backend.value)),
        json={"query": statement},
    )
    answer = reply.json()

    assert reply.status_code == 200, reply.text
    assert answer["success"] is False and answer["data"] is None, answer
    assert "Ogranization" in answer["error"] and "edges[0]" in answer["error"], answer["error"]
    assert server.queries() == []
