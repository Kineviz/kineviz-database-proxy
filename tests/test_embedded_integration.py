# -*- coding: utf-8 -*-
"""
The whole path against a real engine, opt-in.

Skipped unless ``GRAPHXR_PROXY_ENGINE_TESTS=1``, because it downloads an engine
build -- tens of megabytes, and an interpreter with it the first time -- and the
acceptance gate has to stay offline and quick. Everything it covers is covered by
the stubbed tests too; what it adds is the one thing a stub cannot prove, which is
that the real engine parses the statements and answers in the shape the mapper
expects.

Run it with::

    GRAPHXR_PROXY_ENGINE_TESTS=1 uv run pytest tests/test_embedded_integration.py -q

``GRAPHXR_PROXY_ENGINES_DIR`` is worth setting to a scratch directory the first
time, so the downloads do not land in the developer's home.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from kineviz_database_proxy.contract.intents import ExpandRequest
from kineviz_database_proxy.drivers.embedded.pool import WORKER_POOL
from kineviz_database_proxy.drivers.embedded.runtime import installed_runtime
from kineviz_database_proxy.drivers.embedded.store_probe import probe_store
from kineviz_database_proxy.drivers.embedded.wheelhouse import wheel_for, wheelhouse_dir
from kineviz_database_proxy.drivers.kuzu import KuzuDriver
from kineviz_database_proxy.drivers.ladybug import LadybugDriver
from kineviz_database_proxy.drivers.latticedb import LatticeDbDriver
from kineviz_database_proxy.models.project import DatabaseConfig, DatabaseType, Project
from kineviz_database_proxy.services.graph_schema_cache import GRAPH_SCHEMA_CACHE

pytestmark = pytest.mark.skipif(
    os.getenv("GRAPHXR_PROXY_ENGINE_TESTS") != "1",
    reason="set GRAPHXR_PROXY_ENGINE_TESTS=1 to download engines and run these",
)

#: The newest release of each family at the time of writing. Pinned so the test
#: says what it exercised; the resolver would find them on its own.
ENGINES = [("kuzu", "0.11.3", KuzuDriver), ("ladybug", "0.19.1", LadybugDriver)]

#: LatticeDB is not in ``ENGINES``: it has no DDL to run and no primary key to
#: declare, so its store is built by a different route and asserted separately.
LATTICEDB_VERSION = "0.14.0"


def _latticedb_gap() -> str:
    """
    Why LatticeDB cannot be exercised on this machine, or "" when it can.

    Upstream publishes no Windows wheel, so there it needs one the user built --
    which is a supported configuration rather than a broken one, and the difference
    between "no wheel" and "a wheel you have not built yet" is worth saying in the
    skip reason instead of writing the platform off.
    """
    if sys.platform != "win32":
        return ""
    if wheel_for("latticedb", LATTICEDB_VERSION) is not None:
        return ""
    if installed_runtime("latticedb", LATTICEDB_VERSION) is not None:
        return ""
    return (
        "latticedb publishes no Windows wheel; build one into "
        f"{wheelhouse_dir()} (see doc/EMBEDDED_STORES.md) to run these"
    )


LATTICEDB_GAP = _latticedb_gap()

SCHEMA = [
    "CREATE NODE TABLE Person(name STRING, age INT64, PRIMARY KEY(name))",
    "CREATE NODE TABLE City(cid INT64, name STRING, PRIMARY KEY(cid))",
    "CREATE REL TABLE LivesIn(FROM Person TO City, since INT64)",
    "CREATE (p:Person {name: 'Alice', age: 30})",
    "CREATE (c:City {cid: 1, name: 'Toronto'})",
    "MATCH (p:Person), (c:City) CREATE (p)-[:LivesIn {since: 2020}]->(c)",
]


@pytest.fixture(autouse=True)
def clean_schema_cache():
    GRAPH_SCHEMA_CACHE.clear()
    yield
    GRAPH_SCHEMA_CACHE.clear()


async def build_store(engine, version, path):
    """Create a small graph with the real engine, through the real worker."""
    from kineviz_database_proxy.drivers.embedded.engine_service import ENGINE_SERVICE
    from kineviz_database_proxy.drivers.embedded.pool import EngineWorker

    runtime = await ENGINE_SERVICE.ensure(engine, version)
    worker = EngineWorker(runtime, str(path), read_only=False)
    await worker.start()
    try:
        for statement in SCHEMA:
            await worker.request("query", statement=statement)
    finally:
        await worker.stop()
    return runtime


def make_project(driver_class, path):
    database_type = DatabaseType(driver_class.database_type)
    return Project(
        id="itest",
        name="itest",
        database_type=database_type,
        database_config=DatabaseConfig(type=database_type, database_path=str(path)),
    )


@pytest.mark.parametrize("engine,version,driver_class", ENGINES, ids=[e for e, _v, _d in ENGINES])
async def test_a_real_store_round_trips_through_the_driver(engine, version, driver_class, tmp_path):
    store = tmp_path / "graph.kz"
    await build_store(engine, version, store)

    # The header the engine just wrote is what picks the build to read it back.
    fingerprint = probe_store(store)
    assert fingerprint.engine == engine

    driver = driver_class(make_project(driver_class, store))
    try:
        await driver.connect()

        schema = await driver.get_graph_schema()
        assert schema.success
        assert {c.name for c in schema.data.categories} == {"Person", "City"}
        person = next(c for c in schema.data.categories if c.name == "Person")
        assert person.keys == ["name"]
        assert {r.name for r in schema.data.relationships} == {"LivesIn"}

        result = await driver.execute_query("MATCH (n:Person)-[r]->(m) RETURN n, r, m")
        assert result.success and result.data.type == "GRAPH"
        assert [n.id for n in result.data.data.nodes] == ["Person:Alice", "City:1"]
        edge = result.data.data.relationships[0]
        assert (edge.startNodeId, edge.endNodeId) == ("Person:Alice", "City:1")

        # The id the driver just handed out has to be one its own predicate matches.
        expanded = await driver.expand(ExpandRequest(nodeIds=["Person:Alice"], limit=10))
        assert {n.id for n in expanded.data.nodes} == {"Person:Alice", "City:1"}

        table = await driver.get_schema()
        assert table.success
        assert table.data["Person"]["age"] == "INT64"
    finally:
        await driver.disconnect()
        await WORKER_POOL.shutdown()


async def test_a_ladybug_project_serves_a_kuzu_store_with_the_kuzu_engine(tmp_path):
    # The families are interchangeable from the caller's side: the file picks the
    # engine, the project type picks the route.
    kuzu_store = tmp_path / "kuzu.kz"
    await build_store("kuzu", "0.11.3", kuzu_store)

    driver = LadybugDriver(make_project(LadybugDriver, kuzu_store))
    try:
        await driver.connect()
        assert driver.engine_in_use == "kuzu"

        schema = await driver.get_graph_schema()
        assert schema.success
        assert {c.name for c in schema.data.categories} == {"Person", "City"}

        expanded = await driver.expand(ExpandRequest(nodeIds=["Person:Alice"], limit=10))
        assert {n.id for n in expanded.data.nodes} == {"Person:Alice", "City:1"}
    finally:
        await driver.disconnect()
        await WORKER_POOL.shutdown()


async def test_a_reply_larger_than_the_readers_line_buffer_arrives_whole(tmp_path):
    # The failure this guards: replies were read with readline(), which gives up at
    # 64 KiB, so every real graph came back as "Separator is not found, and chunk
    # exceed the limit".
    store = tmp_path / "graph.kz"
    await build_store("kuzu", "0.11.3", store)

    driver = KuzuDriver(make_project(KuzuDriver, store))
    try:
        await driver.connect()
        result = await driver.execute_query(
            "UNWIND range(1, 20000) AS i RETURN i, '" + "y" * 250 + "' AS pad"
        )
        assert result.success, result.error
        assert len(result.data.data) == 20001  # header row plus 20000 rows
    finally:
        await driver.disconnect()
        await WORKER_POOL.shutdown()


# -- LatticeDB ---------------------------------------------------------------


#: Built with the engine's own API rather than through the worker: LatticeDB has
#: no DDL, and creating a store needs `create=True`, which the proxy never passes
#: because the proxy never creates one.
BUILD_LATTICE_STORE = """
    import sys
    from latticedb import Database

    db = Database(sys.argv[1], create=True)
    db.open()
    with db.write() as txn:
        alice = txn.create_node(labels=["Person"], properties={"name": "Alice", "age": 30})
        bob = txn.create_node(labels=["Person", "Employee"], properties={"name": "Bob"})
        toronto = txn.create_node(labels=["City"], properties={"name": "Toronto"})
        odd = txn.create_node(labels=["Odd Label"], properties={"x": 1.5})
        edge = txn.create_edge(alice.id, toronto.id, "LivesIn")
        txn.set_edge_property(edge.id, "since", 2020)
        txn.create_edge(alice.id, bob.id, "Knows")
        txn.create_edge(bob.id, odd.id, "IS FRIEND OF")
        txn.commit()
    db.close()
