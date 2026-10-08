"""
``POST /api/relational/{project}/preview`` pinned at the route for the three
relational backends, including the three upstream changes the recent round
made and the closed-set guards the route's contract promises.

The route is the only place a person sees what their document actually means,
so it must answer a verdict and a row count even when the document is broken
upstream of the bind: ``compile._require_bindable_key`` raises for a column
type the engine has no name for, the dialect's ``column_type`` answers ``None``
for an array-shaped declaration, and a ``NULL`` key marker triggers the bounded
catalog read that may or may not fill the type in. None of those is the
table the pair is about, so the route must not refuse a working pair on a
working table just because some other record in the same document is broken.

The moved cases are re-styled from ``tests/test_relational_endpoints.py``'s
three original ``/preview`` cases (the verdict, the empty-direction, and the
pair-index-alone refuses its own field), now alongside the four new case
families. The mutations live in the isolated tree the gate runs in.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from graphxr_database_proxy.api.auth import verify_admin_token
from graphxr_database_proxy.api.relational import get_project_service
from graphxr_database_proxy.drivers.relational.driver import RelationalDriver
from graphxr_database_proxy.drivers.relational.reads import PREVIEW_ROWS_MAX, VERDICTS
from graphxr_database_proxy.main import app
from graphxr_database_proxy.models.project import DatabaseType, Project
from tests.test_relational_capability_record import RELATIONAL_BACKENDS, relational_project
from tests.test_relational_endpoints import (  # noqa: F401  (fixtures, used by the moved cases)
    ScriptedProjects,
    client,
    projects,
    server,
)
from tests.test_relational_pool import FakeEngine
from tests.test_relational_sample_data import SampledServer, remapped

#: Every relational engine this proxy serves, id'd by its enum value. The
#: same shape ``tests/test_relational_placement.py`` uses, so a failure on
#: one backend is the failure the test is about and not a missing fixture.
BACKENDS = pytest.mark.parametrize(
    "backend", RELATIONAL_BACKENDS, ids=lambda one: one.value
)

#: The pair the moved cases ask the route about: ``Order -> Person`` from a
#: record order's foreign key, the canonical "both directions join" pair.
ORDERED = {
    "src": {"label": "Order", "column": "order_id", "column_type": "int"},
    "dst": {"label": "Person", "column": "customer_id", "column_type": "int"},
}

#: The pair the per-backend tests ask about: ``Employment`` between ``Person``
#: and ``Organization``, the canonical "neither nor both" pair — the joined
#: table has a key of its own, so an expand on it is not a same-label link.
EMPLOYMENT_PAIR = {
    "src": {"label": "Person", "column": "person_id", "column_type": "int"},
    "dst": {"label": "Organization", "column": "org_id", "column_type": "bigint"},
}

#: The four words the route's ``verdict`` field is allowed to be, as the spec
#: spells them. Pinned here so a future change to ``VERDICTS`` that drops or
#: adds a value fails at the test layer rather than at the page.
CLOSED_VERDICTS = frozenset({"settled", "backwards", "you-decide", "neither"})

#: The one row the fake engine answers for the two preview directions. The
#: cap test below pins the value the statement carries, not what the fake
#: returns, so the count is at most the cap and the bound is the cap.
ONE_ROW: Dict[str, Any] = {"from": 1, "to": 2, "from_label": "A", "to_label": "B"}
TWO_DIRECTIONS: List[List[Dict[str, Any]]] = [[ONE_ROW], [ONE_ROW]]


def preview_url(backend: DatabaseType) -> str:
    """The URL ``/preview`` lives at, one per backend's project name."""
    return "/api/relational/{}/preview".format(backend.value)


