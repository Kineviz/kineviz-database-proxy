"""
What a relational backend answers over the wire, pinned — and the copy the client keeps.

GraphXR holds a **second** copy of this record (`databases/proxy/capabilities.ts` in
graphxr-dev), filled in synchronously because the adapter needs it before any round-trip.
A copy that drifts fails exactly like a missing case in that table: both end at a statement
built from the bolt family's tokens — `ID(n) IN ["Person:1001"]` against a key column — and
the user reads the engine's binder error. Nothing in either repo catches that, so this file
does: it answers the route, diffs the answer against the client's copy field by field, and
fails on any field the two no longer share.

The backends come from the driver registry rather than a typed-out list, so the fourth
relational engine fails here until somebody records it. See
`tests/test_relational_dialect_record.py` for the legacy-mode profile, and
`doc/RELATIONAL_API.md`.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from graphxr_database_proxy.api import database as database_module
from graphxr_database_proxy.contract.capabilities import CONTRACT_VERSION
from graphxr_database_proxy.drivers.factory import DriverFactory
from graphxr_database_proxy.drivers.graph_support import (
    MSSQL_CAPABILITIES,
    MYSQL_CAPABILITIES,
    SNOWFLAKE_CAPABILITIES,
    POSTGRESQL_CAPABILITIES,
)
from graphxr_database_proxy.drivers.relational.driver import RelationalDriver
from graphxr_database_proxy.drivers.relational.engine import PROPERTY_TYPES
from graphxr_database_proxy.main import app
from graphxr_database_proxy.models.project import DatabaseType, Project
from tests.test_relational_endpoints import ScriptedProjects
from tests.test_relational_pool import FakeEngine, access_for, config, draft_document
from tests.test_relational_reflect import SPEC, FakeInspector

#: Every relational engine this proxy serves, read off the registry rather than typed out:
#: a new one registered against ``RelationalDriver`` joins this list by itself.
def relational_backends() -> List[DatabaseType]:
    return sorted(
        (
            database_type
            for database_type, driver_class in DriverFactory._drivers.items()
            if issubclass(driver_class, RelationalDriver)
        ),
        key=lambda database_type: database_type.value,
    )


RELATIONAL_BACKENDS = relational_backends()

#: The golden document, field for field, as `GET /api/{backend}/{project}/capabilities`
#: answers it for ``mssql``. The three engines' records are identical modulo ``type`` —
#: verified rather than assumed by ``test_the_three_records_differ_in_nothing_but_their_name``
#: in ``tests/test_driver_intents.py`` — so one body here and one name per backend.
GOLDEN_DOCUMENT: Dict[str, Any] = {
    "type": "mssql",
    "queryLanguages": ["cypher"],
    "defaultQueryLanguage": "cypher",
    "identity": {"nodeId": "label-key"},
    "expand": {
        "predicate": "primary-key",
        "directions": ["all", "from", "to"],
        "multiHop": True,
        "relationshipTypeFilter": True,
        "internalRelationships": False,
        "excludeRelationshipIds": False,
    },
    "schema": {"source": "proxy", "refreshOnWrite": False},
    "fulltextSearch": {"supported": False, "manageIndex": False},
    "pull": {"category": True, "relationship": True, "excludeLoaded": True},
    "multiDatabase": False,
    "write": False,
    "intents": ["expand", "pullCategory", "pullRelationship"],
    "contractVersion": CONTRACT_VERSION,
}

_ABSENT = "<absent>"

#: The client's static copy, transcribed from `relationalCapabilities()` in
#: `databases/proxy/capabilities.ts` with `...SHARED` applied and the defaults
#: `defineCapabilities()` fills in (`shared/graphdb/capabilities.ts:222-258`) resolved.
#: `type` is that helper's first argument, `"databaseProxy"` — the adapter's own name, not
#: the backend's; `intents` is deliberately absent because `supportedIntents()`
#: (`index.ts:101-106`) negotiates it from the live document instead.
CLIENT_STATIC_RECORD: Dict[str, Any] = {
    "type": "databaseProxy",
    "queryLanguages": ["cypher"],
    "defaultQueryLanguage": "cypher",
    "identity": {"nodeId": "label-key"},
    "expand": {
        "predicate": "primary-key",
        "directions": ["all", "from", "to"],
        "multiHop": True,
        "relationshipTypeFilter": True,
        "internalRelationships": False,
        "excludeRelationshipIds": False,
    },
    "schema": {"source": "proxy", "refreshOnWrite": False},
    "fulltextSearch": {"supported": False, "manageIndex": False},
    "pull": {"category": True, "relationship": True, "excludeLoaded": True},
    "multiDatabase": False,
    "write": False,
}

#: Every field path the two copies do not share, and why. A new entry is not a note to
#: write — it is the drift this file exists to catch, and it belongs to whoever adds it.
MIRROR_DRIFT: Dict[str, str] = {
    "type": 'the client names the adapter ("databaseProxy"), the proxy names the backend',
    "intents": "negotiated live by supportedIntents(), on purpose (index.ts:101-106)",
    "contractVersion": "only the proxy knows which contract it was built against",
}


def document_for(backend: DatabaseType) -> Dict[str, Any]:
    golden = dict(GOLDEN_DOCUMENT)
    golden["type"] = backend.value
    return golden


def _flatten(document: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    # Leaf values keep their identity, so a list compares in order: a reordered
    # `directions` is a drift here, not a wash.
    flat: Dict[str, Any] = {}
    for key, value in document.items():
        path = prefix + str(key)
        if isinstance(value, dict):
            flat.update(_flatten(value, path + "."))
        else:
            flat[path] = value
    return flat


def _delta(client: Dict[str, Any], proxy: Dict[str, Any]) -> Dict[str, Any]:
    """Paths the two records disagree on, as ``(client value, proxy value)``."""
    left, right = _flatten(client), _flatten(proxy)
    absent = _ABSENT
    return {
        path: (left.get(path, absent), right.get(path, absent))
        for path in sorted(set(left) | set(right))
        if left.get(path, absent) != right.get(path, absent)
    }


def relational_project(backend: DatabaseType) -> Project:
    """
    One project per relational backend, its mapping the draft discovery proposes for the
    shared spec. Built in memory — `config/projects.json` holds live credentials — and the
    port stays SQL Server's because nothing dials it: every driver here gets a fake pool.
    """
    made = config(type=backend, graph_mapping=draft_document().model_dump(by_alias=True))
    return Project(
        id=backend.value, name=backend.value, database_type=backend, database_config=made
    )


def wired_driver(target: Project) -> Any:
    """A production driver — class, capability record, dialect and engine spelling — over fakes."""
    made = DriverFactory.create_driver(target)
    made._access = access_for(
        target, spelling=made.spelling, engine=FakeEngine(), inspector=FakeInspector(SPEC)
    )
    return made


@pytest.fixture
def capability_client():
    """The real route, over scripted projects and no credentials and no live config."""
    projects = ScriptedProjects()
    for backend in RELATIONAL_BACKENDS:
        projects.add(relational_project(backend))
    app.dependency_overrides[database_module.get_project_service] = lambda: projects
    app.dependency_overrides[database_module.verify_api_key_or_admin] = lambda: None
    with TestClient(app) as made:
        yield made
    app.dependency_overrides.pop(database_module.get_project_service, None)
    app.dependency_overrides.pop(database_module.verify_api_key_or_admin, None)


def answer(capability_client: Any, backend: DatabaseType) -> Dict[str, Any]:
    reply = capability_client.get(f"/api/{backend.value}/{backend.value}/capabilities")
    assert reply.status_code == 200, reply.text
    # The document itself, not wrapped in {success, data} like the other read routes: that
    # is the contract, and a wrapper here would read as an empty capability set.
    return reply.json()


# -- the guard: a fourth engine cannot skip this ----------------------------------------

def test_the_backends_under_test_are_the_ones_the_factory_registers() -> None:
    """
    Derived, not typed: `mongodb` is a `DatabaseType` with no driver and so is absent, while
    a relational engine added tomorrow appears here without anyone editing this file.
    """
    derived = [database_type.value for database_type in RELATIONAL_BACKENDS]
    assert derived == sorted(derived)
    assert derived, "no relational driver is registered — the derivation is broken, not empty"
    registered = [
        database_type.value
        for database_type, driver_class in DriverFactory._drivers.items()
        if issubclass(driver_class, RelationalDriver)
    ]
    assert sorted(registered) == derived


def test_every_relational_backend_has_a_recorded_golden_document() -> None:
    """The bite of the guard: an unrecorded backend fails here before anything else does."""
    recorded = {backend.value for backend in RELATIONAL_BACKENDS}
    assert recorded == {"mssql", "postgresql", "mysql", "snowflake"}


# -- the golden document ----------------------------------------------------------------

@pytest.mark.parametrize("backend", RELATIONAL_BACKENDS, ids=lambda item: item.value)
def test_the_wire_document_is_exactly_the_recorded_one(capability_client: Any, backend: DatabaseType) -> None:
    assert answer(capability_client, backend) == document_for(backend)


@pytest.mark.parametrize("backend", RELATIONAL_BACKENDS, ids=lambda item: item.value)
def test_the_intents_are_listed_so_the_client_never_falls_back_to_legacy(
    capability_client: Any, backend: DatabaseType
) -> None:
    """
    The one field the client reads live. Absent or empty, `supportedIntents()` answers `[]`
    and every intent is built in the browser from the legacy profile — which is the whole
    failure path this file guards, so it is asserted separately from the document equality.
    """
    intents = answer(capability_client, backend)["intents"]
    assert intents == ["expand", "pullCategory", "pullRelationship"]
    assert "search" not in intents, "no relational engine has a full-text index to point at"


@pytest.mark.parametrize("backend", RELATIONAL_BACKENDS, ids=lambda item: item.value)
def test_a_primary_key_backend_reports_the_identity_trio_that_makes_its_predicate_legal(
    capability_client: Any, backend: DatabaseType
) -> None:
    """
    Three fields that must arrive together. `label-key` ids without a `primary-key`
    predicate, or either without a schema source that supplies category keys, is a seed that
    cannot be re-selected — `assert_capabilities_consistent` says so at import time, and the
    route would have answered something else.
    """
    body = answer(capability_client, backend)
    assert body["identity"]["nodeId"] == "label-key"
    assert body["expand"]["predicate"] == "primary-key"
    assert body["schema"]["source"] == "proxy"


# -- the mirror -------------------------------------------------------------------------

@pytest.mark.parametrize("backend", RELATIONAL_BACKENDS, ids=lambda item: item.value)
def test_the_clients_copy_shares_every_field_it_carries(capability_client: Any, backend: DatabaseType) -> None:
    """
    Field by field, and the disagreement is the assertion: the only paths allowed to differ
    are the three recorded in `MIRROR_DRIFT`. A fourth means one repo changed a contract
    field the other still describes differently, which is how a mirrored record drifts
    silently.
    """
    delta = _delta(CLIENT_STATIC_RECORD, answer(capability_client, backend))
    assert set(delta) == set(MIRROR_DRIFT), {path: delta[path] for path in set(delta) ^ set(MIRROR_DRIFT)}
    assert delta["type"] == ("databaseProxy", backend.value)
    assert delta["intents"] == (_ABSENT, GOLDEN_DOCUMENT["intents"])
    assert delta["contractVersion"] == (_ABSENT, CONTRACT_VERSION)


def test_the_declared_records_are_the_document_the_route_answers() -> None:
    """
    The record a driver carries and the body on the wire are one fact, not two to keep in step:
    the route re-wraps it in `CapabilityReport`, and that adds only the contract version.
    """
    declared = {
        MSSQL_CAPABILITIES.type: MSSQL_CAPABILITIES,
        POSTGRESQL_CAPABILITIES.type: POSTGRESQL_CAPABILITIES,
        MYSQL_CAPABILITIES.type: MYSQL_CAPABILITIES,
        SNOWFLAKE_CAPABILITIES.type: SNOWFLAKE_CAPABILITIES,
    }
    for backend in RELATIONAL_BACKENDS:
        record = declared[backend.value]
        assert record.model_dump(by_alias=True) == {
            key: value for key, value in document_for(backend).items() if key != "contractVersion"
        }


# -- what the schema surface owes a primary-key backend ---------------------------------

@pytest.mark.parametrize("backend", RELATIONAL_BACKENDS, ids=lambda item: item.value)
async def test_every_category_carries_the_key_a_seed_is_addressed_by(backend: DatabaseType) -> None:
    """
    Duty one. `_resolve_refs` (drivers/intents.py:137-140) drops a seed whose label has no
    key, so an empty `keys` is not an error but an `/expand` that answers an empty graph.
    """
    schema = await wired_driver(relational_project(backend)).get_graph_schema()
    assert schema.success, schema.error
    assert schema.data.categories
    for category in schema.data.categories:
        assert category.keys, category.name
        assert all(key in category.props for key in category.keys), category.name
        assert set(category.keysTypes) == set(category.keys), category.name


@pytest.mark.parametrize("backend", RELATIONAL_BACKENDS, ids=lambda item: item.value)
async def test_a_node_id_resolves_to_a_predicate_on_the_real_key(backend: DatabaseType) -> None:
    """
    The consequence of duty one, on a cold driver: the intent layer must load the schema
    itself (no earlier call remembered it, because the API builds a driver per request) and
    turn `<Label>:<key>` into the key property that column actually is.
    """
    driver = wired_driver(relational_project(backend))
    await driver._ensure_graph_categories()
    refs = driver._resolve_refs(["Person:1001"])
    assert len(refs) == 1, "the seed was dropped: /expand would answer an empty graph"
    assert refs[0].category == "Person"
    assert refs[0].key_prop == "person_id"
    assert refs[0].key_value == "1001"


@pytest.mark.parametrize("backend", RELATIONAL_BACKENDS, ids=lambda item: item.value)
async def test_graph_key_types_names_every_category_in_the_shared_vocabulary(backend: DatabaseType) -> None:
    """
    Duty two. `render_key_literal` decides from this map whether a seed is quoted, so a
    numeric column missing here — or normalised to something outside
    `dialect._NUMERIC_KEY_TYPES` — arrives quoted and stops matching its index.
    """
    driver = wired_driver(relational_project(backend))
    schema = await driver.get_graph_schema()
    key_types = driver.graph_key_types()
    labels = {category.name for category in schema.data.categories}
    assert set(key_types) == labels
    for category in schema.data.categories:
        from graphxr_database_proxy.drivers.relational.engine import PROPERTY_TYPES

        declared = key_types[category.name]
        assert declared in PROPERTY_TYPES, category.name
        # The shared spec's numeric keys have to stay numeric on all three engines.
        if category.name in {"Person", "Order", "Organization", "TreeNode"}:
            assert declared in {"INT64", "INT32", "DOUBLE", "DECIMAL"}, category.name
    assert key_types["Session"] == "STRING", "a token key is text and must be quoted"


@pytest.mark.parametrize("backend", RELATIONAL_BACKENDS, ids=lambda item: item.value)
async def test_one_relationship_per_link_type_with_both_ends_named(backend: DatabaseType) -> None:
    """
    Duty three. The contract allows one `startCategory`/`endCategory` per relationship, so a
    polymorphic link shows its largest pair — and an edge that names no record is dropped
    rather than drawn with a blank end, because a relationship nobody can click is worse.
    """
    driver = wired_driver(relational_project(backend))
    mapping = await driver.effective_mapping()
    schema = await driver.get_graph_schema()
    labels = {category.name for category in schema.data.categories}
    names = [relationship.name for relationship in schema.data.relationships]
    assert names == sorted(set(names)) or len(names) == len(set(names)), names
    assert len(names) == len(set(names))
    assert set(names) <= {edge.type.upper() for edge in mapping.edges.values()}, names
    for relationship in schema.data.relationships:
        assert relationship.startCategory in labels, relationship.name
        assert relationship.endCategory in labels, relationship.name
