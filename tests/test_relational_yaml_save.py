"""
The yaml body path of ``PUT /api/relational/{project}/mapping``.

T8's pin covers the model body. The save side-effect lives on the route, but
the body parse decides whether the route reaches ``_invalidate`` at all - a
future change that short-circuited the yaml branch would still pass the model
case and still leak a stale cache. Three branches, one file:

  1. A well-formed yaml save calls ``_invalidate`` and drops the entry.
  2. A yaml save with M5's NULL-typed key still calls ``_invalidate`` (the
     marker is reported in the answer, but the save itself stands).
  3. A yaml body that does not parse returns 422, never invalidates, and
     leaves an entry that was already there alone.
"""

from __future__ import annotations

from typing import Any, List
from unittest.mock import patch

import pytest

from graphxr_database_proxy.drivers.relational import mapping_io
from graphxr_database_proxy.models.graph_mapping import GraphMapping, MappingNode
from graphxr_database_proxy.services.graph_schema_cache import (
    GRAPH_SCHEMA_CACHE,
    cache_key,
)
from tests.test_relational_endpoints import client, projects, server  # noqa: F401


def _doc(properties: dict) -> GraphMapping:
    """A one-node document keyed on ``person_id``; the only thing that varies is the type."""
    return GraphMapping(
        nodes=[
            MappingNode(
                label="Person",
                table="Person",
                schema_name="dbo",
                key="person_id",
                properties=properties,
            )
        ],
        edges=[],
    )


def _install_spy() -> tuple[List[str], Any]:
    """A spy on ``invalidate_project`` that calls through, so the cache really does drop."""
    calls: List[str] = []
    original = GRAPH_SCHEMA_CACHE.invalidate_project

    def spy(project_id: str) -> None:
        calls.append(project_id)
        original(project_id)

    patcher = patch.object(GRAPH_SCHEMA_CACHE, "invalidate_project", spy)
    patcher.start()
    return calls, patcher


def test_a_yaml_save_invalidates_the_cache_and_drops_the_entry(
    client: Any, projects: Any, server: Any
) -> None:
    """Branch 1: the well-formed yaml save reaches ``_invalidate`` like the model body does."""
    project = projects.by_name["mapped"]
    key = cache_key(project)
    sentinel = object()
    GRAPH_SCHEMA_CACHE.put(key, sentinel)  # type: ignore[arg-type]
    assert GRAPH_SCHEMA_CACHE.get(key) is sentinel

    calls, patcher = _install_spy()
    try:
        answer = client.put(
            "/api/relational/mapped/mapping",
            json={"yaml": mapping_io.to_yaml(_doc({"person_id": "bigint"}))},
        ).json()
    finally:
        patcher.stop()

    assert answer["saved"] is True, answer
    assert calls == ["mapped"], "yaml body must call invalidate_project"
    assert GRAPH_SCHEMA_CACHE.get(key) is None, "save must drop the entry"


def test_a_yaml_save_with_a_null_typed_key_still_calls_invalidate(
    client: Any, projects: Any, server: Any
) -> None:
    """Branch 2: M5's marker is reported, the save stands, the cache still drops.

    A NULL-typed key parses cleanly - the marker is a string the map passes
    untouched - but ``_mapping_answer`` reports it as a problem on the document.
    The cache must still drop: the marker is a problem for the *next* expand,
    not for the invalidation that follows the save.
    """
    project = projects.by_name["mapped"]
    key = cache_key(project)
    sentinel = object()
    GRAPH_SCHEMA_CACHE.put(key, sentinel)  # type: ignore[arg-type]
    assert GRAPH_SCHEMA_CACHE.get(key) is sentinel

    calls, patcher = _install_spy()
    try:
        answer = client.put(
            "/api/relational/mapped/mapping",
            json={"yaml": mapping_io.to_yaml(_doc({"person_id": "NULL"}))},
        ).json()
    finally:
        patcher.stop()

    assert answer["saved"] is True, answer
    assert calls == ["mapped"], "the marker must not short-circuit invalidation"
    assert GRAPH_SCHEMA_CACHE.get(key) is None, "the entry must be gone"


def test_an_unparseable_yaml_body_returns_422_and_does_not_invalidate_or_poison(
    client: Any, projects: Any, server: Any
) -> None:
    """Branch 3: bad yaml is a 422, never calls invalidate, leaves prior entries alone.

    The shared ``client`` raises server exceptions; a ``MappingFileError`` from
    ``parse`` is uncaught at the route level today, so this case builds its own
    ``TestClient`` with ``raise_server_exceptions=False`` to assert the wire
    contract: a 422 naming the path, never a 5xx that costs a person their draft.
    """
    from fastapi.testclient import TestClient

    from graphxr_database_proxy.main import app as fastapi_app

    project = projects.by_name["mapped"]
    key = cache_key(project)
    sentinel = object()
    GRAPH_SCHEMA_CACHE.put(key, sentinel)  # type: ignore[arg-type]
    assert GRAPH_SCHEMA_CACHE.get(key) is sentinel

    calls, patcher = _install_spy()
    try:
        local = TestClient(fastapi_app, raise_server_exceptions=False)
        response = local.put(
            "/api/relational/mapped/mapping",
            json={"yaml": "foo: [unclosed bracket\n  then: open"},
        )
    finally:
        patcher.stop()

    assert response.status_code == 422, (
        "bad yaml must answer 422 (the path to fix), not a 5xx; got {} body={}".format(
            response.status_code, response.text[:200]
        )
    )
    assert calls == [], "bad yaml must not call invalidate_project"
    assert GRAPH_SCHEMA_CACHE.get(key) is sentinel, (
        "the prior entry must survive a rejected save"
    )


@pytest.mark.parametrize("method,route", [("put", "mapping"), ("post", "validate")])
@pytest.mark.parametrize("body,path", [
    ({"yaml": "nodes: ["}, "line 1"),
    ({"yaml": "- a list"}, "file"),
    ({"yaml": "# no document"}, "file"),
    ({}, "file"),
    ({"model": {"nodes": [{"label": "Person", "typo": True}]}}, "nodes[0].typo"),
    ({"yaml": "notes: " + "x" * mapping_io.MAX_MAPPING_BYTES}, "file"),
])
def test_invalid_mapping_bodies_report_paths_without_saving(
    client, projects, server, method, route, body, path
):
    project = projects.by_name["mapped"]
    before = project.database_config.graph_mapping
    key = cache_key(project)
    sentinel = object()
    GRAPH_SCHEMA_CACHE.put(key, sentinel)
    response = getattr(client, method)("/api/relational/mapped/" + route, json=body)
    assert response.status_code == 422, response.text
    assert any(problem["path"] == path for problem in response.json()["detail"])
    assert projects.saved == []
    assert projects.by_name["mapped"].database_config.graph_mapping == before
    assert GRAPH_SCHEMA_CACHE.get(key) is sentinel
