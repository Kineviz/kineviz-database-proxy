# -*- coding: utf-8 -*-
"""
The LatticeDB driver, over a scripted worker.

The engine is replaced; everything above it is real -- the store probe reads real
header bytes, the schema comes from real sampling answers, and ``/expand`` goes
through the shared intent machinery and the real dialect. What is asserted is the
driver's side of the contract: which store it accepts, what it infers with no
catalog to read, and what it hands back for a graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kineviz_database_proxy.contract.intents import ExpandRequest, PullCategoryRequest
from kineviz_database_proxy.drivers import latticedb as latticedb_module
from kineviz_database_proxy.drivers.embedded.runtime import EngineRuntime
from kineviz_database_proxy.drivers.factory import DriverFactory
from kineviz_database_proxy.drivers.latticedb import LatticeDbDriver
from kineviz_database_proxy.models.project import DatabaseConfig, DatabaseType, Project
from kineviz_database_proxy.services.graph_schema_cache import GRAPH_SCHEMA_CACHE

#: A LatticeDB header: BDTL, then the format as a uint16, written twice.
LATTICE_HEADER = b"BDTL" + (3).to_bytes(2, "little") + (3).to_bytes(2, "little") + bytes(8)
KUZU_HEADER = b"KUZU" + (39).to_bytes(8, "little")

#: What latticedb 0.14.0 answers for each schema statement, keyed by a fragment of it.
SAMPLED = {
    "DISTINCT labels(n) SKIP": {
        "columns": ["labels(n)"],
        "rows": [[["Person"]], [["City"]]],
    },
    "MATCH (n:Person) RETURN properties(n)": {
        "columns": ["properties(n)"],
        "rows": [[{"name": "Alice", "age": 30}], [{"name": "Bob"}]],
    },
    "MATCH (n:City) RETURN properties(n)": {
        "columns": ["properties(n)"],
        "rows": [[{"name": "Portland"}]],
    },
    "DISTINCT labels(n), type(r), labels(m)": {
        "columns": ["labels(n)", "type(r)", "labels(m)"],
        "rows": [[["Person"], "LIVES_IN", ["City"]]],
    },
    "MATCH (n)-[r:LIVES_IN]->(m) RETURN properties(r)": {
        "columns": ["properties(r)"],
        "rows": [[{"since": 2020}]],
    },
}

#: One expand row, in the shape the dialect's projection asks for.
EXPAND_RESULT = {
    "columns": [
        "id(n)", "labels(n)", "properties(n)",
        "id(r)", "type(r)", "properties(r)", "r_src", "r_dst",
        "id(m)", "labels(m)", "properties(m)",
    ],
    "rows": [
        [1, ["Person"], {"name": "Alice"}, 1, "LIVES_IN", {"since": 2020}, 1, 2,
         2, ["City"], {"name": "Portland"}],
    ],
}


class ScriptedWorker:
    """Answers sampling statements from recorded rows and MATCHes with one graph row."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.alive = True
        self.statements = []

    async def request(self, operation, **payload):
        statement = payload.get("statement", "")
        self.statements.append(statement)
        for marker, result in SAMPLED.items():
            if marker in statement:
                return {"ok": True, "results": [dict(result, types=[], truncated=False)]}
        if statement.strip().upper().startswith("MATCH"):
            return {"ok": True, "results": [dict(EXPAND_RESULT, types=[], truncated=False)]}
        return {
            "ok": True,
            "results": [{"columns": ["ok"], "types": [], "rows": [[1]], "truncated": False}],
        }

    async def stop(self):
        self.alive = False


class ScriptedEngineService:
    def __init__(self):
        self.opened = []
        self.worker = None

    async def open_store(self, fingerprint, read_only=True, pin=None):
        self.opened.append((fingerprint.engine, fingerprint.storage_version, read_only, pin))
        runtime = EngineRuntime(
            engine=fingerprint.engine,
            version="0.14.0",
            root=Path("/engines"),
            python=Path("/python"),
        )
        self.worker = ScriptedWorker(runtime)
        return runtime, self.worker


@pytest.fixture(autouse=True)
def clean_schema_cache():
    GRAPH_SCHEMA_CACHE.clear()
    yield
    GRAPH_SCHEMA_CACHE.clear()


