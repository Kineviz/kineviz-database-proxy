"""
The mapping page's routes, over a connection that connects to nothing.

No DBAPI is imported here and ``config/projects.json`` is never opened — it holds live
credentials — so the projects live in a scripted dict and the driver's access is the same
fake pool the driver tests read through. What these cases prove is the wire contract the
editor is built against: the field names it reads, how far a page reflects, and that a
failure arrives as a 4xx naming the control that caused it instead of a 500 that costs the
person their draft.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from graphxr_database_proxy.api import database as database_module
from graphxr_database_proxy.api import relational as relational_module
from graphxr_database_proxy.api.auth import verify_admin_token
from graphxr_database_proxy.api.relational import get_project_service
from graphxr_database_proxy.drivers.relational.driver import RelationalDriver
from graphxr_database_proxy.drivers.relational.reads import VERDICTS
from graphxr_database_proxy.main import app
from graphxr_database_proxy.models.project import (
    AuthType,
    DatabaseConfig,
    DatabaseType,
    Project,
    ProjectUpdate,
)
from graphxr_database_proxy.services.graph_schema_cache import GRAPH_SCHEMA_CACHE
from tests.test_relational_pool import (
    FakeEngine,
    FakeInspector,
    FakeSpelling,
    access_for,
    config,
    mapped_project,
)
from tests.test_relational_reflect import SPEC

ORDERED = {
    "src": {"label": "Order", "column": "order_id", "column_type": "int"},
    "dst": {"label": "Person", "column": "customer_id", "column_type": "int"},
}


class ScriptedProjects:
    """
    The two calls these routes make on the service, answered from a dict.

    A save is recorded rather than written: the point of the PUT case is what the route does
    *after* the save, and the real service would persist to the live config directory.
    """

    def __init__(self) -> None:
        self.by_name: Dict[str, Project] = {}
        self.saved: List[str] = []

    def add(self, target: Project) -> Project:
        self.by_name[target.name] = target
        return target

    async def get_project_by_name(self, name: str) -> Optional[Project]:
        return self.by_name.get(name)

    async def update_project(self, project_id: str, update_data: ProjectUpdate) -> Optional[Project]:
        held = next((one for one in self.by_name.values() if one.id == project_id), None)
        if held is None:
            return None
        self.saved.append(project_id)
        changed = held.model_copy(
            update={"database_config": update_data.database_config or held.database_config}
        )
        self.by_name[changed.name] = changed
        return changed


@pytest.fixture
def projects():
    scripted = ScriptedProjects()
    scripted.add(mapped_project("mapped"))
    app.dependency_overrides[get_project_service] = lambda: scripted
    app.dependency_overrides[verify_admin_token] = lambda: None
    yield scripted
    app.dependency_overrides.pop(get_project_service, None)
    app.dependency_overrides.pop(verify_admin_token, None)


@pytest.fixture
def server():
    """A fake engine, spelling and catalog shared by every driver one test builds."""
    made = FakeServer()
    made.install()
    yield made
    made.uninstall()


class FakeServer:
    """
    The three seams a relational route reaches, swapped together.

    ``make_access`` is the driver's own one-line seam, so the production ``RelationalAccess``
    still walks the catalog and still pages it — only the inspector and the pool underneath it
    are fakes. That is what lets a case assert *how many names one reflection round trip was
    handed*, which is the promise the paging rule exists to keep.
    """

    def __init__(self) -> None:
        self.engine = FakeEngine()
        self.inspector = FakeInspector(SPEC)
        self.spelling = FakeSpelling()
        self._patch: Any = None

    def install(self) -> None:
        holder = self
        self._patch = patch.object(
            RelationalDriver,
            "make_access",
            lambda driver: access_for(
                driver.project,
                spelling=holder.spelling,
                engine=holder.engine,
                inspector=holder.inspector,
            ),
        )
        self._patch.start()

    def uninstall(self) -> None:
        self._patch.stop()

    def reflected(self) -> List[List[str]]:
        """Every set of names the walk asked columns for, in the order it asked."""
        return [list(one[1]) for one in self.inspector.calls if one[0] == "columns"]


@pytest.fixture
def client():
    with TestClient(app) as made:
        yield made


# ---------------------------------------------------------------------------
# the table list
# ---------------------------------------------------------------------------


def test_a_page_of_names_reflects_only_that_page(client, projects, server):
    """
    The picker's paging promise, measured at the inspector rather than in the JSON.

    ``total`` is the whole visible list because a DB-API inspector cannot offset names; the
    columns read behind it are capped at the page, and that cap is the difference between
    opening an ERP schema and opening twenty of its tables.
    """
    order = sorted(SPEC)
    at = order.index("Person") // 2 + 1
    server.spelling.counts = {"Person": 12}

    answer = client.get(
        "/api/relational/mapped/tables?page={}&page_size=2".format(at)
    ).json()

    assert answer["page"] == at and answer["page_size"] == 2
    assert answer["total"] == len(order)
    assert [one["table"] for one in answer["rows"]] == order[2 * (at - 1) : 2 * at]
    assert {"table", "schema", "columns", "row_estimate"} <= set(answer["rows"][0])
    assert all(len(one) <= 2 for one in server.reflected()), server.reflected()
    assert answer["rows"][order.index("Person") % 2]["row_estimate"] == 12


def test_a_search_narrows_the_names_before_anything_is_reflected(client, projects, server):
    """
    A search that fetched the library to filter it in the browser is the mistake here.

    The filter runs on names alone, so the reflection that follows is still one page.
    """
    answer = client.get("/api/relational/mapped/tables?search=order").json()

    assert [one["table"] for one in answer["rows"]] == ["Order"]
    assert answer["total"] == 1
    assert server.reflected() == [["Order"]]


def test_one_table_answers_the_column_picker(client, projects, server):
    """
    The read the key picker and the property list share.

    ``key_candidates`` is the catalog's judgement — a single-column primary key, else a
    single-column NOT NULL unique — and not something the page re-derives from per-column
    flags, because re-deriving it would offer the first half of a composite key.
    """
    answer = client.get("/api/relational/mapped/tables?include=columns&table=Person").json()

    assert answer["table"] == "Person"
    assert answer["key_candidates"] == ["person_id"]
    assert answer["total"] == 2, "total counts the columns, which is what the picker pages on"
    assert answer["columns"] == [
        {
            "name": "person_id",
            "type": "int",
            "nullable": False,
            "is_primary_key": True,
            "is_unique": False,
        },
        {
            "name": "full_name",
            "type": "varchar(100)",
            "nullable": True,
            "is_primary_key": False,
            "is_unique": False,
        },
    ]


def test_a_wide_table_pages_its_columns_and_keeps_every_candidate(client, projects, server):
    """
    A 300-column table arrives 100 at a time, with the key still offered.

    The candidates are computed over the whole table and not over the page: a key past the
    first hundred columns would otherwise disappear from the picker that needs it.
    """
    wide: Dict[str, Any] = {
        "Wide": {
            "columns": [
                ("c{:03d}".format(index), "int", index > 0) for index in range(300)
            ],
            "pk": ("PK_Wide", ["c250"]),
            "fks": [],
        },
        "Person": SPEC["Person"],
    }
    server.inspector = FakeInspector(wide)

    answer = client.get(
        "/api/relational/mapped/tables?include=columns&table=Wide&page_size=100&page=3"
    ).json()

    assert answer["total"] == 300 and len(answer["columns"]) == 100
    assert answer["columns"][0]["name"] == "c200"
    assert answer["key_candidates"] == ["c250"]


def test_a_name_that_is_not_a_table_is_answered_as_absent(client, projects, server):
    """
    A 404 that says what it is not, because a view not being a table is the common case.

    Discovery skips views and engine internals, so a person who typed a name the page never
    offered needs to learn which of the two it is — the answer is in the sentence.
    """
    answer = client.get("/api/relational/mapped/tables?table=Nope")

    assert answer.status_code == 404
    assert "not a table" in answer.json()["detail"]


def test_a_table_whose_statistics_stayed_silent_arrives_as_no_number(client, projects, server):
    """
    The read the picker's tooltip and the Rows column are both built on.

    ``counts`` stands for the engine's own statistics, which name only the tables they have
    actually counted — so a ``0`` here would be the proxy reporting an empty table on the
    engine's silence, which is every table of an unanalysed MySQL schema.
    """
    server.spelling.counts = {"Person": 12}

    listed = client.get("/api/relational/mapped/tables?page=1&page_size=100").json()
    by_name = {one["table"]: one["row_estimate"] for one in listed["rows"]}
    assert by_name["Person"] == 12
    assert by_name["Employment"] is None

    one = client.get("/api/relational/mapped/tables?include=columns&table=Employment").json()
    assert one["row_estimate"] is None


def test_discovery_tells_the_editor_which_links_it_never_counted(client, projects, server):
    """
    The proposal's own row counts, on the wire, with the gap kept.

    The editor sorts and ranks on what arrives here: an absent count that came over as a
    ``0`` would put an unmeasured link in the same pile as a genuinely empty one, and the
    pair it then reports is the pair the relationship gets drawn between.
    """
    server.spelling.counts = {"Employment": 1180}

    answer = client.post("/api/relational/mapped/discover", json={}).json()

    edges = {one["type"]: one for one in answer["model"]["edges"]}
    assert edges["EMPLOYMENT"]["endpoints"][0]["row_estimate"] == 1180
    link = edges["FK_ORDER_CUSTOMER"]["endpoints"][0]
    assert link["row_estimate"] is None
    assert "row_estimate" in link, "the gap is answered, not dropped from the payload"


# ---------------------------------------------------------------------------
# a connection that has not been saved
# ---------------------------------------------------------------------------


def test_a_draft_connection_gets_the_same_read_as_a_saved_one(client, projects, server):
    """
    The picker works before the password has been kept, and the project list is not consulted.

    The draft is addressed by ``draft:<name>`` inside the pool, which is what keeps a test
    connection from being served by — or serving — the saved project of the same name.
    """
    saved_before = list(projects.by_name)

    answer = client.post(
        "/api/relational/new-database/tables",
        json={"connection": config().model_dump(), "page_size": 3},
    ).json()

    assert len(answer["rows"]) == 3
    assert answer["total"] == len(SPEC)
    assert projects.saved == [] and list(projects.by_name) == saved_before


def test_a_draft_config_that_does_not_parse_names_the_field(client, projects, server):
    """
    422 with a path, not a sentence: this body is the form's own state.

    A person whose port field rejected a word is told which control is wrong, because the
    alternative — "invalid connection" — sends them looking through the whole form.
    """
    broken = config().model_dump()
    broken["port"] = "not-a-port"

    answer = client.post("/api/relational/new-database/tables", json={"connection": broken})

    assert answer.status_code == 422
    assert any(one["path"].startswith("port") for one in answer.json()["detail"])


def test_a_mongodb_project_is_refused_by_name(client, projects, server):
    """``/api/relational`` serves tables; a document store has none to serve."""
    projects.add(
        Project(
            id="doc-store",
            name="doc-store",
            database_type=DatabaseType.NEO4J,
            database_config=DatabaseConfig(
                type=DatabaseType.NEO4J,
                auth_type=AuthType.USERNAME_PASSWORD,
                uri="bolt://db:7687",
            ),
        )
    )

    answer = client.get("/api/relational/doc-store/tables")

    assert answer.status_code == 400
    assert "neo4j" in answer.json()["detail"]


def test_a_database_that_needs_a_package_answers_the_install_line(client, projects):
    """
    No fake access here on purpose: this is the production pool meeting a missing DBAPI.

    The answer must be the engine's own install sentence at 400. It is a server-configuration
    fault a person can fix, and a 500 would bury the one word that tells them what to install.
    """
    answer = client.get("/api/relational/mapped/tables")

    assert answer.status_code == 400
    assert "pip install" in answer.json()["detail"]


def test_the_generic_surface_offers_the_intents_so_the_client_stays_in_intent_mode(
    client, projects, server
):
    """
    ``/api/mssql/<project>/capabilities`` has to list the intents, or the adapter guesses.

    A proxy with no ``/capabilities``, or one that lists none, puts GraphXR in *legacy* mode,
    where it builds the statement from its own static profile for the backend — and the
    fallback profile for an unknown backend is the bolt family: Cypher with ``ID(n)``, which
    is the wrong answer for a table and the one that gave a Kuzu project a string list against
    an internal-id column.
    """
    app.dependency_overrides[database_module.get_project_service] = lambda: projects

    try:
        answer = client.get("/api/mssql/mapped/capabilities")
    finally:
        app.dependency_overrides.pop(database_module.get_project_service, None)

    assert answer.status_code == 200
    body = answer.json()
    assert "expand" in body["intents"]
    assert body["identity"]["nodeId"] == "label-key"
    assert body["expand"]["predicate"] == "primary-key"


# ---------------------------------------------------------------------------
# the direction check (three cases moved to tests/test_relational_preview_route.py)
# ---------------------------------------------------------------------------


# moved to tests/test_relational_preview_route.py: test_a_pair_that_joins_in_both_directions_settles_as_you_decide
# moved to tests/test_relational_preview_route.py: test_an_empty_join_in_either_direction_is_neither_and_never_a_success
# moved to tests/test_relational_preview_route.py: test_a_request_that_names_no_column_is_refused_on_its_own_field


# ---------------------------------------------------------------------------
# checking a draft
# ---------------------------------------------------------------------------


def test_a_checked_draft_comes_back_as_the_document_the_tabs_edit(client, projects, server):
    """
    An upload or a pasted file is applied to the structured tabs from *this* answer, so a reply
    that carries only its text rendering replaces forty rows with none.

    The route already holds the document — it counts its entries and renders its file from it —
    and `model` is the name every other mapping route uses, so the editor never has to parse.
    Echoing it has to be idempotent: what comes back must check the same way it went in.
    """
    document = mapped_project("saved").database_config.graph_mapping

    answer = client.post("/api/relational/mapped/validate", json={"model": document}).json()

    assert answer["valid"] is True
    echoed = answer["model"]
    assert [node["label"] for node in echoed["nodes"]] == [
        node["label"] for node in document["nodes"]
    ]
    assert answer["nodes"] == len(echoed["nodes"]) and answer["edges"] == len(echoed["edges"])

    again = client.post("/api/relational/mapped/validate", json={"model": echoed}).json()
    assert again["valid"] is True and again["model"] == echoed


# ---------------------------------------------------------------------------
# saving
# ---------------------------------------------------------------------------


def test_saving_a_mapping_drops_the_schema_cache_and_the_pool(client, projects, server):
    """
    Both, because a new mapping changes what a seed id resolves to.

    A stale graph schema would keep answering with the labels that were just replaced, and a
    pool left open against a project whose mapping just changed has no reason to stay open.
    """
    document = mapped_project("saved").database_config.graph_mapping
    disposed: List[str] = []
    invalidated: List[str] = []

    with patch.object(relational_module.POOL, "dispose_project", disposed.append), patch.object(
        GRAPH_SCHEMA_CACHE, "invalidate_project", invalidated.append
    ):
        answer = client.put("/api/relational/mapped/mapping", json={"model": document}).json()

    assert answer["saved"] is True and answer["valid"] is True
    assert disposed == ["mapped"] and invalidated == ["mapped"]
