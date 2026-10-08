"""
The reflection call count for one ``GET /api/{backend}/{project}/schema`` request.

A single ``/schema`` for a project that has a stored document walks the catalog twice:
``effective_mapping()`` runs ``with_key_types()`` to fill in the key type a document did
not declare (``"STRING"`` is the placeholder the loader writes when no schema is passed,
which is the normal case), and the name-page walk in ``get_schema()`` itself then
reflects the same names again to answer the route. The cost is real — every probe
(``get_multi_columns``, ``get_multi_pk_constraint``, ``get_multi_foreign_keys``,
``get_multi_unique_constraints``, ``get_multi_indexes``, plus the per-page row
counts) is paid twice on the hot path.

This file pins the total probe count for one request, so a refactor that merged the
two walks reports a lower number and a refactor that added a third one fails loudly.
A mutation test (extra fake probe call) is appended at the bottom to prove the
assertion is not a tautology of the spy.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Sequence
from unittest.mock import patch

from fastapi.testclient import TestClient

from graphxr_database_proxy.api import database as database_module
from graphxr_database_proxy.drivers.relational.driver import RelationalDriver
from graphxr_database_proxy.drivers.relational.pool import RelationalAccess
from graphxr_database_proxy.main import app
from graphxr_database_proxy.models.project import Project
from tests.test_relational_capability_record import (
    RELATIONAL_BACKENDS,
    relational_project,
)
from tests.test_relational_pool import FakeEngine, FakePool, FakeSpelling
from tests.test_relational_reflect import SPEC, FakeInspector

#: The five reflective probes the catalog walk asks for. ``row counts`` is the
#: per-page statistics call and is answered by the spelling, not the inspector, so it
#: is counted through the spelling's call counter instead.
REFLECTIVE_PROBES: tuple = ("columns", "pk", "fk", "unique", "indexes")


class CountingInspector(FakeInspector):
    """The fake inspector with a per-method call counter.

    Every reflective method logs into ``self.calls`` (the parent does too) and bumps
    its own counter, so a test can ask for the total of one method without filtering
    by name. ``get_table_names`` and ``get_view_names`` are the *name* probes, not
    reflective, and are excluded.
    """

    def __init__(self, spec: Dict[str, Any]) -> None:
        super().__init__(spec)
        self.probe_counts: Dict[str, int] = {name: 0 for name in REFLECTIVE_PROBES}

    def get_multi_columns(
        self, tables: Optional[Sequence[str]] = None, schema: Optional[str] = None
    ) -> Dict[str, Any]:
        self.probe_counts["columns"] += 1
        return super().get_multi_columns(tables=tables, schema=schema)

    def get_multi_pk_constraint(
        self, tables: Optional[Sequence[str]] = None, schema: Optional[str] = None
    ) -> Dict[str, Any]:
        self.probe_counts["pk"] += 1
        return super().get_multi_pk_constraint(tables=tables, schema=schema)

    def get_multi_foreign_keys(
        self, tables: Optional[Sequence[str]] = None, schema: Optional[str] = None
    ) -> Dict[str, Any]:
        self.probe_counts["fk"] += 1
        return super().get_multi_foreign_keys(tables=tables, schema=schema)

    def get_multi_unique_constraints(
        self, tables: Optional[Sequence[str]] = None, schema: Optional[str] = None
    ) -> Dict[str, Any]:
        self.probe_counts["unique"] += 1
        return super().get_multi_unique_constraints(tables=tables, schema=schema)

    def get_multi_indexes(
        self, tables: Optional[Sequence[str]] = None, schema: Optional[str] = None
    ) -> Dict[str, Any]:
        self.probe_counts["indexes"] += 1
        # The parent does not implement ``get_multi_indexes``; a route-level read is
        # still expected to *ask* for it, because that is what keeps the optional probe
        # from being dropped by a refactor that only saw the four the tests answer.
        return {}

    def total(self) -> int:
        return sum(self.probe_counts.values())


class CountingSpelling(FakeSpelling):
    """The fake spelling with a counter for the per-page row-count calls."""

    def __init__(self, backend: str = "mssql") -> None:
        super().__init__(backend)
        self.row_count_calls: int = 0

    def approx_row_counts(
        self, connection: Any, schema: Optional[str], tables: Sequence[str]
    ) -> Dict[str, int]:
        self.row_count_calls += 1
        return {}


class CountingServer:
    """The seams a relational route reaches, with per-probe counters.

    The seam is ``RelationalDriver.make_access`` so the production ``RelationalAccess``
    is what runs and pages — only the inspector, the pool and the engine are fakes. The
    spelling is patched as a property for the same reason ``SampledServer`` does it: a
    route builds a fresh driver per request, so an instance attribute is gone before
    the walk starts.
    """

    def __init__(self, spec: Dict[str, Any]) -> None:
        self.engine = FakeEngine()
        self.inspector = CountingInspector(spec)
        self.spellings: Dict[str, CountingSpelling] = {}
        self._patches: List[Any] = []

    def spelling_for(self, name: str) -> CountingSpelling:
        if name not in self.spellings:
            self.spellings[name] = CountingSpelling(name)
        return self.spellings[name]

    def install(self) -> None:
        holder = self

        def make_access(driver: RelationalDriver) -> RelationalAccess:
            spelling = holder.spelling_for(driver.project.database_type.value)
            pool = FakePool(holder.engine)
            return RelationalAccess(
                driver.project,
                spelling,
                pool=pool,
                inspector=lambda _engine: holder.inspector,
            )

        self._patches.append(patch.object(RelationalDriver, "make_access", make_access))
        self._patches.append(
            patch.object(
                RelationalDriver,
                "spelling",
                new=property(
                    lambda driver: holder.spelling_for(driver.project.database_type.value)
                ),
            )
        )
        for one in self._patches:
            one.start()

    def uninstall(self) -> None:
        while self._patches:
            self._patches.pop().stop()


class _ScriptedProjects:
    """The one method the route calls on the project service."""

    def __init__(self) -> None:
        self.by_name: Dict[str, Project] = {}

    def add(self, target: Project) -> None:
        self.by_name[target.name] = target

    async def get_project_by_name(self, name: str) -> Optional[Project]:
        return self.by_name.get(name)


@contextmanager
def served_schema(target: Project, spec: Dict[str, Any]):
    """One project, the counting seams, the real route — and nothing else on the wire."""
    server = CountingServer(spec)
    projects = _ScriptedProjects()
    projects.add(target)
    app.dependency_overrides[database_module.get_project_service] = lambda: projects
    app.dependency_overrides[database_module.verify_api_key_or_admin] = lambda: None
    server.install()
    try:
        with TestClient(app) as client:
            yield client, server
    finally:
        server.uninstall()
        app.dependency_overrides.pop(database_module.get_project_service, None)
        app.dependency_overrides.pop(database_module.verify_api_key_or_admin, None)


def schema_url(target: Project) -> str:
    return "/api/{}/{}/schema".format(target.database_type.value, target.name)


# ---------------------------------------------------------------------------
# the count, today
# ---------------------------------------------------------------------------


def test_one_schema_request_paid_each_probe_twice_on_a_draft_mapped_project() -> None:
    """
    Two reflection walks for one ``/schema`` — ``with_key_types`` and the name page.

    A draft document loads, every key is undeclared (the loader writes ``"STRING"`` when
    no schema is passed), so ``effective_mapping()`` reflects the mapping's own tables
    to read the key type, and the name-page walk in ``get_schema()`` reflects the same
    tables again. The total probe count is measured here at the inspector so a refactor
    that merged the two walks is forced to update this number.

    Measured today: **probes=10** (each of the 5 probes called exactly twice),
    **rows=2** (one per walk's page), total **12**. The two walks serve different
    purposes — ``with_key_types`` walks only tables whose keys lack a type,
    ``get_schema`` walks every mapping table — and they happen to coincide here only
    because the draft document leaves every key with the ``"STRING"`` placeholder the
    loader writes when no schema is passed. A manual override that declares all key
    types would have ``with_key_types`` walk a strict subset; a refactor that always
    walks the full set in ``effective_mapping`` would change the cost paid by
    ``/graphSchema``, ``/sampleData`` and the query path. The board's "Cost, not
    correctness" item therefore pins the count rather than merging the walks.
    """
    target = relational_project(RELATIONAL_BACKENDS[0])
    with served_schema(target, SPEC) as (client, server):
        reply = client.get(schema_url(target))

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["success"] is True, body["error"]

    probes = server.inspector.total()
    rows = sum(sp.row_count_calls for sp in server.spellings.values())
    assert probes == 10, (
        "today's count for one /schema is 10 probes; got probes={}, rows={}, total={}; "
        "if this changes, one walk was added or merged, and a reviewer has to ask "
        "whether /schema, /graphSchema, /sampleData and the query path are all still "
        "the same".format(probes, rows, probes + rows)
    )
    assert rows == 2, (
        "row counts were asked {} times; the page-by-page walk calls the spelling once "
        "per page, and one /schema is two pages today".format(rows)
    )


# ---------------------------------------------------------------------------
# the mutation: a refactor that quietly adds a third walk is caught here
# ---------------------------------------------------------------------------


def test_an_extra_probe_call_breaks_the_count() -> None:
    """
    A walk that grew by one probe should fail this count, not pass it.

    The board item names the second walk as the cost: the call count assertion above
    is exactly twice the per-walk cost, so a refactor that adds a third walk reports
    a higher number. This test installs a one-shot extra probe call and proves the
    assertion above does not absorb it.
    """
    target = relational_project(RELATIONAL_BACKENDS[0])
    with served_schema(target, SPEC) as (client, server):
        # One extra call to ``get_multi_columns`` against the inspector before the
        # request is made: a refactor that added a third walk would do the same.
        server.inspector.get_multi_columns(tables=["Person"])
        reply = client.get(schema_url(target))

    assert reply.status_code == 200, reply.text
    probes = server.inspector.total()
    assert probes != 10, (
        "the extra probe was absorbed: probes={}; the count above is a tautology of "
        "the spy and the cost is unpinned".format(probes)
    )