@pytest.fixture
def engine(monkeypatch):
    service = ScriptedEngineService()
    monkeypatch.setattr(latticedb_module, "ENGINE_SERVICE", service)
    return service


def make_project(tmp_path, header=LATTICE_HEADER, **config):
    store = tmp_path / "knowledge.db"
    store.write_bytes(header + bytes(32))
    return Project(
        id="p1",
        name="demo",
        database_type=DatabaseType.LATTICEDB,
        database_config=DatabaseConfig(
            type=DatabaseType.LATTICEDB, database_path=str(store), **config
        ),
    )


# -- the store ---------------------------------------------------------------


async def test_the_factory_builds_a_latticedb_driver(tmp_path):
    driver = DriverFactory.create_driver(make_project(tmp_path))
    assert isinstance(driver, LatticeDbDriver)


async def test_a_latticedb_store_opens_with_the_format_its_header_names(tmp_path, engine):
    driver = LatticeDbDriver(make_project(tmp_path))
    await driver.connect()
    assert engine.opened == [("latticedb", 3, True, None)]


async def test_a_kuzu_store_is_refused_rather_than_served(tmp_path, engine):
    """
    The Kuzu driver adopts a Ladybug file because either engine can read it and the
    dialects are identical. Nothing of the sort is true here: LatticeDB cannot open
    a Kuzu file, and these statements would mean nothing to Kuzu.
    """
    driver = LatticeDbDriver(make_project(tmp_path, header=KUZU_HEADER))

    with pytest.raises(ConnectionError) as error:
        await driver.connect()

    message = str(error.value)
    assert "Kuzu store" in message
    # It names the project type that would work, rather than only refusing.
    assert "'kuzu' project" in message
    assert engine.opened == []


async def test_a_project_with_no_path_says_what_it_needs(tmp_path):
    project = Project(
        id="p1",
        name="demo",
        database_type=DatabaseType.LATTICEDB,
        database_config=DatabaseConfig(type=DatabaseType.LATTICEDB),
    )
    with pytest.raises(ValueError):
        _ = LatticeDbDriver(project).store_path


async def test_a_writable_project_reports_write_without_changing_the_class_record(tmp_path):
    driver = LatticeDbDriver(make_project(tmp_path, read_only=False))
    assert driver.graph_capabilities.write is True
    assert LatticeDbDriver.graph_capabilities.write is False


# -- connection --------------------------------------------------------------


async def test_the_connection_probe_does_not_use_a_bare_return(tmp_path, engine):
    """
    ``RETURN 1`` cannot be planned here -- "could not create execution plan" -- so
    the probe unwinds a literal instead, which touches no data.
    """
    driver = LatticeDbDriver(make_project(tmp_path))
    assert await driver.test_connection() is True
    assert engine.worker.statements == ["UNWIND [1] AS ok RETURN ok"]


# -- schema ------------------------------------------------------------------


async def test_the_graph_schema_is_inferred_because_there_is_no_catalog(tmp_path, engine):
    driver = LatticeDbDriver(make_project(tmp_path))

    response = await driver.get_graph_schema()

    assert response.success
    assert [c.name for c in response.data.categories] == ["Person", "City"]
    assert response.data.categories[0].propsTypes == {"name": "STRING", "age": "INT64"}
    assert [(r.startCategory, r.name, r.endCategory) for r in response.data.relationships] == [
        ("Person", "LIVES_IN", "City")
    ]


async def test_the_inferred_categories_carry_no_keys(tmp_path, engine):
    """Identity is ``internal-id`` here, so expand never has to know a key property."""
    response = await LatticeDbDriver(make_project(tmp_path)).get_graph_schema()
    assert all(category.keys == [] for category in response.data.categories)


async def test_every_schema_statement_is_paged_at_the_source(tmp_path, engine):
    await LatticeDbDriver(make_project(tmp_path)).get_graph_schema()
    matches = [s for s in engine.worker.statements if s.startswith("MATCH")]
    assert matches and all("LIMIT" in statement for statement in matches)