@contextmanager
def per_backend_route(
    document: Optional[Dict[str, Any]] = None,
    engine: Optional[FakeEngine] = None,
):
    """
    The ``/preview`` route, over the three shipped backends with the given document.

    The route is reached behind the admin token, with no live config, and the
    seam a relational driver reads through is patched to a fake engine and a
    fake catalog. ``document``, when given, replaces the one discovery proposes
    on every backend, so a test can ask the route about a pair against a
    mapping the upstream paths have already broken.

    Yields the ``TestClient`` and the ``SampledServer`` it reads through, so a
    test can set the engine's row script for the two preview directions.
    """
    targets: List[Project] = []
    for backend in RELATIONAL_BACKENDS:
        if document is not None:
            targets.append(remapped(backend, document))
        else:
            targets.append(relational_project(backend))
    made = SampledServer(engine=engine or FakeEngine())
    scripted = ScriptedProjects()
    for one in targets:
        scripted.add(one)
    app.dependency_overrides[get_project_service] = lambda: scripted
    app.dependency_overrides[verify_admin_token] = lambda: None
    made.install()
    try:
        with TestClient(app) as client:
            yield client, made
    finally:
        made.uninstall()
        app.dependency_overrides.pop(get_project_service, None)
        app.dependency_overrides.pop(verify_admin_token, None)


# ---------------------------------------------------------------------------
# the moved cases, re-styled from tests/test_relational_endpoints.py:403-456
# ---------------------------------------------------------------------------


def test_a_pair_that_joins_in_both_directions_settles_as_you_decide(
    client: Any, projects: Any, server: Any
) -> None:
    """
    One call, both directions, and the four words the editor has labels for.

    ``you-decide`` is the database declining to settle it — both readings join
    rows — and the word arrives as the spec spells it, not as one of its own
    synonyms. ``pair`` is echoed only when the caller sent an index, so the
    editor's address for the pair survives the round trip.
    """
    server.engine.script = [[{"from": 1, "to": 2}], [{"from": 1, "to": 2}]]

    answer = client.post(
        "/api/relational/mapped/preview",
        json={"edge": "FK_ORDER_CUSTOMER", "table": "Order", "pair": 0, **ORDERED},
    ).json()

    assert answer["verdict"] == "you-decide"
    assert answer["verdict"] in VERDICTS
    assert answer["pair"] == 0 and answer["table"] == "Order"
    assert answer["forwards"]["count"] == 1 and answer["backwards"]["count"] == 1


def test_an_empty_join_in_either_direction_is_neither_and_never_a_success(
    client: Any, projects: Any, server: Any
) -> None:
    """
    The case this route exists for: nothing joined, in either direction.

    An empty result is a finding, not an absence of one, and reading it as
    ``settled`` is how a backwards link ends up drawing a canvas of nothing.
    Both sides empty is ``neither`` — never ``settled``, which would put a full
    canvas of nothing on the screen and let a person conclude their link is
    fine.
    """
    server.engine.script = [[], []]

    answer = client.post(
        "/api/relational/mapped/preview",
        json={"edge": "WORKS_FOR", "table": "Employment", "pair": 0,
              "src": EMPLOYMENT_PAIR["src"], "dst": EMPLOYMENT_PAIR["dst"]},
    ).json()

    assert answer["verdict"] == "neither"
    assert answer["forwards"]["rows"] == [] and answer["backwards"]["count"] == 0


def test_a_request_that_names_no_column_is_refused_on_its_own_field(
    client: Any, projects: Any, server: Any
) -> None:
    """
    A pair *index* alone names nothing, and a join cannot run on nothing.

    The editor holds the columns; a caller that sends only the index would
    otherwise get a refusal about its mapping when the request itself is
    what was incomplete. 422 with the path of the control that was left
    empty — the same shape a rejected mapping upload uses.
    """
    answer = client.post(
        "/api/relational/mapped/preview",
        json={"edge": "WORKS_FOR", "table": "Employment", "pair": 1},
    )

    assert answer.status_code == 422
    assert answer.json()["detail"][0]["path"] == "pair"


# ---------------------------------------------------------------------------
# the three upstream changes: the route answers even when the document is broken
# ---------------------------------------------------------------------------


def _with_extra_node(base: Dict[str, Any], extra_node: Dict[str, Any]) -> Dict[str, Any]:
    """The shared spec's draft, with one extra broken node grafted on.

    The breakage is on a *different* table than the one the pair is about, so
    the route's per-pair statement build never reaches the broken column. The
    new table is one the catalog does not have, so the key type falls through
    to whatever the document's ``properties`` map declares.
    """
    document = dict(base)
    document["nodes"] = list(base.get("nodes", [])) + [extra_node]
    return document


