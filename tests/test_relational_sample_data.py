"""
``GET /api/{backend}/{project}/sampleData`` pinned at the route for the three relational
engines — a read no test in this repo has ever made, for any backend.

The bounds themselves are pinned where they are built (``tests/test_relational_driver.py``),
the cell rules where a row is converted (``tests/test_relational_convert.py``) and the SQL
shapes per engine (``tests/test_relational_pipeline.py``). What was unpinned is the answer a
GraphXR schema panel actually receives: that the cap reaches every label *inside* the
statement it was handed rather than as a Python slice, that the projection is the mapping's
own columns so an unmapped blob is never fetched in order to be dropped, that the labels
sampled are exactly the labels declared, and that a mapping this proxy refused to load is
said out loud rather than answered as a 500 or as an empty sample.

Two invariants hold this surface together. The first is the repo's paging law: a sample of a
table somebody is only *looking at* must never become a scan of it. The second is item for
item with ``/schema`` and ``/graphSchema`` — the three reads a project makes before it has a
query have to agree about which labels exist, about which columns belong to each one, and
about whether this project is servable at all.
"""

from __future__ import annotations

import datetime as _datetime
import decimal
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from kineviz_database_proxy.api import database as database_module
from kineviz_database_proxy.drivers.relational import convert
from kineviz_database_proxy.drivers.relational import reads as reads_module
from kineviz_database_proxy.drivers.relational.driver import RelationalDriver
from kineviz_database_proxy.drivers.factory import DriverFactory
from kineviz_database_proxy.drivers.relational.driver_schema import SAMPLE_ROWS
from kineviz_database_proxy.main import app
from kineviz_database_proxy.models.project import DatabaseType, Project
from tests.test_relational_capability_record import RELATIONAL_BACKENDS, relational_project
from tests.test_relational_endpoints import FakeServer, ScriptedProjects
from tests.test_relational_pipeline import ENGINES
from tests.test_relational_pool import FakeEngine, FakeSpelling, access_for
from tests.test_relational_reflect import SPEC, FakeInspector, table as spec_table

#: One project per relational engine, so every case below runs against all three drivers.
BACKENDS = pytest.mark.parametrize(
    "backend", RELATIONAL_BACKENDS, ids=lambda one: one.value
)

#: The labels the shared spec's discovery draft maps, and the tables it never maps at all:
#: two link tables (``Employment``, ``Manager``) and three with no usable key.
MAPPED_LABELS = {"Order", "Organization", "Person", "Session", "TreeNode"}
UNSAMPLED_TABLES = ("Employment", "Manager", "Label", "Audit", "Badge")

class Opaque:
    """A DBAPI's own type: neither ``json`` nor the response model knows how to write it."""

    def __str__(self) -> str:
        return "opaque-cell"


#: One cell per column the shared spec maps, each a value a JSON response cannot carry as it
#: stands. The same values are pinned on the graph path in ``tests/test_relational_convert.py``;
#: what is new here is that they arrive through an HTTP route.
CELLS: Dict[str, Any] = {
    "order_id": decimal.Decimal("1.0000000000000000001"),
    "customer_id": decimal.Decimal("1000.50"),
    "total": decimal.Decimal("12.34"),
    "person_id": float("nan"),
    "full_name": _datetime.datetime(2024, 5, 17, 13, 45, 12, 123456),
    "org_id": 9007199254740993,
    "legal_name": bytearray(b"\x01\x02binary"),
    "token": uuid.UUID("12345678-1234-5678-1234-567812345678"),
    "node_id": Opaque(),
    "parent_id": None,
    "title": _datetime.date(2020, 1, 2),
}


