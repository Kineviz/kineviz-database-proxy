# -*- coding: utf-8 -*-
"""
The Kuzu and Ladybug drivers, over a scripted worker.

The engine is replaced; everything above it is real -- the store probe reads real
header bytes, the schema comes from real catalog rows, and ``/expand`` goes through
the shared intent machinery and the real dialect. What is asserted is the driver's
side of the contract: which store it accepts, what it builds from the catalog, and
what it hands back for a graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kineviz_database_proxy.contract.intents import ExpandRequest, PullCategoryRequest
from kineviz_database_proxy.drivers import kuzu as kuzu_module
from kineviz_database_proxy.drivers.embedded.runtime import EngineRuntime
from kineviz_database_proxy.drivers.factory import DriverFactory
from kineviz_database_proxy.drivers.kuzu import KuzuDriver
from kineviz_database_proxy.drivers.ladybug import LadybugDriver
from kineviz_database_proxy.models.project import DatabaseConfig, DatabaseType, Project
from kineviz_database_proxy.services.graph_schema_cache import GRAPH_SCHEMA_CACHE

KUZU_HEADER = b"KUZU" + (39).to_bytes(8, "little")
LADYBUG_HEADER = b"LBUG" + (43).to_bytes(8, "little")

CATALOG = {
    "show_tables": {
        "columns": ["id", "name", "type", "database name", "comment"],
        "rows": [
            [0, "Person", "NODE", "local(kuzu)", ""],
            [1, "City", "NODE", "local(kuzu)", ""],
            [3, "LivesIn", "REL", "local(kuzu)", ""],
        ],
    },
    "table_info('Person')": {
        "columns": ["property id", "name", "type", "default expression", "primary key"],
        "rows": [[0, "name", "STRING", "NULL", True], [1, "age", "INT64", "NULL", False]],
    },
    "table_info('City')": {
        "columns": ["property id", "name", "type", "default expression", "primary key"],
        "rows": [[0, "cid", "INT64", "NULL", True], [1, "name", "STRING", "NULL", False]],
    },
    "table_info('LivesIn')": {
        "columns": ["property id", "name", "type", "default expression", "primary key"],
        "rows": [[0, "since", "INT64", "NULL", False]],
    },
    "show_connection('LivesIn')": {
        "columns": [
            "source table name",
            "destination table name",
            "source table primary key",
            "destination table primary key",
        ],
        "rows": [["Person", "City", "name", "cid"]],
    },
}

GRAPH_ROW = [
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


class ScriptedWorker:
    """Answers catalog statements from recorded rows and MATCHes with one graph row."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.alive = True
        self.statements = []
        self.stopped = False

    async def request(self, operation, **payload):
        statement = payload.get("statement", "")
        self.statements.append(statement)
        for marker, result in CATALOG.items():
            if marker in statement:
                return {"ok": True, "results": [dict(result, truncated=False)]}
        if statement.strip().upper().startswith("MATCH"):
            return {
                "ok": True,
                "results": [
                    {"columns": ["n", "r", "m"], "types": [], "rows": [GRAPH_ROW], "truncated": False}
                ],
            }
        return {
            "ok": True,
            "results": [{"columns": ["ok"], "types": ["INT64"], "rows": [[1]], "truncated": False}],
        }

    async def stop(self):
        self.stopped = True
        self.alive = False