@BACKENDS
def test_a_document_with_a_declared_array_still_answers_an_unrelated_pair(
    client: Any, server: Any, backend: DatabaseType
) -> None:
    """
    R8 (``571777b``): an array-shaped declaration is refused by every engine's
    ``column_type`` — SQL Server has no array column, PostgreSQL's ``text[] =
    text`` is a binder error, MySQL has no array column either. The refusal
    lives upstream of the bind, in the dialect, on a *property* the route's
    preview statement never reaches.

    The route's ``preview_statement`` builds for the table it was asked
    about, not for the document as a whole, so a pair on a working table is
    unaffected. This is the case a person hits when they have a typed-array
    property on a record they are not previewing.
    """
    base = relational_project(backend).database_config.graph_mapping
    document = _with_extra_node(
        base,
        {"label": "ArrayNode", "table": "Person", "key": "person_id",
         "include": True,
         "properties": {"person_id": "int[]"}},
    )
    with per_backend_route(document=document) as (client, made):
        made.engine.script = TWO_DIRECTIONS
        answer = client.post(
            preview_url(backend),
            json={"edge": "WORKS_FOR", "table": "Employment", "pair": 0,
                  "limit": PREVIEW_ROWS_MAX, **EMPLOYMENT_PAIR},
        ).json()

    assert answer["verdict"] in VERDICTS, answer
    assert answer["forwards"]["count"] <= PREVIEW_ROWS_MAX, answer
    assert answer["backwards"]["count"] <= PREVIEW_ROWS_MAX, answer


@BACKENDS
def test_a_document_with_a_geometry_key_still_answers_an_unrelated_pair(
    client: Any, server: Any, backend: DatabaseType
) -> None:
    """
    C1 (``7efc50f``): a real-name type the engine cannot bind (``geometry``,
    and the like) raises ``UnbindableKey`` in ``compile._require_bindable_key``
    when that specific record is queried. The split keeps the marker case
    (blank / ``STRING`` / ``NULL``) and the real-name case distinguishable
    in the message a person reads, so the fix each one points at is named.

    The refusal is per-label: a record on a different table is not in the
    statement the pair builds, so a working pair on a working table is
    unaffected. The new table the broken node sits on is not in the catalog,
    so the key type falls through to the document's ``properties`` map, where
    ``geometry`` lands as a real name and would be refused if the route
    reached it.
    """
    base = relational_project(backend).database_config.graph_mapping
    document = _with_extra_node(
        base,
        {"label": "GeoNode", "table": "NewGeoTable", "key": "geo_id",
         "include": True,
         "properties": {"geo_id": "geometry"}},
    )
    with per_backend_route(document=document) as (client, made):
        made.engine.script = TWO_DIRECTIONS
        answer = client.post(
            preview_url(backend),
            json={"edge": "WORKS_FOR", "table": "Employment", "pair": 0,
                  "limit": PREVIEW_ROWS_MAX, **EMPLOYMENT_PAIR},
        ).json()

    assert answer["verdict"] in VERDICTS, answer
    assert answer["forwards"]["count"] <= PREVIEW_ROWS_MAX, answer
    assert answer["backwards"]["count"] <= PREVIEW_ROWS_MAX, answer


@BACKENDS
def test_a_document_with_a_null_marker_on_a_key_still_answers_an_unrelated_pair(
    client: Any, server: Any, backend: DatabaseType
) -> None:
    """
    M5 (``92c8cdb``): a ``NULL`` marker on a key column triggers the bounded
    catalog read in ``with_key_types`` (``_key_unnamed`` and ``names_a_type``
    now agree on what a marker looks like). The read either fills in a real
    type or leaves the marker in place — the *honest re-read* the commit
    names, not a new outcome.

    The marker fires per-label too: a record on a different table is not
    reached, so a working pair on a working table is unaffected. The new
    table the broken node sits on is not in the catalog, so the catalog
    re-read has nothing to add and the marker survives as ``NULL``.
    """
    base = relational_project(backend).database_config.graph_mapping
    document = _with_extra_node(
        base,
        {"label": "NullNode", "table": "NewNullTable", "key": "null_id",
         "include": True,
         "properties": {"null_id": "NULL"}},
    )
    with per_backend_route(document=document) as (client, made):
        made.engine.script = TWO_DIRECTIONS
        answer = client.post(
            preview_url(backend),
            json={"edge": "WORKS_FOR", "table": "Employment", "pair": 0,
                  "limit": PREVIEW_ROWS_MAX, **EMPLOYMENT_PAIR},
        ).json()

    assert answer["verdict"] in VERDICTS, answer
    assert answer["forwards"]["count"] <= PREVIEW_ROWS_MAX, answer
    assert answer["backwards"]["count"] <= PREVIEW_ROWS_MAX, answer


