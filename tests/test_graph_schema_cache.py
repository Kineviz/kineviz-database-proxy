"""
The graph-schema cache.

What is asserted is the behaviour the endpoints depend on: that a hit does not
touch the store, that a hit still leaves a fresh driver able to resolve an
``/expand`` seed, that a config edit is not served the previous database's
schema, and that several callers arriving at once probe once between them.
"""

from __future__ import annotations

import asyncio

import pytest

from kineviz_database_proxy.drivers.base import BaseDatabaseDriver
from kineviz_database_proxy.models.project import (
    Category,
    DatabaseConfig,
    DatabaseType,
    GraphSchema,
    GraphSchemaResponse,
    Project,
)
from kineviz_database_proxy.services import graph_schema_cache as module
from kineviz_database_proxy.services.graph_schema_cache import (
    GRAPH_SCHEMA_CACHE,
    GraphSchemaCache,
    cache_key,
)


def project(name: str = "p", host: str = "db", project_id: str = "p1") -> Project:
    return Project(
        id=project_id,
        name=name,
        database_type=DatabaseType.NEO4J,
        database_config=DatabaseConfig(type=DatabaseType.NEO4J, host=host, database_id="neo4j"),
    )


def schema(label: str = "Person") -> GraphSchema:
    return GraphSchema(
        categories=[Category(name=label, props=["name"], keys=["name"], keysTypes={}, propsTypes={})],
        relationships=[],
    )


class ProbeDriver(BaseDatabaseDriver):
    """A driver that counts how often the store was actually asked."""

    def __init__(self, proj: Project, response: GraphSchemaResponse, delay: float = 0.0):
        super().__init__(proj)
        self.response = response
        self.delay = delay
        self.probes = 0
        self.remembered: dict = {}

    async def connect(self):  # pragma: no cover - never dialled
        pass

    async def disconnect(self):  # pragma: no cover
        pass

    async def test_connection(self):  # pragma: no cover
        return True

    async def execute_query(self, query, parameters=None):  # pragma: no cover
        raise NotImplementedError

    async def get_schema(self):  # pragma: no cover
        raise NotImplementedError

    async def get_sample_data(self):  # pragma: no cover
        raise NotImplementedError

    def get_api_info(self, project_name):  # pragma: no cover
        return {"api_urls": {}}

    def remember_graph_categories(self, categories):
        self.remembered = categories

    async def get_graph_schema(self):
        self.probes += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.response


@pytest.fixture(autouse=True)
def clean_cache():
    GRAPH_SCHEMA_CACHE.clear()
    ttl = GRAPH_SCHEMA_CACHE.ttl
    GRAPH_SCHEMA_CACHE.ttl = 60.0
    yield
    GRAPH_SCHEMA_CACHE.clear()
    GRAPH_SCHEMA_CACHE.ttl = ttl


# ---------------------------------------------------------------------------
# the key
# ---------------------------------------------------------------------------


def test_two_projects_never_share_an_entry():
    assert cache_key(project(project_id="a")) != cache_key(project(project_id="b"))


def test_repointing_a_project_at_another_database_changes_the_key():
    # The id survives an edit, so keying on it alone would serve the previous
    # database's schema until the entry expired.
    assert cache_key(project(host="old")) != cache_key(project(host="new"))


def test_the_key_carries_a_digest_rather_than_the_config():
    # The config holds the password and the service-account key; neither may end
    # up as part of a cache key that gets logged.
    key = cache_key(project(host="secret-host"))
    assert "secret-host" not in key


# ---------------------------------------------------------------------------
# hits, misses and expiry
# ---------------------------------------------------------------------------


async def test_a_second_call_does_not_touch_the_store():
    driver = ProbeDriver(project(), GraphSchemaResponse(success=True, data=schema()))

    first = await driver.get_graph_schema_cached()
    second = await driver.get_graph_schema_cached()

    assert first.success and second.success
    assert [c.name for c in second.data.categories] == ["Person"]
    assert driver.probes == 1


async def test_a_hit_still_hands_the_categories_to_the_intent_mixin():
    # A primary-key backend turns a node id into a predicate with these, and the
    # driver serving the hit has never seen them -- without this /expand resolves
    # no seeds and answers with an empty graph.
    proj = project()
    await ProbeDriver(proj, GraphSchemaResponse(success=True, data=schema())).get_graph_schema_cached()

    fresh = ProbeDriver(proj, GraphSchemaResponse(success=True, data=schema()))
    await fresh.get_graph_schema_cached()

    assert fresh.probes == 0
    assert list(fresh.remembered) == ["Person"]


async def test_an_entry_older_than_the_ttl_is_probed_again(monkeypatch):
    driver = ProbeDriver(project(), GraphSchemaResponse(success=True, data=schema()))
    clock = {"now": 1000.0}
    monkeypatch.setattr(module.time, "monotonic", lambda: clock["now"])

    await driver.get_graph_schema_cached()
    clock["now"] += GRAPH_SCHEMA_CACHE.ttl + 1
    await driver.get_graph_schema_cached()

    assert driver.probes == 2


async def test_refresh_re_probes_and_repopulates():
    driver = ProbeDriver(project(), GraphSchemaResponse(success=True, data=schema()))

    await driver.get_graph_schema_cached()
    await driver.get_graph_schema_cached(refresh=True)
    await driver.get_graph_schema_cached()

    assert driver.probes == 2  # the refresh, and nothing after it


async def test_a_failed_probe_is_not_cached():
    # Holding a connection error would keep answering for a database that has
    # since come back.
    driver = ProbeDriver(project(), GraphSchemaResponse(success=False, error="unreachable"))

    assert (await driver.get_graph_schema_cached()).success is False
    assert (await driver.get_graph_schema_cached()).success is False
    assert driver.probes == 2


async def test_a_ttl_of_zero_turns_the_cache_off():
    GRAPH_SCHEMA_CACHE.ttl = 0
    driver = ProbeDriver(project(), GraphSchemaResponse(success=True, data=schema()))

    await driver.get_graph_schema_cached()
    await driver.get_graph_schema_cached()

    assert driver.probes == 2


# ---------------------------------------------------------------------------
# single flight
# ---------------------------------------------------------------------------


async def test_callers_arriving_together_probe_once_between_them():
    # GraphXR opens a graph by asking several panels for the schema at once; on a
    # cold cache that is simultaneous identical probes without the lock.
    proj = project()
    drivers = [
        ProbeDriver(proj, GraphSchemaResponse(success=True, data=schema()), delay=0.02)
        for _ in range(5)
    ]

    results = await asyncio.gather(*(d.get_graph_schema_cached() for d in drivers))

    assert all(result.success for result in results)
    assert sum(d.probes for d in drivers) == 1


# ---------------------------------------------------------------------------
# bounds
# ---------------------------------------------------------------------------


def test_the_cache_does_not_grow_past_its_ceiling():
    cache = GraphSchemaCache(ttl=60.0, max_entries=4)
    for index in range(20):
        cache.put(f"key-{index}", schema())
    assert len(cache._entries) <= 4


def test_invalidating_a_project_drops_it_whatever_config_it_was_stored_under():
    cache = GraphSchemaCache(ttl=60.0)
    cache.put("p1:aaaa", schema())
    cache.put("p1:bbbb", schema())
    cache.put("p2:cccc", schema())

    cache.invalidate_project("p1")

    assert list(cache._entries) == ["p2:cccc"]