async def test_the_table_schema_route_refuses_and_says_where_to_look(tmp_path, engine):
    """A LatticeDB store declares no tables, so there is no relational view to give."""
    response = await LatticeDbDriver(make_project(tmp_path)).get_schema()
    assert response.success is False
    assert "/graphSchema" in response.error


async def test_sample_data_reads_a_handful_of_nodes_per_category(tmp_path, engine):
    response = await LatticeDbDriver(make_project(tmp_path)).get_sample_data()

    assert response.success
    assert response.data["Person"] == [{"name": "Alice", "age": 30}, {"name": "Bob"}]
    assert response.data["City"] == [{"name": "Portland"}]


# -- results -----------------------------------------------------------------


async def test_a_projected_result_comes_back_as_a_graph(tmp_path, engine):
    driver = LatticeDbDriver(make_project(tmp_path))

    response = await driver.execute_query(
        "MATCH (n)-[r]->(m) RETURN id(n), labels(n), properties(n), id(r), type(r), "
        "properties(r), id(n) AS r_src, id(m) AS r_dst, id(m), labels(m), properties(m)"
    )

    assert response.success
    assert response.data.type == "GRAPH"
    assert [node.id for node in response.data.data.nodes] == ["1", "2"]
    edge = response.data.data.relationships[0]
    assert (edge.id, edge.type, edge.startNodeId, edge.endNodeId) == ("1", "LIVES_IN", "1", "2")


async def test_an_engine_error_is_reported_rather_than_raised(tmp_path, engine, monkeypatch):
    driver = LatticeDbDriver(make_project(tmp_path))
    await driver.connect()

    async def fail(operation, **payload):
        raise RuntimeError("Invalid token")

    monkeypatch.setattr(engine.worker, "request", fail)
    response = await driver.execute_query("MATCH (n RETURN n")

    assert response.success is False
    assert "Invalid token" in response.error


# -- intents -----------------------------------------------------------------


async def test_expand_selects_seeds_by_identity_and_names_each_edges_ends(tmp_path, engine):
    driver = LatticeDbDriver(make_project(tmp_path))

    data = await driver.expand(ExpandRequest(nodeIds=["1"], direction="from", limit=10))

    statement = engine.worker.statements[-1]
    assert "WHERE id(n) IN [1]" in statement
    assert "id(n) AS r_src, id(m) AS r_dst" in statement
    assert data.type == "GRAPH"
    assert data.data.relationships[0].startNodeId == "1"


async def test_an_undirected_expand_runs_one_statement_each_way(tmp_path, engine):
    driver = LatticeDbDriver(make_project(tmp_path))

    await driver.expand(ExpandRequest(nodeIds=["1"], direction="all", limit=10))

    matches = [s for s in engine.worker.statements if s.startswith("MATCH")]
    assert len(matches) == 2
    assert "(n)-[r]->(m)" in matches[0]
    assert "(n)<-[r]-(m)" in matches[1]


async def test_expand_needs_no_schema_round_trip_first(tmp_path, engine):
    """
    A ``primary-key`` backend has to load the schema before it can turn a node id
    into a predicate. This one does not, and must not pay for it.
    """
    driver = LatticeDbDriver(make_project(tmp_path))

    await driver.expand(ExpandRequest(nodeIds=["1"], direction="from", limit=10))

    assert not any("DISTINCT labels" in s for s in engine.worker.statements)


async def test_pull_category_pages_and_projects(tmp_path, engine):
    driver = LatticeDbDriver(make_project(tmp_path))

    await driver.pull_category(PullCategoryRequest(category="Person", limit=25, skip=50))

    statement = engine.worker.statements[-1]
    assert statement.startswith("MATCH (n:Person)")
    assert "RETURN id(n), labels(n), properties(n)" in statement
    assert statement.endswith("SKIP 50 LIMIT 25")


# -- api info ----------------------------------------------------------------


async def test_api_info_reports_the_release_actually_serving_the_project(tmp_path, engine):
    driver = LatticeDbDriver(make_project(tmp_path))
    await driver.connect()

    features = driver.get_api_info("demo")["features"]

    assert features["engine"] == "latticedb"
    assert features["engine_version"] == "0.14.0"
    assert features["embedded"] is True
    # Inferred, not declared, so there is no relational layout to advertise.
    assert features["table_schema"] is False