def more_rows_than_the_cap(statement: Any) -> List[Dict[str, Any]]:
    """
    Every column the statement projected, answered ``SAMPLE_ROWS + 1`` times.

    A real server would stop at its cap and the fake does not, which is what makes the length
    of the answer evidence that the bound lives in the statement rather than in a slice of the
    rows afterwards.
    """
    row = {
        str(getattr(column, "name", "")): CELLS.get(str(getattr(column, "name", "")), "cell")
        for column in statement.selected_columns
    }
    return [dict(row) for _ in range(SAMPLE_ROWS + 1)]


class SampledServer(FakeServer):
    """
    ``FakeServer``'s three seams, with rows that answer themselves and a spelling per backend.

    The spellings are kept by name because one case has to move a knob the route reads —
    ``names_page_size`` — without replacing the wiring the driver builds itself. ``spelling``
    is patched as well as ``make_access`` because a route builds a fresh driver per request:
    setting the attribute on one instance would be gone before the statement is built.
    """

    def __init__(
        self, spec: Optional[Dict[str, Any]] = None, engine: Optional[FakeEngine] = None
    ) -> None:
        super().__init__()
        self.engine = engine or FakeEngine(row_factory=more_rows_than_the_cap)
        self.inspector = FakeInspector(spec or SPEC)
        self.spellings: Dict[str, Any] = {}
        self._spellings: Any = None

    def install(self) -> None:
        holder = self
        self._patch = patch.object(
            RelationalDriver,
            "make_access",
            lambda driver: access_for(
                driver.project,
                spelling=holder.spelling_for(driver.project.database_type.value),
                engine=holder.engine,
                inspector=holder.inspector,
            ),
        )
        self._spellings = patch.object(
            RelationalDriver,
            "spelling",
            new=property(lambda driver: holder.spelling_for(driver.project.database_type.value)),
        )
        self._patch.start()
        self._spellings.start()

    def uninstall(self) -> None:
        self._patch.stop()
        self._spellings.stop()

    def spelling_for(self, name: str) -> Any:
        found = self.spellings.get(name)
        if found is None:
            found = self.spellings[name] = FakeSpelling(name)
        return found

    def statements(self) -> List[Any]:
        """Every SELECT the fake engine was handed, in the order it was handed them."""
        return list(self.engine.selects)

    def forget(self) -> None:
        self.engine.selects.clear()


@contextmanager
def served(
    targets: List[Project],
    spec: Optional[Dict[str, Any]] = None,
    engine: Optional[FakeEngine] = None,
):
    """The generic read routes, over the projects and the seam a test names."""
    made = SampledServer(spec=spec, engine=engine)
    projects = ScriptedProjects()
    for one in targets:
        projects.add(one)
    app.dependency_overrides[database_module.get_project_service] = lambda: projects
    app.dependency_overrides[database_module.verify_api_key_or_admin] = lambda: None
    made.install()
    try:
        with TestClient(app) as client:
            yield client, made
    finally:
        made.uninstall()
        app.dependency_overrides.pop(database_module.get_project_service, None)
        app.dependency_overrides.pop(database_module.verify_api_key_or_admin, None)


@pytest.fixture
def route():
    """The three shipped relational projects, mapped from the shared spec."""
    with served([relational_project(one) for one in RELATIONAL_BACKENDS]) as (client, server):
        yield client, server


def url(backend: Any, route_name: str) -> str:
    return "/api/{}/{}{}".format(backend.value, backend.value, route_name)


def answer(client: Any, backend: Any, route_name: str) -> Dict[str, Any]:
    """A read route's envelope: 200, and ``success`` true, or the case fails on the error."""
    reply = client.get(url(backend, route_name))
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["success"] is True, body["error"]
    return body["data"]


def sample(client: Any, backend: Any) -> Dict[str, Any]:
    return answer(client, backend, "/sampleData")


def text_of(statement: Any) -> str:
    return str(statement).replace("\n", " ")


def bound(statement: Any) -> Dict[str, Any]:
    """The values bound *inside* the statement — where a page and a seed actually live."""
    return dict(statement.compile().params)


