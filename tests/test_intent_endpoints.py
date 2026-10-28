"""
The typed intent endpoints and the Private Network Access preflight.

Uses a stub driver rather than a real database: what is under test is the routing,
the capability gate and the middleware, not any backend's SQL.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kineviz_database_proxy.contract import GraphDbCapabilities
from kineviz_database_proxy.contract.intents import ExpandRequest, PullCategoryRequest
from kineviz_database_proxy.drivers.dialect import ROCKETGRAPH_DIALECT, SPANNER_DIALECT
from kineviz_database_proxy.drivers.graph_support import GraphIntentSupport
from kineviz_database_proxy.drivers.intents import GraphIntentMixin, merge_graph_results, split_label_key_id
from kineviz_database_proxy.models.project import (
    Category,
    GraphData,
    GraphSchema,
    GraphSchemaResponse,
    Node,
    QueryData,
)


# ---------------------------------------------------------------------------
# split_label_key_id
# ---------------------------------------------------------------------------

def test_label_key_ids_split_on_the_first_colon():
    assert split_label_key_id("Person:42") == ("Person", "42")
    # A key containing colons survives.
    assert split_label_key_id("Event:2026-08-20T10:30:00") == ("Event", "2026-08-20T10:30:00")


@pytest.mark.parametrize("value", [":42", "Person:", "Person", ""])
def test_an_id_with_no_label_or_no_key_is_rejected(value):
    assert split_label_key_id(value) is None


# ---------------------------------------------------------------------------
# merge_graph_results
# ---------------------------------------------------------------------------

def graph(node_ids):
    return QueryData(
        type="GRAPH",
        data=GraphData(
            nodes=[Node(id=i, labels=["Person"], properties={}) for i in node_ids],
            relationships=[],
        ),
    )


def test_merging_a_fan_out_de_duplicates_by_id():
    merged = merge_graph_results([graph(["1", "2"]), graph(["2", "3"])])
    assert [node.id for node in merged.data.nodes] == ["1", "2", "3"]


def test_merging_skips_table_results():
    merged = merge_graph_results([QueryData(type="TABLE", data=[["a"], [1]]), graph(["1"])])
    assert len(merged.data.nodes) == 1


# ---------------------------------------------------------------------------
# GraphIntentMixin
# ---------------------------------------------------------------------------

class StubDriver(GraphIntentMixin):
    """A driver that records the statements the mixin builds."""

    def __init__(self, capabilities: GraphDbCapabilities, dialect=SPANNER_DIALECT):
        self.graph_capabilities = capabilities
        self.graph_dialect = dialect
        self.statements: list = []

    async def execute_query(self, query, parameters=None):
        self.statements.append(query)

        class _Response:
            data = graph([])

        return _Response()

    def graph_categories(self):
        return {"Person": {"keys": ["id"]}, "Company": {"keys": ["id"]}}

    def graph_key_types(self):
        return {"Person": "TEXT", "Company": "TEXT"}


def spanner_capabilities() -> GraphDbCapabilities:
    capabilities = GraphDbCapabilities(type="spanner")
    capabilities.expand.multiHop = True
    capabilities.expand.excludeRelationshipIds = True
    capabilities.pull.category = True
    capabilities.pull.relationship = True
    capabilities.pull.excludeLoaded = True
    capabilities.intents = ["expand", "pullCategory", "pullRelationship"]
    return capabilities


@pytest.mark.asyncio
async def test_expand_builds_and_runs_a_statement():
    driver = StubDriver(spanner_capabilities())
    await driver.expand(ExpandRequest(nodeIds=["101", "102"], direction="all", limit=25))
    assert len(driver.statements) == 1
    assert 'ELEMENT_ID(n) IN UNNEST (["101","102"])' in driver.statements[0]


@pytest.mark.asyncio
async def test_an_unsupported_direction_falls_back_to_a_supported_one():
    capabilities = spanner_capabilities()
    capabilities.expand.directions = ["from"]
    driver = StubDriver(capabilities)
    await driver.expand(ExpandRequest(nodeIds=["101"], direction="both", limit=10))
    assert "-[r]->(m)" in driver.statements[0]


@pytest.mark.asyncio
async def test_a_denied_capability_is_not_exercised():
    capabilities = spanner_capabilities()
    capabilities.expand.multiHop = False
    capabilities.expand.excludeRelationshipIds = False
    driver = StubDriver(capabilities)

    await driver.expand(
        ExpandRequest(nodeIds=["101"], direction="all", hops=3, excludeRelationshipIds=["901"], limit=10)
    )
    statement = driver.statements[0]
    assert "n1" not in statement, "multi-hop is off, so no intermediate variable"
    assert "901" not in statement, "edge-id exclusion is off, so no such predicate"


# ---------------------------------------------------------------------------
# Lazy schema load for key-predicate backends
# ---------------------------------------------------------------------------


class KeyPredicateDriver(GraphIntentSupport):
    """
    A backend with no identity function, built the way the API builds one: fresh
    per request, with nothing carried over from an earlier call.

    Subclasses ``GraphIntentSupport`` rather than the bare mixin so the remembering
    and the key-type lookup under test are the shipped ones, not the stub's.
    """

    graph_capabilities = None  # set per instance
    graph_dialect = ROCKETGRAPH_DIALECT

    def __init__(self, schema_response):
        self.graph_capabilities = key_predicate_capabilities()
        self.statements: list = []
        self.schema_calls = 0
        self._schema_response = schema_response
        self._graph_categories = {}

    async def execute_query(self, query, parameters=None):
        self.statements.append(query)

        class _Response:
            data = graph([])

        return _Response()

    async def get_graph_schema(self):
        self.schema_calls += 1
        return self._schema_response


def key_predicate_capabilities() -> GraphDbCapabilities:
    capabilities = GraphDbCapabilities(type="rocketgraph", queryLanguages=["cypher"])
    capabilities.identity.nodeId = "label-key"
    capabilities.expand.predicate = "primary-key"
    capabilities.expand.directions = ["from", "to"]
    capabilities.pull.category = True
    capabilities.pull.relationship = True
    capabilities.pull.excludeLoaded = True
    capabilities.intents = ["expand", "pullCategory", "pullRelationship"]
    return capabilities


def person_schema(success: bool = True) -> GraphSchemaResponse:
    return GraphSchemaResponse(
        success=success,
        data=GraphSchema(
            categories=[
                Category(name="Person", props=["id"], keys=["id"], keysTypes={"id": "INT64"})
            ]
        ),
    )


@pytest.mark.asyncio
async def test_a_key_predicate_backend_loads_its_schema_before_resolving_seeds():
    # Without this the driver knew no category keys, resolved zero seeds, and
    # answered every expansion with an empty graph instead of an error.
    driver = KeyPredicateDriver(person_schema())
    await driver.expand(ExpandRequest(nodeIds=["Person:1"], category="Person", limit=10))

    assert driver.schema_calls == 1
    assert driver.statements == [
        "MATCH (n:`Person`)-[r]->(m) WHERE n.`id` IN [1] RETURN n, r, m SKIP 0 LIMIT 10"
    ]


@pytest.mark.asyncio
async def test_the_schema_is_loaded_once_per_driver_not_once_per_intent():
    driver = KeyPredicateDriver(person_schema())
    await driver.expand(ExpandRequest(nodeIds=["Person:1"], category="Person", limit=10))
    await driver.pull_category(PullCategoryRequest(category="Person", limit=10))
    assert driver.schema_calls == 1


@pytest.mark.asyncio
async def test_a_backend_that_cannot_answer_its_schema_still_returns_rather_than_raises():
    driver = KeyPredicateDriver(GraphSchemaResponse(success=False, error="unreachable"))
    result = await driver.expand(ExpandRequest(nodeIds=["Person:1"], category="Person", limit=10))
    assert result.data.nodes == [] and driver.statements == []


@pytest.mark.asyncio
async def test_an_identity_backend_is_not_made_to_pay_for_a_schema_it_does_not_need():
    driver = StubDriver(spanner_capabilities())
    driver.get_graph_schema = _unexpected_schema_call
    await driver.expand(ExpandRequest(nodeIds=["101"], limit=10))
    assert len(driver.statements) == 1


async def _unexpected_schema_call():
    raise AssertionError("an internal-id backend must not load the graph schema to expand")


@pytest.mark.asyncio
async def test_pull_returns_an_empty_graph_when_the_capability_is_off():
    capabilities = spanner_capabilities()
    capabilities.pull.category = False
    driver = StubDriver(capabilities)

    from kineviz_database_proxy.contract.intents import PullCategoryRequest

    result = await driver.pull_category(PullCategoryRequest(category="Person", limit=10))
    assert result.type == "GRAPH"
    assert driver.statements == []


# ---------------------------------------------------------------------------
# Private Network Access
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("origin", ["https://graphxr.example.com", "http://localhost:3000"])
def test_the_private_network_preflight_is_answered(origin):
    from kineviz_database_proxy.main import app

    client = TestClient(app)
    response = client.options(
        "/api/spanner/Demo/query",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-API-Key, X-Admin-Token, Content-Type",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    # Chrome requires a successful response as well as the private-network header.
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert {"x-api-key", "x-admin-token", "content-type"} <= {
        value.strip().lower()
        for value in response.headers["Access-Control-Allow-Headers"].split(",")
    }
    assert response.headers.get("Access-Control-Allow-Private-Network") == "true"
    assert response.headers.get("Access-Control-Max-Age")


def test_the_header_is_not_claimed_when_the_browser_did_not_ask():
    from kineviz_database_proxy.main import app

    client = TestClient(app)
    response = client.options(
        "/api/spanner/Demo/query",
        headers={"Origin": "https://graphxr.example.com", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "https://graphxr.example.com"
    assert "Access-Control-Allow-Private-Network" not in response.headers


@pytest.mark.parametrize("origin", ["https://graphxr.example.com", "http://localhost:3000"])
@pytest.mark.parametrize("cookies", [{}, {"session": "test-session"}])
def test_cross_origin_responses_allow_credentials(origin, cookies):
    from kineviz_database_proxy.main import app

    client = TestClient(app, cookies=cookies)
    response = client.get("/health", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert "origin" in {
        value.strip().lower() for value in response.headers["Vary"].split(",")
    }
