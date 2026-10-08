"""
The graph-schema cache: the contract the endpoints rely on, and which write paths
invalidate it.

The contract tests exercise the cache directly -- ``get``, ``put``, ``invalidate``,
``invalidate_project``, ``clear`` and the TTL. The trigger tests pin the seams
where a write path calls ``invalidate_project``: once pinned, a save that left
out the invalidation fails this file's tests instead of serving stale schemas.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from graphxr_database_proxy.api import projects as projects_module
from graphxr_database_proxy.api import relational as relational_module
from graphxr_database_proxy.api.auth import verify_admin_token
from graphxr_database_proxy.api.projects import get_project_service
from graphxr_database_proxy.models.project import (
    Category,
    DatabaseConfig,
    DatabaseType,
    GraphSchema,
    Project,
    ProjectUpdate,
)
from graphxr_database_proxy.services.graph_schema_cache import (
    GRAPH_SCHEMA_CACHE,
    GraphSchemaCache,
)


def schema(label: str = "Person") -> GraphSchema:
    return GraphSchema(
        categories=[
            Category(name=label, props=["name"], keys=["name"], keysTypes={}, propsTypes={})
        ],
        relationships=[],
    )


def project(name: str = "p", host: str = "db", project_id: str = "p1") -> Project:
    return Project(
        id=project_id,
        name=name,
        database_type=DatabaseType.NEO4J,
        database_config=DatabaseConfig(
            type=DatabaseType.NEO4J, host=host, database_id="neo4j"
        ),
    )


@pytest.fixture(autouse=True)
def clean_cache():
    GRAPH_SCHEMA_CACHE.clear()
    original = GRAPH_SCHEMA_CACHE.ttl
    GRAPH_SCHEMA_CACHE.ttl = 60.0
    yield
    GRAPH_SCHEMA_CACHE.clear()
    GRAPH_SCHEMA_CACHE.ttl = original


# ---------------------------------------------------------------------------
# contract: get / put / invalidate / clear
# ---------------------------------------------------------------------------


def test_get_returns_none_on_miss():
    cache = GraphSchemaCache(ttl=60.0)
    assert cache.get("never-seen") is None


def test_put_then_get_returns_the_stored_value():
    cache = GraphSchemaCache(ttl=60.0)
    stored = schema("Person")
    cache.put("p1:digest", stored)
    assert cache.get("p1:digest") is stored


def test_invalidate_removes_only_the_named_entry():
    cache = GraphSchemaCache(ttl=60.0)
    cache.put("p1:aaaa", schema("A"))
    cache.put("p2:bbbb", schema("B"))

    cache.invalidate("p1:aaaa")

    assert cache.get("p1:aaaa") is None
    assert cache.get("p2:bbbb") is not None


def test_invalidate_project_drops_every_entry_for_that_id():
    cache = GraphSchemaCache(ttl=60.0)
    cache.put("p1:aaaa", schema("A"))
    cache.put("p1:bbbb", schema("B"))
    cache.put("p2:cccc", schema("C"))

    cache.invalidate_project("p1")

    assert cache.get("p1:aaaa") is None
    assert cache.get("p1:bbbb") is None
    assert cache.get("p2:cccc") is not None


def test_clear_drops_every_entry():
    cache = GraphSchemaCache(ttl=60.0)
    cache.put("p1:aaaa", schema())
    cache.put("p2:bbbb", schema())

    cache.clear()

    assert cache.get("p1:aaaa") is None
    assert cache.get("p2:bbbb") is None


def test_invalidate_on_an_unknown_key_is_a_noop():
    cache = GraphSchemaCache(ttl=60.0)
    cache.put("p1:aaaa", schema())

    cache.invalidate("never-there")  # must not raise, must not evict

    assert cache.get("p1:aaaa") is not None


def test_invalidate_project_for_an_unknown_id_is_a_noop():
    cache = GraphSchemaCache(ttl=60.0)
    cache.put("p1:aaaa", schema())

    cache.invalidate_project("never-there")  # must not raise, must not evict

    assert cache.get("p1:aaaa") is not None


# ---------------------------------------------------------------------------
# TTL contract
# ---------------------------------------------------------------------------


def test_a_value_under_the_ttl_is_returned():
    cache = GraphSchemaCache(ttl=1.0)
    cache.put("p1:digest", schema())
    assert cache.get("p1:digest") is not None


def test_a_value_older_than_the_ttl_is_gone():
    cache = GraphSchemaCache(ttl=1.0)
    cache.put("p1:digest", schema())
    time.sleep(1.5)
    assert cache.get("p1:digest") is None


def test_a_ttl_of_zero_disables_returning_values():
    # ``GRAPH_SCHEMA_CACHE_TTL_SECONDS=0`` is the documented off-switch.
    cache = GraphSchemaCache(ttl=0)
    cache.put("p1:digest", schema())
    assert cache.get("p1:digest") is None


# ---------------------------------------------------------------------------
# triggers: which write paths call invalidate_project
# ---------------------------------------------------------------------------


def test_invalidate_helper_calls_invalidate_project_for_the_project_id(monkeypatch):
    """
    ``_invalidate`` in ``api/relational.py:272-282`` is the cache's only caller for
    the save path. A save that replaced the cache line with ``pass`` would still
    pass the route's contract; this test fails the moment that line goes.
    """
    target = project(project_id="mapped-id")

    calls: List[str] = []
    monkeypatch.setattr(
        GRAPH_SCHEMA_CACHE,
        "invalidate_project",
        lambda project_id: calls.append(project_id),
    )
    # ``_invalidate`` also disposes the pool; stub it so this test is independent.
    monkeypatch.setattr(
        relational_module,
        "POOL",
        type("P", (), {"dispose_project": staticmethod(lambda project_id: None)})(),
    )

    relational_module._invalidate(target)

    assert calls == ["mapped-id"]


class _ScriptedProjectsGateway:
    """
    A projects service that answers from a dict so the route never opens
    ``config/projects.json`` -- the live local state the lane is forbidden to
    read. The contract ``delete_project`` returns ``True`` is what the real
    service answers when the project exists.
    """

    def __init__(self, held: Project) -> None:
        self.held = held
        self.by_name: Dict[str, Project] = {held.name: held}

    async def get_project_by_name(self, name: str) -> Optional[Project]:
        return self.by_name.get(name)

    async def get_project(self, project_id: str) -> Optional[Project]:
        return self.held if self.held.id == project_id else None

    async def update_project(
        self, project_id: str, update_data: ProjectUpdate
    ) -> Optional[Project]:
        if self.held.id != project_id:
            return None
        changed = self.held.model_copy(
            update={
                "database_config": update_data.database_config or self.held.database_config
            }
        )
        self.by_name[changed.name] = changed
        self.held = changed
        return changed

    async def delete_project(self, project_id: str) -> bool:
        if self.held.id != project_id:
            return False
        self.by_name = {}
        return True


@pytest.mark.xfail(
    reason="defect: DELETE /api/project/delete leaves cache entries behind until TTL",
    strict=False,
)
def test_delete_project_route_calls_invalidate_project(monkeypatch):
    """
    Pin for the delete trigger -- the bug the lane is reporting to the CTO.

    The route lives in ``api/projects.py:delete_project`` (``api/projects.py:92-106``)
    and reaches ``ProjectService.delete_project``
    (``services/project_service.py:143-149``). Neither calls the cache; the lane
    does not own either, so the gap is reported rather than fixed.

    Marked ``xfail`` (strict=False) so the gate stays green today; it becomes
    ``xpass`` once the CTO closes the gap, which is the moment to remove the
    marker.
    """
    app = FastAPI()
    app.include_router(projects_module.router)
    gateway = _ScriptedProjectsGateway(
        project(name="deleted", project_id="deleted-id")
    )
    app.dependency_overrides[get_project_service] = lambda: gateway
    app.dependency_overrides[verify_admin_token] = lambda: None

    calls: List[str] = []
    monkeypatch.setattr(
        GRAPH_SCHEMA_CACHE,
        "invalidate_project",
        lambda project_id: calls.append(project_id),
    )

    with TestClient(app) as client:
        response = client.delete("/api/project/delete?project_id=deleted-id")

    assert response.status_code == 200, response.text
    assert calls == ["deleted-id"]