def remapped(backend: Any, graph_mapping: Dict[str, Any]) -> Project:
    """One of the shipped projects, its mapping replaced by the document a test names."""
    target = relational_project(backend)
    return target.model_copy(
        update={
            "database_config": target.database_config.model_copy(update={"graph_mapping": graph_mapping})
        }
    )


# ---------------------------------------------------------------------------
# the bound, at the source
# ---------------------------------------------------------------------------


@BACKENDS
def test_every_label_is_sampled_with_its_cap_inside_the_statement_it_was_handed(route, backend):
    """
    One statement per declared label, each carrying the cap as a bound value of its own.

    The number has to be in the statement the server was handed, because the alternative is
    reading the table: ``SAMPLE_ROWS`` is a look at the shape of a table, not an export of it.
    The fake answers more rows than the cap on purpose — a trim applied to the rows after the
    fact would cut this answer to ten while the statement asked for nothing.
    """
    client, server = route
    data = sample(client, backend)

    assert len(data) == len(MAPPED_LABELS)
    assert len(server.statements()) == len(MAPPED_LABELS), "one label is one statement"
    for statement in server.statements():
        assert "LIMIT :param_1" in text_of(statement), text_of(statement)
        assert bound(statement) == {"param_1": SAMPLE_ROWS}, bound(statement)
    assert len(data["Person"]) == SAMPLE_ROWS + 1, "the cap was applied in Python, not at the source"


#: How each engine's own grammar bounds one statement, rendered from the statement the driver
#: builds. ``LIMIT :param_1`` is only what the default dialect shows; SQL Server takes ``TOP``,
#: and an ``OFFSET`` without an ``ORDER BY`` would raise there rather than page.
CAP_TOKENS = {
    "mssql": "TOP __[POSTCOMPILE_param_1]",
    "mysql": "LIMIT %s",
    "postgresql": "LIMIT %(param_1)s::INTEGER",
}


@pytest.mark.parametrize("name", sorted(CAP_TOKENS))
async def test_the_cap_arrives_in_the_grammar_the_engine_itself_understands(name: str) -> None:
    """
    The driver's own read, rendered in the engine's own dialect, still bounds itself.

    The statement builder is shared code and only the spelling and the dialect change per
    case, which is the seam where a cap could stop being a cap: a ``TOP`` a refactor turns into
    an ``OFFSET`` costs SQL Server an exception before a row is read.
    """
    spelling, dialect, _ = ENGINES[name]
    target = relational_project(RELATIONAL_BACKENDS[0])
    driver = DriverFactory.create_driver(target)
    driver._spelling = spelling
    driver._access = access_for(
        target, spelling=spelling, engine=FakeEngine(), inspector=FakeInspector(SPEC)
    )
    mapping = await driver.effective_mapping()

    compiled = driver.sample_statement(mapping.nodes["Person"], SAMPLE_ROWS).compile(dialect=dialect)

    assert CAP_TOKENS[name].replace("::INTEGER", "") in str(compiled).replace("\n", " ").replace("::INTEGER", ""), str(compiled)
    assert dict(compiled.params) == {"param_1": SAMPLE_ROWS}


# ---------------------------------------------------------------------------
# the projection, and the closed set of labels
# ---------------------------------------------------------------------------


@BACKENDS
def test_a_column_the_mapping_left_out_is_never_fetched_in_order_to_be_dropped(backend):
    """
    The other half of a bounded read: the columns are the mapping's, not the table's.

    ``Person`` gains a ``payload`` the mapped document never named. A statement that selected
    the whole row would still answer ten rows and the panel would look fine — it would just be
    reading a blob out of a customer's database in order to throw it away.
    """
    wide = dict(SPEC)
    wide["Person"] = spec_table(
        [("person_id", "int", False), ("full_name", "varchar(100)", True), ("payload", "varbinary(max)", True)],
        ("PK_Person", ["person_id"]),
    )
    with served([relational_project(backend)], spec=wide) as (client, server):
        data = sample(client, backend)

    person = next(one for one in server.statements() if '"Person"' in text_of(one))
    assert "payload" not in text_of(person), text_of(person)
    assert "*" not in text_of(person), text_of(person)
    assert list(data["Person"][0]) == ["person_id", "full_name"]


