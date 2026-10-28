# -*- coding: utf-8 -*-
"""
The configure-an-embedded-store endpoints.

Real routing and real file handling; the engine resolver is stubbed, because what
is under test is what the form sees -- the identification, the refusal of a file
that is not a store, and the refusal to overwrite one that is.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kineviz_database_proxy.api import embedded as embedded_module
from kineviz_database_proxy.api.embedded import get_project_service, safe_name
from kineviz_database_proxy.main import app
from kineviz_database_proxy.models.project import (
    AuthType,
    DatabaseConfig,
    DatabaseType,
    Project,
)

KUZU_HEADER = b"KUZU" + (39).to_bytes(8, "little")
LADYBUG_HEADER = b"LBUG" + (43).to_bytes(8, "little")


class ScriptedEngineService:
    """Resolves without touching PyPI, and records what was asked to install."""

    def __init__(self):
        self.installs = []
        from kineviz_database_proxy.drivers.embedded.version_map import VersionMap

        self.version_map = VersionMap()

    async def candidates(self, engine, storage_version, pin=None):
        if pin:
            return [pin]
        return self.version_map.candidates(engine, storage_version)

    def status(self, engine, version):
        from kineviz_database_proxy.drivers.embedded.engine_service import EngineStatus

        return EngineStatus(engine=engine, version=version, status="absent")

    def statuses(self):
        return []

    def start_install(self, engine, version):
        from kineviz_database_proxy.drivers.embedded.engine_service import EngineStatus

        self.installs.append((engine, version))
        return EngineStatus(engine=engine, version=version, status="installing", detail="queued")

    async def uninstall(self, engine, version):
        return True

    def local_candidates(self, engine, storage_version, pin=None):
        if pin:
            return [pin]
        return self.version_map.candidates(engine, storage_version)


class ScriptedProjects:
    """The projects a listing annotates against, without touching projects.json."""

    def __init__(self, projects=()):
        self.projects = list(projects)

    async def list_projects(self):
        return self.projects


def project_at(name, path, database_type=DatabaseType.KUZU):
    return Project(
        id=name,
        name=name,
        database_type=database_type,
        database_config=DatabaseConfig(
            type=database_type,
            auth_type=AuthType.USERNAME_PASSWORD,
            database_path=str(path),
        ),
    )


@pytest.fixture
def engine(monkeypatch):
    service = ScriptedEngineService()
    monkeypatch.setattr(embedded_module, "ENGINE_SERVICE", service)
    monkeypatch.setattr(embedded_module, "installed_runtime", lambda _e, _v: None)
    return service


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setenv("GRAPHXR_PROXY_STORES_DIR", str(tmp_path / "databases"))
    return tmp_path / "databases"


@pytest.fixture
def projects():
    """The live projects.json is never read here -- it holds real credentials."""
    scripted = ScriptedProjects()
    app.dependency_overrides[get_project_service] = lambda: scripted
    yield scripted
    app.dependency_overrides.pop(get_project_service, None)


@pytest.fixture
def client(engine, stores, projects):
    with TestClient(app) as made:
        yield made


def write_store(tmp_path, header=KUZU_HEADER, name="graph.kz"):
    path = tmp_path / name
    path.write_bytes(header + b"\x00" * 64)
    return path


# -- name safety ------------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("my project", "myproject"),
        ("../../etc/passwd", "etcpasswd"),
        ("..", "fallback"),
        ("", "fallback"),
        ("ok-name_1.kz", "ok-name_1.kz"),
    ],
)
def test_a_form_supplied_name_cannot_escape_its_directory(given, expected):
    # These become a path segment, so anything outside the allowlist is dropped
    # rather than escaped.
    cleaned = safe_name(given, "fallback")
    assert cleaned == expected
    assert "/" not in cleaned and "\\" not in cleaned and cleaned != ".."


# -- inspect ----------------------------------------------------------------


def test_a_path_is_identified_and_an_engine_release_resolved(client, tmp_path):
    store = write_store(tmp_path, LADYBUG_HEADER)

    body = client.post("/api/embedded/inspect", json={"path": str(store)}).json()

    assert body["success"] is True
    assert body["engine"] == "ladybug"
    assert body["storage_version"] == 43
    assert body["description"] == "Ladybug store, storage version 43"
    assert body["resolved_version"] == "0.19.1"
    assert body["engine_status"]["installed"] is False


def test_a_pin_narrows_the_answer(client, tmp_path):
    store = write_store(tmp_path)
    body = client.post(
        "/api/embedded/inspect", json={"path": str(store), "engine_version": "0.11.0"}
    ).json()
    assert body["resolved_version"] == "0.11.0"


def test_a_directory_store_is_identified_through_its_catalog(client, tmp_path):
    # Kuzu 0.10 and older. It cannot be dragged, but it can be configured by path,
    # and this is where the user finds that out.
    store = tmp_path / "olddb"
    store.mkdir()
    (store / "catalog.kz").write_bytes(b"KUZU" + (38).to_bytes(8, "little"))

    body = client.post("/api/embedded/inspect", json={"path": str(store)}).json()

    assert body["layout"] == "directory"
    assert body["resolved_version"] == "0.10.0"


def test_a_path_that_is_not_a_store_is_an_answer_not_an_error(client, tmp_path):
    # The user is probably still typing; a 500 in the form would be wrong.
    notes = tmp_path / "notes.txt"
    notes.write_text("hello", encoding="utf-8")

    response = client.post("/api/embedded/inspect", json={"path": str(notes)})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "magic number" in body["error"] or "too small" in body["error"]


def test_a_format_nothing_can_read_says_so_rather_than_resolving(client, tmp_path):
    store = write_store(tmp_path, b"KUZU" + (99).to_bytes(8, "little"))

    body = client.post("/api/embedded/inspect", json={"path": str(store)}).json()

    assert body["success"] is True
    assert body["resolved_version"] is None
    assert "storage version 99" in body["error"]


# -- upload -----------------------------------------------------------------


def test_a_dropped_store_is_saved_and_identified(client, stores):
    payload = KUZU_HEADER + b"\x00" * 4096

    body = client.post(
        "/api/embedded/upload",
        files={"file": ("graph.kz", io.BytesIO(payload), "application/octet-stream")},
        data={"project": "demo"},
    ).json()

    assert body["success"] is True
    assert body["engine"] == "kuzu"
    saved = Path(body["path"])
    assert saved == (stores / "demo" / "graph.kz").resolve() or saved.exists()
    assert saved.read_bytes() == payload


def test_a_file_that_is_not_a_store_is_rejected_by_its_first_bytes(client, stores):
    # Rejected as soon as the header is wrong, so a large wrong file is not written
    # out in full first.
    response = client.post(
        "/api/embedded/upload",
        files={"file": ("photo.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"x" * 4096), "image/png")},
        data={"project": "demo"},
    )

    assert response.status_code == 400
    assert "magic number" in response.json()["detail"]
    assert list((stores / "demo").glob("*")) == []


def test_a_file_too_short_to_have_a_header_is_rejected(client, stores):
    response = client.post(
        "/api/embedded/upload",
        files={"file": ("tiny.kz", io.BytesIO(b"KUZU"), "application/octet-stream")},
        data={"project": "demo"},
    )
    assert response.status_code == 400
    assert "too small" in response.json()["detail"]


def test_an_existing_store_is_not_silently_replaced(client, stores):
    payload = KUZU_HEADER + b"\x00" * 16
    first = client.post(
        "/api/embedded/upload",
        files={"file": ("graph.kz", io.BytesIO(payload), "application/octet-stream")},
        data={"project": "demo"},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/embedded/upload",
        files={"file": ("graph.kz", io.BytesIO(LADYBUG_HEADER + b"\x00" * 16), "application/octet-stream")},
        data={"project": "demo"},
    )

    assert second.status_code == 409
    assert "overwrite=true" in second.json()["detail"]
    # And the original is untouched.
    assert (stores / "demo" / "graph.kz").read_bytes() == payload


def test_overwrite_replaces_it_when_it_is_asked_for(client, stores):
    for header in (KUZU_HEADER, LADYBUG_HEADER):
        response = client.post(
            "/api/embedded/upload",
            files={"file": ("graph.kz", io.BytesIO(header + b"\x00" * 16), "application/octet-stream")},
            data={"project": "demo", "overwrite": "true"},
        )
        assert response.status_code == 200
    assert (stores / "demo" / "graph.kz").read_bytes().startswith(b"LBUG")


def test_an_upload_past_the_size_limit_is_refused_and_leaves_nothing_behind(
    client, stores, monkeypatch
):
    monkeypatch.setenv("GRAPHXR_PROXY_MAX_UPLOAD_BYTES", "64")

    response = client.post(
        "/api/embedded/upload",
        files={"file": ("graph.kz", io.BytesIO(KUZU_HEADER + b"\x00" * 4096), "application/octet-stream")},
        data={"project": "demo"},
    )

    assert response.status_code == 413
    assert list((stores / "demo").glob("*")) == []


def test_an_upload_without_a_project_still_lands_somewhere_safe(client, stores):
    body = client.post(
        "/api/embedded/upload",
        files={"file": ("graph.kz", io.BytesIO(KUZU_HEADER + b"\x00" * 16), "application/octet-stream")},
    ).json()
    assert body["success"] is True
    assert "unassigned" in body["path"]


# -- the store library ------------------------------------------------------


def drop(client, name, project="demo", header=KUZU_HEADER):
    response = client.post(
        "/api/embedded/upload",
        files={"file": (name, io.BytesIO(header + b"\x00" * 64), "application/octet-stream")},
        data={"project": project},
    )
    assert response.status_code == 200, response.text
    return response.json()["path"]


def test_the_library_lists_what_was_dropped_into_it(client, stores):
    drop(client, "graph.kz", "demo")
    drop(client, "other.kz", "demo", LADYBUG_HEADER)

    body = client.get("/api/embedded/stores").json()

    assert body["total"] == 2
    assert [item["name"] for item in body["items"]] == ["graph.kz", "other.kz"]
    assert [item["engine"] for item in body["items"]] == ["kuzu", "ladybug"]
    assert all(item["folder"] == "demo" for item in body["items"])
    assert body["folders"] == ["demo"]
    assert Path(body["root"]) == stores


def test_a_listing_names_an_engine_release_without_reaching_for_the_network(client):
    drop(client, "graph.kz")

    item = client.get("/api/embedded/stores").json()["items"][0]

    assert item["resolved_version"] == "0.11.3"
    assert item["engine_installed"] is False


def test_the_listing_is_paged_and_the_pages_partition_the_library(client):
    for index in range(5):
        drop(client, f"store-{index}.kz")

    first = client.get("/api/embedded/stores?offset=0&limit=2").json()
    second = client.get("/api/embedded/stores?offset=2&limit=2").json()
    third = client.get("/api/embedded/stores?offset=4&limit=2").json()

    assert [len(page["items"]) for page in (first, second, third)] == [2, 2, 1]
    assert all(page["total"] == 5 for page in (first, second, third))
    names = [item["name"] for page in (first, second, third) for item in page["items"]]
    assert names == [f"store-{i}.kz" for i in range(5)]


def test_search_filters_the_listing(client):
    drop(client, "customers.kz")
    drop(client, "orders.kz")

    body = client.get("/api/embedded/stores?search=order").json()

    assert body["total"] == 1
    assert body["items"][0]["name"] == "orders.kz"


def test_a_listing_says_which_projects_hold_each_store(client, projects):
    path = drop(client, "graph.kz")
    drop(client, "spare.kz")
    projects.projects = [project_at("live", path)]

    body = client.get("/api/embedded/stores").json()
    holders = {item["name"]: item["used_by"] for item in body["items"]}

    assert holders == {"graph.kz": ["live"], "spare.kz": []}


def test_stores_kept_outside_the_library_are_offered_only_when_asked_for(
    client, projects, tmp_path
):
    outside = tmp_path / "elsewhere.kz"
    outside.write_bytes(LADYBUG_HEADER + b"\x00" * 16)
    projects.projects = [project_at("remote", outside, DatabaseType.LADYBUG)]

    assert client.get("/api/embedded/stores").json()["external"] == []

    external = client.get("/api/embedded/stores?include_external=true").json()["external"]
    assert len(external) == 1
    assert Path(external[0]["path"]) == outside
    assert external[0]["database_type"] == "ladybug"
    assert external[0]["used_by"] == ["remote"]


def test_a_store_is_deleted_along_with_the_folder_it_empties(client, stores):
    drop(client, "graph.kz", "demo")

    response = client.request("DELETE", "/api/embedded/stores", params={"path": "demo/graph.kz"})

    assert response.status_code == 200
    assert not (stores / "demo").exists()
    assert client.get("/api/embedded/stores").json()["total"] == 0


def test_a_store_a_project_still_points_at_is_not_deleted_on_the_first_ask(
    client, stores, projects
):
    path = drop(client, "graph.kz")
    projects.projects = [project_at("live", path)]

    refused = client.request("DELETE", "/api/embedded/stores", params={"path": "demo/graph.kz"})

    assert refused.status_code == 409
    assert "live" in refused.json()["detail"]
    assert Path(path).exists()

    forced = client.request(
        "DELETE", "/api/embedded/stores", params={"path": "demo/graph.kz", "force": "true"}
    )
    assert forced.status_code == 200
    assert forced.json()["used_by"] == ["live"]
    assert not Path(path).exists()


def test_a_delete_cannot_reach_outside_the_library(client, stores, tmp_path):
    victim = tmp_path / "precious.kz"
    victim.write_bytes(KUZU_HEADER)
    stores.mkdir(parents=True, exist_ok=True)

    response = client.request(
        "DELETE", "/api/embedded/stores", params={"path": "../precious.kz"}
    )

    assert response.status_code == 400
    assert victim.exists()


def test_a_file_the_proxy_cannot_serve_is_still_listed_by_name(client, stores):
    # Not uploadable -- the upload route refuses anything without an engine magic
    # number -- but a file manager has to show what is on the disk regardless.
    (stores / "demo").mkdir(parents=True, exist_ok=True)
    (stores / "demo" / "notes.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 32)

    item = client.get("/api/embedded/stores").json()["items"][0]

    assert item["kind"] == "sqlite"
    assert item["servable"] is False
    assert item["engine"] is None


# -- engines ----------------------------------------------------------------


def test_installing_an_engine_returns_at_once_rather_than_waiting(client, engine):
    body = client.post("/api/embedded/engines/kuzu/0.11.3/install").json()

    assert body["status"] == "installing"
    assert engine.installs == [("kuzu", "0.11.3")]


def test_an_unknown_engine_name_is_refused(client):
    assert client.post("/api/embedded/engines/postgres/1.0/install").status_code == 400
    assert client.delete("/api/embedded/engines/postgres/1.0").status_code == 400


def test_uninstalling_reports_what_it_did(client):
    body = client.delete("/api/embedded/engines/kuzu/0.11.3").json()
    assert body == {"removed": True, "engine": "kuzu", "version": "0.11.3"}


def test_the_known_formats_route_separates_an_unknown_format_from_a_missing_engine(client):
    body = client.get("/api/embedded/known-formats").json()

    assert 39 in body["kuzu"]["formats"]
    assert 43 in body["ladybug"]["formats"]
    assert body["kuzu"]["newestKnownRelease"] == "0.11.3"


def test_the_embedded_routes_are_not_swallowed_by_the_generic_database_route(client):
    # /api/{database_type}/{project_name} would match /api/embedded/engines if this
    # router were included after it, and the form would get "Project not found".
    assert client.get("/api/embedded/engines").status_code == 200
