"""
End-to-end lifecycle of a relational project as the graph-schema cache sees it.

The cache's own contract lives in ``tests/test_graph_schema_cache*.py``; the
route-level invalidation triggers live in ``tests/test_relational_endpoints.py``.
This file pins the other half: the sequence of HTTP calls the editor and the
management surface make on a project's lifetime, and the cache's obligation
to be consistent with each of them end to end.

The probe count is read off ``server.inspector.calls`` — every catalog read
the walk did. A test that sees it go up proves a miss; one that sees it stay
still proves a hit. A bare project (no ``graph_mapping`` override) is the
fixture, so every ``/graphSchema`` call has to walk the catalog on a miss.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from graphxr_database_proxy.api import database as database_module
from graphxr_database_proxy.api.auth import verify_admin_token
from graphxr_database_proxy.api.projects import (
    get_project_service as projects_get_project_service,
)
from graphxr_database_proxy.api.relational import (
    get_project_service as relational_get_project_service,
)
from graphxr_database_proxy.main import app
from graphxr_database_proxy.models.project import (
    AuthType,
    DatabaseConfig,
    DatabaseType,
    Project,
    ProjectCreate,
    ProjectUpdate,
)
from graphxr_database_proxy.services.graph_schema_cache import (
    GRAPH_SCHEMA_CACHE,
    cache_key,
)
from tests.test_relational_endpoints import client, server  # noqa: F401  (fixtures)
from tests.test_relational_pool import draft_document, project as make_project


GRAPH_SCHEMA_URL = "/api/{dbtype}/{name}/graphSchema"
MAPPING_URL = "/api/relational/{name}/mapping"
DELETE_URL = "/api/project/delete?project_id={pid}"
CREATE_URL = "/api/project/create"


def graph_schema_url(name: str, dbtype: str = "mssql") -> str:
    return GRAPH_SCHEMA_URL.format(dbtype=dbtype, name=name)


def mapping_url(name: str) -> str:
    return MAPPING_URL.format(name=name)


def delete_url(project_id: str) -> str:
    return DELETE_URL.format(pid=project_id)


def probe_count(server: Any) -> int:
    """How many catalog reads the walk did. Each inspector call bumps this."""
    return len(server.inspector.calls)


class _FullScriptedProjects:
    """
    A scripted projects service for end-to-end lifecycle tests.

    Mirrors the live ``ProjectService`` (so the route contract holds) but
    answers from a dict. ``create_project`` reuses the same id when a name
    already exists, so a recreate-after-delete is observed against the same
    key.
    """

    def __init__(self, initial: Project) -> None:
        self.by_id: Dict[str, Project] = {initial.id: initial}
        self.by_name: Dict[str, Project] = {initial.name: initial}
        self.saved: List[str] = []
        self.deleted: List[str] = []
        self.created: List[str] = []

    async def get_project_by_name(self, name: str) -> Optional[Project]:
        return self.by_name.get(name)

    async def get_project(self, project_id: str) -> Optional[Project]:
        return self.by_id.get(project_id)

    async def update_project(
        self, project_id: str, update_data: ProjectUpdate
    ) -> Optional[Project]:
        held = self.by_id.get(project_id)
        if held is None:
            return None
        self.saved.append(project_id)
        changed = held.model_copy(
            update={"database_config": update_data.database_config or held.database_config}
        )
        self.by_id[changed.id] = changed
        self.by_name[changed.name] = changed
        return changed

    async def create_project(self, project_data: ProjectCreate) -> Project:
        held = self.by_name.get(project_data.name)
        if held is not None:
            return held
        made = Project(
            id=project_data.name,
            name=project_data.name,
            database_type=project_data.database_type,
            database_config=project_data.database_config,
        )
        self.by_id[made.id] = made
        self.by_name[made.name] = made
        self.created.append(made.id)
        return made

    async def delete_project(self, project_id: str) -> bool:
        held = self.by_id.get(project_id)
        if held is None:
            return False
        self.deleted.append(project_id)
        del self.by_id[project_id]
        self.by_name.pop(held.name, None)
        return True

    async def list_projects(self) -> List[Project]:
        return list(self.by_id.values())


@pytest.fixture(autouse=True)
def clean_cache() -> Any:
    """Reset the process-wide cache around every test."""
    GRAPH_SCHEMA_CACHE.clear()
    ttl = GRAPH_SCHEMA_CACHE.ttl
    GRAPH_SCHEMA_CACHE.ttl = 60.0
    yield
    GRAPH_SCHEMA_CACHE.clear()
    GRAPH_SCHEMA_CACHE.ttl = ttl


@pytest.fixture
def lifecycle(client: Any, server: Any) -> Any:
    """
    A scripted service that supports create+delete, mounted on both routers.

    The relational routes and the project routes share the same backing dict,
    so a delete via the project route is visible to the relational route on
    the next call. The project is a bare one with no ``graph_mapping``
    override, so every ``/graphSchema`` call has to walk the catalog on a
    miss.
    """
    held = make_project("mapped")
    scripted = _FullScriptedProjects(held)
    app.dependency_overrides[relational_get_project_service] = lambda: scripted
    app.dependency_overrides[projects_get_project_service] = lambda: scripted
    app.dependency_overrides[database_module.get_project_service] = lambda: scripted
    app.dependency_overrides[verify_admin_token] = lambda: None
    yield scripted
    for dep in (relational_get_project_service, projects_get_project_service,
                database_module.get_project_service, verify_admin_token):
        app.dependency_overrides.pop(dep, None)


def test_create_then_first_schema_call_probes_and_populates_the_cache(
    client: Any, lifecycle: Any, server: Any
) -> None:
    """Step 1: a bare project, then a first ``/graphSchema``: the cache is empty, the
    walk runs, and the entry is stored under ``id:digest``."""
    key = cache_key(lifecycle.by_id["mapped"])
    assert GRAPH_SCHEMA_CACHE.get(key) is None, "the cache should be empty to start"
    before = probe_count(server)
    response = client.get(graph_schema_url("mapped"))
    assert response.status_code == 200, (response.status_code, response.text[:1500])
    answer = response.json()
    assert answer.get("success") is True, answer
    assert probe_count(server) > before, "the first call should have walked the catalog"
    assert GRAPH_SCHEMA_CACHE.get(key) is not None, "the entry should be stored"


def test_a_second_call_is_served_from_the_cache_and_does_not_probe(
    client: Any, lifecycle: Any, server: Any
) -> None:
    """Step 2: the same ``/graphSchema`` again: the cache answers, the inspector is
    not asked for a single column."""
    first = client.get(graph_schema_url("mapped")).json()
    first_probes = probe_count(server)
    assert first_probes > 0, "the first call should have probed at least once"
    second = client.get(graph_schema_url("mapped")).json()
    assert second["success"] is True
    assert [c["name"] for c in second["data"]["categories"]] == [
        c["name"] for c in first["data"]["categories"]
    ]
    assert probe_count(server) == first_probes, "the second call hit the cache"


def test_saving_a_mapping_invalidates_the_cache_and_a_reget_repopulates(
    client: Any, lifecycle: Any, server: Any
) -> None:
    """Step 3: ``PUT /api/relational/mapped/mapping`` drops the schema entry and the
    spy on ``invalidate_project`` records the call. A subsequent ``/graphSchema``
    finds the cache empty and answers again."""
    client.get(graph_schema_url("mapped")).json()
    key = cache_key(lifecycle.by_id["mapped"])
    assert GRAPH_SCHEMA_CACHE.get(key) is not None, "the entry should be there to drop"
    document = draft_document().model_dump(by_alias=True)
    invalidate_calls: List[str] = []
    original_invalidate = GRAPH_SCHEMA_CACHE.invalidate_project

    def spy(project_id: str) -> None:
        invalidate_calls.append(project_id)
        original_invalidate(project_id)

    with patch.object(GRAPH_SCHEMA_CACHE, "invalidate_project", spy):
        answer = client.put(
            mapping_url("mapped"), json={"model": document}
        ).json()
    assert answer["saved"] is True, answer
    assert invalidate_calls == ["mapped"], "save must call invalidate_project"
    # The save changes the config (graph_mapping was added), so the digest moves;
    # the old key no longer exists for this project, and the new key is empty.
    assert GRAPH_SCHEMA_CACHE.get(key) is None, "save must drop the entry"
    new_key = cache_key(lifecycle.by_id["mapped"])
    assert new_key != key, "adding graph_mapping must change the digest"
    assert GRAPH_SCHEMA_CACHE.get(new_key) is None, "the new key should be empty"
    answer_again = client.get(graph_schema_url("mapped")).json()
    assert answer_again["success"] is True
    assert GRAPH_SCHEMA_CACHE.get(new_key) is not None, "the reget must repopulate"


def test_deleting_a_project_drops_its_cache_entry(
    client: Any, lifecycle: Any, server: Any
) -> None:
    """Step 4: ``DELETE /api/project/delete`` (post-``e226efa``) drops the cache entry
    for the deleted id and the spy on ``invalidate_project`` records the call."""
    client.get(graph_schema_url("mapped")).json()
    key = cache_key(lifecycle.by_id["mapped"])
    assert GRAPH_SCHEMA_CACHE.get(key) is not None
    invalidate_calls: List[str] = []
    original_invalidate = GRAPH_SCHEMA_CACHE.invalidate_project

    def spy(project_id: str) -> None:
        invalidate_calls.append(project_id)
        original_invalidate(project_id)

    with patch.object(GRAPH_SCHEMA_CACHE, "invalidate_project", spy):
        response = client.delete(delete_url("mapped"))
    assert response.status_code == 200, response.text
    assert invalidate_calls == ["mapped"]
    assert GRAPH_SCHEMA_CACHE.get(key) is None, "delete must drop the entry"


def test_recreate_with_a_different_config_probes_again_with_a_new_key(
    client: Any, lifecycle: Any, server: Any
) -> None:
    """Step 5: a fresh ``POST /api/project/create`` with the same id but a different
    config: the old key is gone, the new key is empty, the next ``/graphSchema``
    walks the catalog and populates the new entry."""
    client.get(graph_schema_url("mapped")).json()
    old_key = cache_key(lifecycle.by_id["mapped"])
    assert GRAPH_SCHEMA_CACHE.get(old_key) is not None
    response = client.delete(delete_url("mapped"))
    assert response.status_code == 200
    assert GRAPH_SCHEMA_CACHE.get(old_key) is None, "delete must have dropped the entry"
    different_config = DatabaseConfig(
        type=DatabaseType.MSSQL,
        host="other.internal",
        port=1433,
        username="graphxr",
        password="s3cret",
        auth_type=AuthType.USERNAME_PASSWORD,
    )
    create_answer = client.post(
        CREATE_URL,
        json={
            "name": "mapped",
            "database_type": "mssql",
            "database_config": different_config.model_dump(mode="json"),
        },
    ).json()
    assert "id" in create_answer, create_answer
    new_key = cache_key(lifecycle.by_id["mapped"])
    assert new_key != old_key, "a different config must yield a different cache key"
    assert GRAPH_SCHEMA_CACHE.get(new_key) is None, "the new key should be empty"
    before = probe_count(server)
    answer = client.get(graph_schema_url("mapped")).json()
    assert answer["success"] is True, answer
    assert probe_count(server) > before, "recreate + GET should walk the catalog"
    assert GRAPH_SCHEMA_CACHE.get(new_key) is not None, "the new entry should be stored"