@BACKENDS
def test_the_sampled_labels_are_the_labels_the_mapping_declares(route, backend):
    """
    Records only: a link table and a table that cannot be a record are never read.

    ``Employment`` and ``Manager`` are edges, and ``Label`` / ``Audit`` / ``Badge`` have no
    usable key, so none of them is a category — and a sample of a thing the canvas cannot name
    is a row-set nobody can point at.
    """
    client, server = route
    data = sample(client, backend)

    assert set(data) == MAPPED_LABELS
    asked = " ".join(text_of(one) for one in server.statements())
    for name in UNSAMPLED_TABLES:
        assert '"{}"'.format(name) not in asked, "{} was read without being declared".format(name)


# ---------------------------------------------------------------------------
# the cells
# ---------------------------------------------------------------------------


@BACKENDS
def test_every_cell_a_sample_answers_is_one_the_wire_can_carry(route, backend):
    """
    The rule is ``convert.json_safe``'s, and this is the route that sends it.

    A non-finite float is nothing rather than ``NaN`` (which is not JSON), bytes are base64
    (what the bolt drivers already send for a ``BYTEA``), a ``Decimal`` with more digits than a
    double has stays text, and one that merely carries a trailing zero goes out as the number.
    A type nothing knows goes out as text rather than as the object itself, which is what a
    response model typed ``Any`` would otherwise hand straight to the encoder.
    """
    client, server = route
    reply = client.get(url(backend, "/sampleData"))
    assert reply.status_code == 200, reply.text
    data = reply.json()["data"]

    assert data["Order"][0] == {"order_id": "1.0000000000000000001", "customer_id": 1000.5, "total": 12.34}
    assert data["TreeNode"][0]["node_id"] == "opaque-cell"
    assert data["Person"][0]["person_id"] is None
    assert data["Person"][0]["full_name"] == "2024-05-17T13:45:12.123456"
    assert data["TreeNode"][0]["title"] == "2020-01-02"
    assert data["Organization"][0]["legal_name"] == "AQJiaW5hcnk="
    assert data["Organization"][0]["org_id"] == 9007199254740993
    assert data["Session"][0]["token"] == "12345678-1234-5678-1234-567812345678"
    assert "NaN" not in reply.text and "Infinity" not in reply.text


def test_the_sample_applies_the_graph_path_s_cell_rule_and_not_a_second_one() -> None:
    """
    ``plain_cell`` delegates; it does not restate.

    A second converter on the sample path would round a ledger column in the one place nobody
    checks, and the two views of the same table would disagree about what a cell is.
    """
    for value in CELLS.values():
        assert reads_module.plain_cell(value) == convert.json_safe(value), value


async def test_a_sample_carries_nothing_json_cannot_hold_even_without_the_compile_stage():
    """
    The fallback is narrower than the rule and has to stay wire-safe anyway.

    ``reads.plain_cell`` falls back when the compile stage is absent — a build that can serve a
    schema but not a query. Bytes as ``str`` is not base64, but an object reaching the wire is
    a 500 on someone's schema panel, and that is the invariant this pins.
    """
    class NoStage:
        @staticmethod
        def json_safe(value: Any) -> Any:
            raise reads_module.PipelineMissing("convert is not available")

    backend = DatabaseType.MSSQL
    with served([relational_project(backend)]) as (client, server):
        with patch.object(reads_module, "PIPELINE", NoStage()):
            reply = client.get(url(backend, "/sampleData"))

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["success"] is True, body["error"]
    # A Decimal left to the response encoder would come back a float, which is the one
    # rounding nobody audits, so the digits have to survive as text with the stage gone too.
    assert body["data"]["Order"][0]["order_id"] == "1.0000000000000000001", body["data"]["Order"][0]
    assert body["data"]["Person"][0]["full_name"] == "2024-05-17T13:45:12.123456"
    for rows in body["data"].values():
        for cell in rows[0].values():
            assert cell is None or isinstance(cell, (bool, int, float, str)), type(cell)