class ScriptedEngineService:
    def __init__(self):
        self.opened = []
        self.worker = None

    async def open_store(self, fingerprint, read_only=True, pin=None):
        self.opened.append((fingerprint.engine, fingerprint.storage_version, read_only, pin))
        runtime = EngineRuntime(
            engine=fingerprint.engine,
            version="9.9.9",
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
    monkeypatch.setattr(kuzu_module, "ENGINE_SERVICE", service)
    return service


def make_project(tmp_path, header=KUZU_HEADER, database_type="kuzu", **config):
    store = tmp_path / "graph.kz"
    store.write_bytes(header + b"\x00" * 32)
    return Project(
        id="p1",
        name="demo",
        database_type=DatabaseType(database_type),
        database_config=DatabaseConfig(
            type=DatabaseType(database_type), database_path=str(store), **config
        ),
    )


# -- accepting a store ------------------------------------------------------


async def test_a_kuzu_store_is_opened_with_the_build_its_header_names(tmp_path, engine):
    driver = KuzuDriver(make_project(tmp_path))
    await driver.connect()

    assert engine.opened == [("kuzu", 39, True, None)]


async def test_a_ladybug_store_reaches_the_ladybug_driver(tmp_path, engine):
    driver = LadybugDriver(make_project(tmp_path, LADYBUG_HEADER, "ladybug"))
    await driver.connect()

    assert engine.opened == [("ladybug", 43, True, None)]


async def test_a_kuzu_project_holding_a_ladybug_store_is_served_by_ladybug(tmp_path, engine):
    # The two families are one codebase with two names, so the file decides the
    # engine and the project type only decides the route. Refusing here would turn a
    # store the proxy can serve perfectly well into a configuration error.
    driver = KuzuDriver(make_project(tmp_path, LADYBUG_HEADER))
    await driver.connect()

    assert engine.opened == [("ladybug", 43, True, None)]
    assert driver.engine_in_use == "ladybug"


async def test_a_ladybug_project_holding_a_kuzu_store_is_served_by_kuzu(tmp_path, engine):
    driver = LadybugDriver(make_project(tmp_path, KUZU_HEADER, "ladybug"))
    await driver.connect()

    assert engine.opened == [("kuzu", 39, True, None)]
    assert driver.engine_in_use == "kuzu"


async def test_the_substituted_engine_is_reported_rather_than_hidden(tmp_path, engine):
    driver = KuzuDriver(make_project(tmp_path, LADYBUG_HEADER))
    await driver.connect()

    features = driver.get_api_info("demo")["features"]
    assert features["engine"] == "ladybug"
    assert features["engine_version"] == "9.9.9"
    # The route, and therefore the capability record, stays the project's own type.
    assert driver.get_api_info("demo")["type"] == "kuzu"


async def test_a_pin_is_dropped_when_the_file_turns_out_to_be_the_other_family(
    tmp_path, engine
):
    # "0.11" is a Kuzu release line; there is no Ladybug 0.11, so carrying the pin
    # across would turn a working store into a failed install.
    driver = KuzuDriver(make_project(tmp_path, LADYBUG_HEADER, engine_version="0.11"))
    await driver.connect()

    assert engine.opened[0][3] is None


async def test_a_missing_store_reports_the_path(tmp_path, engine):
    project = make_project(tmp_path)
    project.database_config.database_path = str(tmp_path / "absent.kz")
    driver = KuzuDriver(project)

    with pytest.raises(ConnectionError, match="absent.kz"):
        await driver.connect()


def test_a_project_with_no_path_says_which_field_is_missing(tmp_path):
    project = make_project(tmp_path)
    project.database_config.database_path = None

    with pytest.raises(ValueError, match="database_path"):
        _ = KuzuDriver(project).store_path


async def test_a_version_pin_is_passed_through(tmp_path, engine):
    driver = KuzuDriver(make_project(tmp_path, engine_version="0.11"))
    await driver.connect()

    assert engine.opened[0][3] == "0.11"


async def test_disconnect_leaves_the_pooled_engine_running(tmp_path, engine):
    # The pool owns the process and the API rebuilds the driver on every request;
    # stopping it here would put an engine import in front of every call.
    driver = KuzuDriver(make_project(tmp_path))
    await driver.connect()
    worker = engine.worker

    await driver.disconnect()

    assert worker.stopped is False


# -- read-only --------------------------------------------------------------


def test_a_project_is_read_only_and_declares_no_write_capability(tmp_path):
    driver = KuzuDriver(make_project(tmp_path))
    assert driver.read_only is True
    assert driver.graph_capabilities.write is False


def test_a_writable_project_declares_it_without_changing_the_class_record(tmp_path):
    driver = KuzuDriver(make_project(tmp_path, read_only=False))

    assert driver.graph_capabilities.write is True
    # The class attribute is what the conformance tests read and what the record
    # means in general; only this project is writable.
    assert KuzuDriver.graph_capabilities.write is False


# -- schema -----------------------------------------------------------------


async def test_the_graph_schema_is_built_from_the_catalog(tmp_path, engine):
    driver = KuzuDriver(make_project(tmp_path))
    response = await driver.get_graph_schema()

    assert response.success
    assert [c.name for c in response.data.categories] == ["Person", "City"]
    person = response.data.categories[0]
    assert person.keys == ["name"]
    (lives_in,) = response.data.relationships
    assert (lives_in.startCategory, lives_in.endCategory) == ("Person", "City")


async def test_the_relational_schema_is_answered_rather_than_refused(tmp_path, engine):
    # The bolt drivers return an error here because there is nothing declared to
    # report; a Kuzu table is declared, so this is real.
    driver = KuzuDriver(make_project(tmp_path))
    response = await driver.get_schema()

    assert response.success
    assert response.data["Person"] == {"name": "STRING", "age": "INT64"}


async def test_sample_data_returns_properties_per_category(tmp_path, engine):
    driver = KuzuDriver(make_project(tmp_path))
    response = await driver.get_sample_data()

    assert response.success
    # One entry per category, holding plain property dicts. The scripted worker
    # answers every MATCH with the same row, so both categories see both nodes.
    assert set(response.data) == {"Person", "City"}
    assert {"name": "Alice", "age": 30} in response.data["Person"]
    assert all(isinstance(row, dict) for row in response.data["City"])


# -- queries ----------------------------------------------------------------


async def test_a_graph_result_carries_label_key_ids(tmp_path, engine):
    driver = KuzuDriver(make_project(tmp_path))
    response = await driver.execute_query("MATCH (n)-[r]->(m) RETURN n, r, m")

    assert response.success
    assert response.data.type == "GRAPH"
    assert [n.id for n in response.data.data.nodes] == ["Person:Alice", "City:1"]
    edge = response.data.data.relationships[0]
    assert (edge.startNodeId, edge.endNodeId) == ("Person:Alice", "City:1")


async def test_mapping_a_graph_loads_the_schema_so_ids_are_keys_not_offsets(tmp_path, engine):
    driver = KuzuDriver(make_project(tmp_path))
    await driver.execute_query("MATCH (n) RETURN n")

    # Without the schema the ids would fall back to internal offsets, which no
    # predicate can match; the driver loads it on the first graph result.
    assert any("show_tables" in statement for statement in engine.worker.statements)


async def test_an_uncapped_statement_is_capped(tmp_path, engine):
    driver = KuzuDriver(make_project(tmp_path))
    await driver.execute_query("MATCH (n) RETURN n")

    assert any(statement.endswith("LIMIT 20000") for statement in engine.worker.statements)


async def test_a_statement_that_caps_itself_is_left_alone(tmp_path, engine):
    driver = KuzuDriver(make_project(tmp_path))
    await driver.execute_query("MATCH (n) RETURN n LIMIT 5")

    assert "MATCH (n) RETURN n LIMIT 5" in engine.worker.statements


async def test_an_engine_error_is_returned_rather_than_raised(tmp_path, engine, monkeypatch):
    driver = KuzuDriver(make_project(tmp_path))
    await driver.connect()

    async def broken(operation, **payload):
        raise kuzu_module.EngineWorkerError("Parser exception: nope")

    monkeypatch.setattr(engine.worker, "request", broken)
    response = await driver.execute_query("NONSENSE")

    assert response.success is False
    assert "Parser exception" in response.error


# -- intents ----------------------------------------------------------------


async def test_expand_resolves_a_label_key_seed_through_the_real_dialect(tmp_path, engine):
    driver = KuzuDriver(make_project(tmp_path))
    await driver.connect()

    data = await driver.expand(ExpandRequest(nodeIds=["Person:Alice"], direction="all", limit=25))

    expand_statements = [s for s in engine.worker.statements if "Alice" in s]
    assert expand_statements == [
        'MATCH (n:`Person`)-[r]-(m) WHERE n.`name` IN ["Alice"] RETURN n, r, m SKIP 0 LIMIT 25'
    ]
    assert [n.id for n in data.data.nodes] == ["Person:Alice", "City:1"]


async def test_expand_emits_a_bare_literal_for_a_numeric_key(tmp_path, engine):
    driver = KuzuDriver(make_project(tmp_path))
    await driver.connect()

    await driver.expand(ExpandRequest(nodeIds=["City:1"], direction="to", limit=25))

    assert any("n.`cid` IN [1]" in statement for statement in engine.worker.statements)


async def test_pull_category_excludes_what_the_canvas_already_holds(tmp_path, engine):
    driver = KuzuDriver(make_project(tmp_path))
    await driver.connect()

    await driver.pull_category(
        PullCategoryRequest(category="Person", loadedNodeIds=["Person:Alice"], limit=5)
    )

    assert any('WHERE NOT n.`name` IN ["Alice"]' in s for s in engine.worker.statements)


# -- registration -----------------------------------------------------------


def test_both_types_reach_their_driver_through_the_factory(tmp_path):
    kuzu_project = make_project(tmp_path)
    assert isinstance(DriverFactory.create_driver(kuzu_project), KuzuDriver)

    ladybug_project = make_project(tmp_path, LADYBUG_HEADER, "ladybug")
    assert isinstance(DriverFactory.create_driver(ladybug_project), LadybugDriver)


def test_the_api_surface_names_the_intents_but_not_search(tmp_path):
    urls = KuzuDriver(make_project(tmp_path)).get_api_info("demo")["api_urls"]

    assert urls["expand"] == "/api/kuzu/demo/expand"
    assert urls["schema"] == "/api/kuzu/demo/schema"
    # Neither engine's full-text extension is wired, so the route is not advertised.
    assert "search" not in urls


def test_the_ladybug_api_surface_answers_under_its_own_name(tmp_path):
    driver = LadybugDriver(make_project(tmp_path, LADYBUG_HEADER, "ladybug"))
    info = driver.get_api_info("demo")

    assert info["type"] == "ladybug"
    assert info["api_urls"]["query"] == "/api/ladybug/demo/query"