"""


async def build_lattice_store(path):
    """Create a small graph with the real engine, in the interpreter it was installed for."""
    from kineviz_database_proxy.drivers.embedded.engine_service import ENGINE_SERVICE

    runtime = await ENGINE_SERVICE.ensure("latticedb", LATTICEDB_VERSION)
    subprocess.run(
        [str(runtime.python), "-c", textwrap.dedent(BUILD_LATTICE_STORE), str(path)],
        check=True,
        env=runtime.env(),
    )
    return runtime


@pytest.mark.skipif(bool(LATTICEDB_GAP), reason=LATTICEDB_GAP or "available")
async def test_a_real_latticedb_store_round_trips_through_the_driver(tmp_path):
    store = tmp_path / "knowledge.db"
    await build_lattice_store(store)

    # The header the engine just wrote is what picks the build to read it back --
    # and LatticeDB's is a uint16 where the Kuzu family's is a uint64.
    fingerprint = probe_store(store)
    assert fingerprint.engine == "latticedb"
    assert fingerprint.storage_version == 3

    driver = LatticeDbDriver(make_project(LatticeDbDriver, store))
    try:
        await driver.connect()

        # Inferred, not read: nothing in the store declares a label or a property.
        # Each label of a multi-label node is its own category.
        schema = await driver.get_graph_schema()
        assert schema.success, schema.error
        assert {c.name for c in schema.data.categories} == {
            "Person", "Employee", "City", "Odd Label",
        }
        person = next(c for c in schema.data.categories if c.name == "Person")
        assert person.propsTypes == {"name": "STRING", "age": "INT64"}
        assert person.keys == []  # identity is internal-id; there is no key to find
        assert {r.name for r in schema.data.relationships} == {
            "LivesIn", "Knows", "IS FRIEND OF",
        }

        # A statement that projects entities comes back as a graph, and the edge is
        # placed from the endpoint columns rather than from anything it carries.
        result = await driver.execute_query(
            "MATCH (n)-[r:LivesIn]->(m) RETURN id(n), labels(n), properties(n), "
            "id(r), type(r), properties(r), id(n) AS r_src, id(m) AS r_dst, "
            "id(m), labels(m), properties(m)"
        )
        assert result.success, result.error
        assert result.data.type == "GRAPH"
        edge = result.data.data.relationships[0]
        assert edge.type == "LivesIn" and edge.properties == {"since": 2020}
        alice = next(n for n in result.data.data.nodes if n.id == edge.startNodeId)
        assert alice.properties["name"] == "Alice"

        # The id the driver just handed out has to be one its own predicate matches.
        expanded = await driver.expand(ExpandRequest(nodeIds=[alice.id], limit=10))
        assert edge.endNodeId in {n.id for n in expanded.data.nodes}

        # No relational layout is declared, so this refuses rather than inventing one.
        assert (await driver.get_schema()).success is False
    finally:
        await driver.disconnect()
        await WORKER_POOL.shutdown()


@pytest.mark.skipif(bool(LATTICEDB_GAP), reason=LATTICEDB_GAP or "available")
async def test_an_undirected_expand_places_every_edge_the_way_the_store_holds_it(tmp_path):
    """
    The failure this guards: an undirected pattern answers each edge twice with its
    ends swapped, and nothing in the row says which orientation is real. The
    dialect splits it into two directed statements so that it does.
    """
    store = tmp_path / "knowledge.db"
    await build_lattice_store(store)

    driver = LatticeDbDriver(make_project(LatticeDbDriver, store))
    try:
        await driver.connect()
        # Alice is node 1; she has two outgoing edges and no incoming ones.
        expanded = await driver.expand(
            ExpandRequest(nodeIds=["1"], direction="all", limit=50)
        )
        assert {(e.startNodeId, e.type) for e in expanded.data.relationships} == {
            ("1", "LivesIn"),
            ("1", "Knows"),
        }
    finally:
        await driver.disconnect()
        await WORKER_POOL.shutdown()


@pytest.mark.skipif(bool(LATTICEDB_GAP), reason=LATTICEDB_GAP or "available")
async def test_the_query_everyone_types_comes_back_as_a_graph(tmp_path):
    """
    ``MATCH (n)-[r]->(m) RETURN * LIMIT 100`` -- the example in this proxy's own API
    documentation -- answered "Expected expression" against the real engine, because
    LatticeDB has no ``*``. The driver rewrites it into the projection, so what a
    user typed reaches the mapper in the shape the mapper reads.
    """
    store = tmp_path / "knowledge.db"
    await build_lattice_store(store)

    driver = LatticeDbDriver(make_project(LatticeDbDriver, store))
    try:
        await driver.connect()

        result = await driver.execute_query("MATCH (n)-[r]->(m) RETURN * LIMIT 100")
        assert result.success, result.error
        assert result.data.type == "GRAPH"
        assert {r.type for r in result.data.data.relationships} == {
            "LivesIn", "Knows", "IS FRIEND OF",
        }
        # Placed, not merely listed: the ends came from the pattern, since a
        # LatticeDB edge cannot be asked for them.
        placed = {n.id for n in result.data.data.nodes}
        for edge in result.data.data.relationships:
            assert edge.startNodeId in placed and edge.endNodeId in placed

        # Naming the variables is the other half: this parses on its own and
        # answers integers, which is a graph-shaped nothing rather than an error.
        named = await driver.execute_query("MATCH (n)-[r]->(m) RETURN n, r, m")
        assert named.success, named.error
        assert named.data.type == "GRAPH"
        assert named.data.data.nodes

        # What it will not rewrite still fails -- with the reason attached.
        undirected = await driver.execute_query("MATCH (n)-[r]-(m) RETURN *")
        assert undirected.success is False
        assert "no * in its grammar" in undirected.error

        # And what GraphXR's search builder sends: every label and type backticked,
        # which is an invalid token here before the parser reaches anything else.
        tick = chr(96)
        quoted = await driver.execute_query(
            "MATCH (n0:{t}Person{t})-[r0:{t}LivesIn{t}]->(n1:{t}City{t}) "
            "RETURN n0, n1, r0 LIMIT 2000".format(t=tick)
        )
        assert quoted.success, quoted.error
        assert quoted.data.type == "GRAPH"
        assert [r.type for r in quoted.data.data.relationships] == ["LivesIn"]

        # A label a pattern cannot carry at all, quoted or not.
        spaced = await driver.execute_query(
            "MATCH (n0:{t}Odd Label{t}) RETURN n0".format(t=tick)
        )
        assert spaced.success, spaced.error
        assert [n.labels for n in spaced.data.data.nodes] == [["Odd Label"]]
    finally:
        await driver.disconnect()
        await WORKER_POOL.shutdown()


@pytest.mark.skipif(bool(LATTICEDB_GAP), reason=LATTICEDB_GAP or "available")
async def test_a_latticedb_project_refuses_a_kuzu_store(tmp_path):
    """
    The Kuzu family substitutes across itself; this boundary is not crossed. The
    message has to name the project type that would work, not merely refuse.
    """
    kuzu_store = tmp_path / "kuzu.kz"
    await build_store("kuzu", "0.11.3", kuzu_store)

    driver = LatticeDbDriver(make_project(LatticeDbDriver, kuzu_store))
    try:
        with pytest.raises(ConnectionError) as error:
            await driver.connect()
        assert "'kuzu' project" in str(error.value)
    finally:
        await WORKER_POOL.shutdown()