# ---------------------------------------------------------------------------
# the refusals
# ---------------------------------------------------------------------------


@BACKENDS
def test_a_mapping_that_will_not_load_is_answered_with_the_loaders_reason(backend):
    """
    A document that does not parse says which field, and stays a response.

    ``GraphMapping.model_validate`` raises before anything is read, and the route has no
    exception wrapper of its own — so without ``_failure`` here a malformed file costs the
    client a 500 with no sentence in it, on a project they can still fix.
    """
    broken = remapped(backend, {"version": 1, "nodes": [{"label": 3}], "edges": "nope"})
    with served([broken]) as (client, server):
        reply = client.get(url(backend, "/sampleData"))

    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["success"] is False
    assert "validation error" in body["error"] and "nodes.0.label" in body["error"], body["error"]
    assert server.statements() == [], "a project that cannot load read a table anyway"


@BACKENDS
def test_a_mapping_that_loads_but_is_refused_refuses_every_sample(backend):
    """
    A refused name is a refusal, not an empty sample.

    ``Order Items`` is a legal *table* name and an illegal graph-side label, so the loader keeps
    that record out — and an answer of ``{}`` reads to a schema panel as a database with nothing
    in it, while the reason sits one field away in the document.
    """
    refused = remapped(
        backend,
        {"version": 1, "nodes": [{"label": "Order Items", "table": "Order", "key": "order_id", "include": True}], "edges": []},
    )
    with served([refused]) as (client, server):
        body = client.get(url(backend, "/sampleData")).json()

    assert body["success"] is False
    assert body["error"].startswith("nodes[0].label:"), body["error"]
    assert "not a legal node label" in body["error"]
    assert server.statements() == []


@BACKENDS
def test_an_engine_that_refuses_a_label_refuses_the_sample_and_stays_a_response(backend):
    """
    One denied table is the server's sentence, not an exception in the middle of a canvas.

    The loop samples label by label, so a failure partway through has to answer with what the
    engine said rather than the partial dict the labels before it produced.
    """
    denied = FakeEngine(row_factory=more_rows_than_the_cap, error="permission denied for table Person")
    with served([relational_project(backend)], engine=denied) as (client, server):
        body = client.get(url(backend, "/sampleData")).json()

    assert body["success"] is False
    assert body["error"] == "permission denied for table Person", body["error"]


# ---------------------------------------------------------------------------
# the two routes and the mapping they were built from
# ---------------------------------------------------------------------------


@BACKENDS
def test_the_sample_never_offers_a_property_the_schema_does_not(route, backend):
    """
    ``/graphSchema``, `/schema` and ``/sampleData`` are one mapping, answered three times.

    The props a category promises in the order a sample row carries them, name for name; every
    one of them a column the relational view names for that label's table (this spec's labels
    *are* its table names, which is why the two can be joined by name at all); and no category
    with no sample, which is a legend entry the canvas can never fill.
    """
    client, server = route
    data = sample(client, backend)
    tables = answer(client, backend, "/schema")
    graph = answer(client, backend, "/graphSchema")
    categories = {one["name"]: one for one in graph["categories"]}

    assert set(categories) == set(data), "a category nobody samples, or a sample of nothing"
    for label, rows in data.items():
        promised = categories[label]["props"]
        assert promised == list(rows[0]), "{}: {} vs {}".format(label, promised, list(rows[0]))
        assert set(promised) <= set(tables[label]), "{}: {}".format(label, set(promised) - set(tables[label]))