# ---------------------------------------------------------------------------
# the closed set: the verdict is a member, pinned by set equality
# ---------------------------------------------------------------------------


@BACKENDS
def test_the_verdict_is_a_member_of_VERDICTS_pinned_by_set_equality(
    client: Any, server: Any, backend: DatabaseType
) -> None:
    """
    The route's verdict is a member of the closed set ``VERDICTS`` from
    ``drivers/relational/reads.py``, pinned here by set equality rather than
    ``in``. A new verdict that is not in the closed set must be caught at
    the test layer: an ``in`` check would let a fifth value slip through
    silently, where the page would render it as ``—`` and the person would
    not know which way round the link went.
    """
    with per_backend_route() as (client, made):
        made.engine.script = TWO_DIRECTIONS
        answer = client.post(
            preview_url(backend),
            json={"edge": "WORKS_FOR", "table": "Employment", "pair": 0, **EMPLOYMENT_PAIR},
        ).json()

    # The answer is a member of the closed set.
    assert set([answer["verdict"]]) <= set(VERDICTS), (answer["verdict"], VERDICTS)
    # The closed set is exactly the four words the spec defines — a future
    # verdict that is not in the set fails here, not at the route.
    assert set(VERDICTS) == CLOSED_VERDICTS, set(VERDICTS)


# ---------------------------------------------------------------------------
# the cap: PREVIEW_ROWS_MAX lives in the statement
# ---------------------------------------------------------------------------


def _preview_statements(made: SampledServer) -> List[Any]:
    """The two SELECTs the route handed the engine for one preview."""
    return [stmt for stmt in made.engine.selects if "Employment" in str(stmt)]


@BACKENDS
def test_the_preview_cap_lives_in_the_statement_at_PREVIEW_ROWS_MAX(
    client: Any, server: Any, backend: DatabaseType
) -> None:
    """
    The cap lives in the statement, not in a Python slice.

    A real database would honour the ``LIMIT`` (or ``TOP`` on SQL Server),
    so a response with more than ``PREVIEW_ROWS_MAX`` rows could not arrive
    on the wire. Removing the cap, or moving it to a Python slice after the
    server has handed over its answer, would let a count > 100 through — and
    the route's contract forbids a count > 100. The bound value the statement
    carries is the cap; the assertion pins it, not the fake's row count.
    """
    with per_backend_route() as (client, made):
        made.engine.script = TWO_DIRECTIONS
        answer = client.post(
            preview_url(backend),
            json={"edge": "WORKS_FOR", "table": "Employment", "pair": 0,
                  "limit": PREVIEW_ROWS_MAX, **EMPLOYMENT_PAIR},
        ).json()

    # The response honours the cap on the wire.
    assert answer["forwards"]["count"] <= PREVIEW_ROWS_MAX, answer
    assert answer["backwards"]["count"] <= PREVIEW_ROWS_MAX, answer

    # The cap lives in the statement as a bound value, one per direction:
    # a real database would only return the bound number of rows, so a count
    # > PREVIEW_ROWS_MAX could not arrive. The two preview statements are
    # one for each direction. The bound is pinned to the literal 100 — the
    # ``PREVIEW_ROWS_MAX = 100`` the route's contract promises — so a future
    # change to the constant is caught here rather than at the route.
    preview_stmts = _preview_statements(made)
    assert len(preview_stmts) == 2, [str(s) for s in preview_stmts]
    for stmt in preview_stmts:
        params = dict(stmt.compile().params)
        assert params.get("param_1") == 100, (str(stmt), params)
