# RocketGraph Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add RocketGraph (XGT-based) graph database support to graphxr-database-proxy, implementing Info, Query, and GraphSchema APIs end-to-end (backend driver + frontend UI).

**Architecture:** A new `RocketGraphDriver` subclassing `BaseDatabaseDriver` is registered in `DriverFactory` under `DatabaseType.ROCKETGRAPH`. It communicates with the RocketGraph REST API over HTTP using `httpx`, supports both Standalone (JWT) and Plugin (Bearer Token / MC session) deployment modes, and reuses the existing `OAuthConfig.token` storage for JWT persistence. A frontend option in `ProjectForm` lets users create RocketGraph projects.

**Tech Stack:** Python 3.9+, FastAPI, Pydantic v2, httpx (new dep), pytest (existing dev dep), React/TypeScript, Ant Design.

**Reference docs:**
- Design spec: `docs/superpowers/specs/2026-05-21-rocketgraph-support-design.md`
- API contract: `doc/API_Reference.md`
- RocketGraph API: `doc/dbs/RocketGraph-REST-API-User-Guide.docx`

---

## File Map

**New files:**
- `src/kineviz_database_proxy/drivers/rocketgraph.py` — Driver implementation (AuthClient, QueryParser, SchemaMapper, RocketGraphDriver)
- `tests/__init__.py` — Empty package marker (tests dir doesn't exist yet)
- `tests/test_rocketgraph_query_parser.py` — Unit tests for QueryParser
- `tests/test_rocketgraph_schema_mapper.py` — Unit tests for SchemaMapper
- `tests/test_rocketgraph_auth_client.py` — Unit tests for AuthClient

**Modified files:**
- `src/kineviz_database_proxy/models/project.py` — Add `ROCKETGRAPH`, `BEARER_TOKEN` enum values; add `use_tls`/`deployment_mode`/`api_base_path` fields
- `src/kineviz_database_proxy/drivers/factory.py` — Register `RocketGraphDriver`
- `requirements.txt` — Add `httpx>=0.25.0`
- `pyproject.toml` — Add `httpx>=0.25.0` to dependencies
- `frontend/src/types/project.ts` — Add `'rocketgraph'`, `'bearer_token'`, new config fields
- `frontend/src/components/ProjectForm.tsx` — Add RocketGraph option + config card

---

## Task 1: Backend Model Extensions

**Files:**
- Modify: `src/kineviz_database_proxy/models/project.py`

- [ ] **Step 1: Add ROCKETGRAPH to DatabaseType enum**

Edit `src/kineviz_database_proxy/models/project.py` lines 14-23:

```python
class DatabaseType(str, Enum):
    """
    Supported database types

    Values match the API endpoint paths (e.g., /api/spanner/{project_id})
    """
    SPANNER = "spanner"
    ROCKETGRAPH = "rocketgraph"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MONGODB = "mongodb"
```

- [ ] **Step 2: Add BEARER_TOKEN to AuthType enum**

Edit `src/kineviz_database_proxy/models/project.py` lines 26-38:

```python
class AuthType(str, Enum):
    """
    Authentication types supported by the proxy

    - OAUTH2: OAuth 2.0 token-based authentication
    - SERVICE_ACCOUNT: Google Cloud service account JSON key
    - USERNAME_PASSWORD: Traditional username/password authentication
    - GOOGLE_ADC: Google Application Default Credentials
    - BEARER_TOKEN: Pre-acquired static bearer token (e.g., MC session)
    """
    OAUTH2 = "oauth2"
    SERVICE_ACCOUNT = "service_account"
    USERNAME_PASSWORD = "username_password"
    GOOGLE_ADC = "google_ADC"
    BEARER_TOKEN = "bearer_token"
```

- [ ] **Step 3: Add RocketGraph fields to DatabaseConfig**

Edit `src/kineviz_database_proxy/models/project.py`. In the `DatabaseConfig` class (around line 69-89), add fields before `options: Dict[str, Any] = ...`:

```python
    # RocketGraph specific
    use_tls: bool = False
    deployment_mode: Optional[str] = None  # "standalone" | "plugin"
    api_base_path: Optional[str] = None    # Custom API base path override
```

- [ ] **Step 4: Verify model loads correctly**

Run:
```bash
cd e:/projects/graphxr-database-proxy && python -c "from src.kineviz_database_proxy.models.project import DatabaseType, AuthType, DatabaseConfig; print(DatabaseType.ROCKETGRAPH, AuthType.BEARER_TOKEN); print(DatabaseConfig(type='rocketgraph', use_tls=True, deployment_mode='standalone'))"
```

Expected output:
```
DatabaseType.ROCKETGRAPH AuthType.BEARER_TOKEN
type=<DatabaseType.ROCKETGRAPH: 'rocketgraph'> ... use_tls=True deployment_mode='standalone' ...
```

- [ ] **Step 5: Commit**

```bash
git add src/kineviz_database_proxy/models/project.py
git commit -m "feat(rocketgraph): extend models with ROCKETGRAPH type and config fields

- Add DatabaseType.ROCKETGRAPH
- Add AuthType.BEARER_TOKEN
- Add DatabaseConfig: use_tls, deployment_mode, api_base_path"
```

---

## Task 2: Add httpx Dependency

**Files:**
- Modify: `requirements.txt`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add httpx to requirements.txt**

Add at end of `requirements.txt`:
```
httpx>=0.25.0
```

- [ ] **Step 2: Add httpx to pyproject.toml**

In `pyproject.toml`, locate the `dependencies = [...]` list under `[project]` and add `"httpx>=0.25.0"` as a new entry.

- [ ] **Step 3: Install httpx**

```bash
cd e:/projects/graphxr-database-proxy && uv pip install "httpx>=0.25.0"
```

Expected: httpx installed successfully.

- [ ] **Step 4: Verify import works**

```bash
python -c "import httpx; print(httpx.__version__)"
```

Expected: prints httpx version (>= 0.25.0).

- [ ] **Step 5: Commit**

```bash
git add requirements.txt pyproject.toml
git commit -m "feat(rocketgraph): add httpx dependency for HTTP client"
```

---

## Task 3: Test Infrastructure Setup

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create tests directory marker**

Create `tests/__init__.py` (empty file):

```python
```

- [ ] **Step 2: Create conftest.py for pytest config**

Create `tests/conftest.py`:

```python
"""Pytest configuration for graphxr-database-proxy tests."""
import sys
from pathlib import Path

# Add src to path so tests can import the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
```

- [ ] **Step 3: Verify pytest discovers tests**

```bash
cd e:/projects/graphxr-database-proxy && python -m pytest tests/ --collect-only 2>&1 | head -10
```

Expected: "no tests ran" or "collected 0 items" — no errors about import.

- [ ] **Step 4: Commit**

```bash
git add tests/__init__.py tests/conftest.py
git commit -m "test: add pytest infrastructure (conftest, tests/ package)"
```

---

## Task 4: SchemaMapper Component (TDD)

**Files:**
- Test: `tests/test_rocketgraph_schema_mapper.py`
- Create: `src/kineviz_database_proxy/drivers/rocketgraph.py` (partial — SchemaMapper only)

- [ ] **Step 1: Write failing tests for SchemaMapper**

Create `tests/test_rocketgraph_schema_mapper.py`:

```python
"""Tests for RocketGraph SchemaMapper."""
import pytest
from kineviz_database_proxy.drivers.rocketgraph import SchemaMapper
from kineviz_database_proxy.models.project import GraphSchema, Category, Relationship


def test_maps_simple_node_type():
    rg_schema = {
        "graph_name": "social",
        "graph_schema": {
            "node_types": [
                {
                    "type": "Person",
                    "key": "id",
                    "properties": [
                        {"name": "id", "type": "TEXT"},
                        {"name": "name", "type": "TEXT"},
                        {"name": "age", "type": "INT"},
                    ],
                }
            ],
            "edge_types": [],
        },
    }

    result = SchemaMapper.map(rg_schema)

    assert isinstance(result, GraphSchema)
    assert len(result.categories) == 1
    cat = result.categories[0]
    assert cat.name == "Person"
    assert cat.keys == ["id"]
    assert set(cat.props) == {"id", "name", "age"}
    assert cat.keysTypes == {"id": "TEXT"}
    assert cat.propsTypes == {"id": "TEXT", "name": "TEXT", "age": "INT"}


def test_maps_edge_type_with_source_target():
    rg_schema = {
        "graph_schema": {
            "node_types": [],
            "edge_types": [
                {
                    "type": "KNOWS",
                    "source": "Person",
                    "target": "Person",
                    "source_key": "id",
                    "target_key": "id",
                    "properties": [{"name": "since", "type": "DATE"}],
                }
            ],
        }
    }

    result = SchemaMapper.map(rg_schema)

    assert len(result.relationships) == 1
    rel = result.relationships[0]
    assert rel.name == "KNOWS"
    assert rel.startCategory == "Person"
    assert rel.endCategory == "Person"
    assert rel.props == ["since"]
    assert rel.propsTypes == {"since": "DATE"}
    assert "id" in rel.keys


def test_maps_edge_with_distinct_source_target_keys():
    rg_schema = {
        "graph_schema": {
            "node_types": [],
            "edge_types": [
                {
                    "type": "OWNS",
                    "source": "Person",
                    "target": "Car",
                    "source_key": "person_id",
                    "target_key": "vin",
                    "properties": [],
                }
            ],
        }
    }

    result = SchemaMapper.map(rg_schema)

    rel = result.relationships[0]
    assert set(rel.keys) == {"person_id", "vin"}


def test_handles_missing_properties_field():
    rg_schema = {
        "graph_schema": {
            "node_types": [{"type": "Foo", "key": "id"}],
            "edge_types": [],
        }
    }

    result = SchemaMapper.map(rg_schema)

    cat = result.categories[0]
    assert cat.name == "Foo"
    assert cat.props == []
    assert cat.propsTypes == {}


def test_handles_empty_schema():
    rg_schema = {"graph_schema": {"node_types": [], "edge_types": []}}
    result = SchemaMapper.map(rg_schema)
    assert result.categories == []
    assert result.relationships == []


def test_handles_top_level_node_types_format():
    """Some responses may put node_types/edge_types at top level."""
    rg_schema = {
        "node_types": [{"type": "X", "key": "id", "properties": [{"name": "id", "type": "TEXT"}]}],
        "edge_types": [],
    }
    result = SchemaMapper.map(rg_schema)
    assert len(result.categories) == 1
    assert result.categories[0].name == "X"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd e:/projects/graphxr-database-proxy && python -m pytest tests/test_rocketgraph_schema_mapper.py -v
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError: cannot import name 'SchemaMapper'`.

- [ ] **Step 3: Create rocketgraph.py with SchemaMapper**

Create `src/kineviz_database_proxy/drivers/rocketgraph.py`:

```python
# -*- coding: utf-8 -*-
"""
RocketGraph driver — connects to RocketGraph REST API (XGT-based graph DB).

Supports Standalone (JWT) and Plugin (Bearer Token) deployment modes.
Implements Info, Query, GraphSchema endpoints per doc/API_Reference.md.
"""

from typing import Any, Dict, List, Optional

from ..models.project import (
    Category,
    GraphSchema,
    Relationship,
)


class SchemaMapper:
    """Maps RocketGraph schema response to project's GraphSchema format."""

    @staticmethod
    def map(rg_response: Dict[str, Any]) -> GraphSchema:
        """Convert a RocketGraph schema response into a GraphSchema.

        Accepts either {"graph_schema": {"node_types": [...], "edge_types": [...]}}
        or the inner form with node_types/edge_types at top level.
        """
        schema_root = rg_response.get("graph_schema") or rg_response
        node_types = schema_root.get("node_types") or []
        edge_types = schema_root.get("edge_types") or []

        categories = [SchemaMapper._map_node(nt) for nt in node_types]
        relationships = [SchemaMapper._map_edge(et) for et in edge_types]

        return GraphSchema(categories=categories, relationships=relationships)

    @staticmethod
    def _map_node(node_type: Dict[str, Any]) -> Category:
        name = node_type.get("type", "")
        key = node_type.get("key")
        properties = node_type.get("properties") or []

        props_types: Dict[str, str] = {p["name"]: p.get("type", "TEXT") for p in properties}
        props = list(props_types.keys())

        keys = [key] if key else []
        keys_types: Dict[str, str] = {k: props_types.get(k, "TEXT") for k in keys}

        return Category(
            name=name,
            props=props,
            keys=keys,
            keysTypes=keys_types,
            propsTypes=props_types,
        )

    @staticmethod
    def _map_edge(edge_type: Dict[str, Any]) -> Relationship:
        name = edge_type.get("type", "")
        source = edge_type.get("source", "")
        target = edge_type.get("target", "")
        source_key = edge_type.get("source_key")
        target_key = edge_type.get("target_key")
        properties = edge_type.get("properties") or []

        props_types: Dict[str, str] = {p["name"]: p.get("type", "TEXT") for p in properties}
        props = list(props_types.keys())

        # Deduplicate keys while preserving order
        keys_list: List[str] = []
        for k in (source_key, target_key):
            if k and k not in keys_list:
                keys_list.append(k)
        keys_types: Dict[str, str] = {k: props_types.get(k, "TEXT") for k in keys_list}

        return Relationship(
            name=name,
            props=props,
            keys=keys_list,
            keysTypes=keys_types,
            propsTypes=props_types,
            startCategory=source,
            endCategory=target,
        )
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_rocketgraph_schema_mapper.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kineviz_database_proxy/drivers/rocketgraph.py tests/test_rocketgraph_schema_mapper.py
git commit -m "feat(rocketgraph): implement SchemaMapper with unit tests"
```

---

## Task 5: QueryParser Component (TDD)

**Files:**
- Test: `tests/test_rocketgraph_query_parser.py`
- Modify: `src/kineviz_database_proxy/drivers/rocketgraph.py` (add QueryParser)

- [ ] **Step 1: Write failing tests for QueryParser**

Create `tests/test_rocketgraph_query_parser.py`:

```python
"""Tests for RocketGraph QueryParser (TABLE/GRAPH detection)."""
import pytest
from kineviz_database_proxy.drivers.rocketgraph import QueryParser
from kineviz_database_proxy.models.project import QueryData, GraphData, Node, RelationshipData


def test_all_scalars_returns_table():
    response = {
        "columns": ["p.name", "p.age"],
        "data": [["Alice", 30], ["Bob", 25]],
    }
    result = QueryParser.parse(response)

    assert isinstance(result, QueryData)
    assert result.type == "TABLE"
    assert result.data == [
        {"p.name": "Alice", "p.age": 30},
        {"p.name": "Bob", "p.age": 25},
    ]


def test_empty_data_returns_table():
    response = {"columns": ["x"], "data": []}
    result = QueryParser.parse(response)
    assert result.type == "TABLE"
    assert result.data == []


def test_node_object_returns_graph():
    response = {
        "columns": ["p"],
        "data": [[
            {
                "id": "1",
                "labels": ["Person"],
                "properties": {"name": "Alice", "age": 30},
            }
        ]],
    }
    result = QueryParser.parse(response)

    assert result.type == "GRAPH"
    assert isinstance(result.data, GraphData)
    assert len(result.data.nodes) == 1
    node = result.data.nodes[0]
    assert node.id == "1"
    assert node.labels == ["Person"]
    assert node.properties == {"name": "Alice", "age": 30}


def test_edge_object_returns_graph():
    response = {
        "columns": ["r"],
        "data": [[
            {
                "id": "r1",
                "type": "KNOWS",
                "source": "1",
                "target": "2",
                "properties": {"since": "2020-01-01"},
            }
        ]],
    }
    result = QueryParser.parse(response)

    assert result.type == "GRAPH"
    assert len(result.data.relationships) == 1
    edge = result.data.relationships[0]
    assert edge.id == "r1"
    assert edge.type == "KNOWS"
    assert edge.startNodeId == "1"
    assert edge.endNodeId == "2"
    assert edge.properties == {"since": "2020-01-01"}


def test_mixed_nodes_edges_returns_graph():
    response = {
        "columns": ["n", "r", "m"],
        "data": [[
            {"id": "1", "labels": ["Person"], "properties": {"name": "Alice"}},
            {"id": "r1", "type": "KNOWS", "source": "1", "target": "2", "properties": {}},
            {"id": "2", "labels": ["Person"], "properties": {"name": "Bob"}},
        ]],
    }
    result = QueryParser.parse(response)

    assert result.type == "GRAPH"
    assert len(result.data.nodes) == 2
    assert len(result.data.relationships) == 1


def test_dedupes_nodes_by_id():
    response = {
        "columns": ["n", "m"],
        "data": [
            [
                {"id": "1", "labels": ["Person"], "properties": {"name": "Alice"}},
                {"id": "2", "labels": ["Person"], "properties": {"name": "Bob"}},
            ],
            [
                {"id": "1", "labels": ["Person"], "properties": {"name": "Alice"}},
                {"id": "3", "labels": ["Person"], "properties": {"name": "Carol"}},
            ],
        ],
    }
    result = QueryParser.parse(response)

    assert result.type == "GRAPH"
    assert len(result.data.nodes) == 3
    ids = sorted([n.id for n in result.data.nodes])
    assert ids == ["1", "2", "3"]


def test_alternative_field_names_identifier_label():
    """Support alternate naming: 'identifier' instead of 'id', 'label' instead of 'labels'."""
    response = {
        "columns": ["p"],
        "data": [[
            {
                "identifier": "n1",
                "label": "Person",
                "properties": {"name": "Alice"},
            }
        ]],
    }
    result = QueryParser.parse(response)

    assert result.type == "GRAPH"
    assert len(result.data.nodes) == 1
    assert result.data.nodes[0].id == "n1"
    assert result.data.nodes[0].labels == ["Person"]


def test_alternative_edge_field_names():
    """Support alternate edge naming: source_node_identifier / destination_node_identifier."""
    response = {
        "columns": ["r"],
        "data": [[
            {
                "identifier": "e1",
                "label": "KNOWS",
                "source_node_identifier": "1",
                "destination_node_identifier": "2",
                "properties": {},
            }
        ]],
    }
    result = QueryParser.parse(response)

    assert result.type == "GRAPH"
    assert len(result.data.relationships) == 1
    edge = result.data.relationships[0]
    assert edge.id == "e1"
    assert edge.type == "KNOWS"
    assert edge.startNodeId == "1"
    assert edge.endNodeId == "2"


def test_scalars_ignored_in_graph_mode():
    """When at least one graph object present, scalars in other columns are ignored."""
    response = {
        "columns": ["n", "count"],
        "data": [[
            {"id": "1", "labels": ["Person"], "properties": {"name": "Alice"}},
            42,
        ]],
    }
    result = QueryParser.parse(response)

    assert result.type == "GRAPH"
    assert len(result.data.nodes) == 1


def test_none_data_returns_empty_table():
    response = {"columns": [], "data": None}
    result = QueryParser.parse(response)
    assert result.type == "TABLE"
    assert result.data == []
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_rocketgraph_query_parser.py -v
```

Expected: FAIL with `ImportError: cannot import name 'QueryParser'`.

- [ ] **Step 3: Add QueryParser to rocketgraph.py**

Append to `src/kineviz_database_proxy/drivers/rocketgraph.py` (after SchemaMapper class):

```python
from ..models.project import (
    GraphData,
    Node,
    QueryData,
    RelationshipData,
)


class QueryParser:
    """Parses RocketGraph query response, detecting TABLE vs GRAPH format."""

    NODE_ID_KEYS = ("id", "identifier", "_id")
    EDGE_ID_KEYS = ("id", "identifier", "_id")
    NODE_LABEL_KEYS = ("labels", "label")
    EDGE_TYPE_KEYS = ("type", "label")
    EDGE_SOURCE_KEYS = ("source", "source_node_identifier", "startNodeId")
    EDGE_TARGET_KEYS = ("target", "destination_node_identifier", "endNodeId")

    @classmethod
    def parse(cls, response: Dict[str, Any]) -> QueryData:
        columns: List[str] = response.get("columns") or []
        data: List[List[Any]] = response.get("data") or []

        # Detect whether any cell contains a graph object
        contains_graph = any(
            cls._is_graph_object(cell) for row in data for cell in row
        )

        if not contains_graph:
            return cls._to_table(columns, data)

        return cls._to_graph(data)

    @classmethod
    def _is_graph_object(cls, value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        return cls._is_edge(value) or cls._is_node(value)

    @classmethod
    def _is_node(cls, value: Dict[str, Any]) -> bool:
        has_id = any(k in value for k in cls.NODE_ID_KEYS)
        has_label = any(k in value for k in cls.NODE_LABEL_KEYS)
        has_properties = "properties" in value
        # Must look like node but NOT edge
        return has_id and has_label and has_properties and not cls._is_edge(value)

    @classmethod
    def _is_edge(cls, value: Dict[str, Any]) -> bool:
        has_id = any(k in value for k in cls.EDGE_ID_KEYS)
        has_source = any(k in value for k in cls.EDGE_SOURCE_KEYS)
        has_target = any(k in value for k in cls.EDGE_TARGET_KEYS)
        return has_id and has_source and has_target

    @classmethod
    def _to_table(cls, columns: List[str], data: List[List[Any]]) -> QueryData:
        rows = [
            {col: row[i] if i < len(row) else None for i, col in enumerate(columns)}
            for row in data
        ]
        return QueryData(type="TABLE", data=rows)

    @classmethod
    def _to_graph(cls, data: List[List[Any]]) -> QueryData:
        nodes: Dict[str, Node] = {}
        edges: Dict[str, RelationshipData] = {}

        for row in data:
            for cell in row:
                if not isinstance(cell, dict):
                    continue
                if cls._is_edge(cell):
                    edge = cls._extract_edge(cell)
                    if edge.id not in edges:
                        edges[edge.id] = edge
                elif cls._is_node(cell):
                    node = cls._extract_node(cell)
                    if node.id not in nodes:
                        nodes[node.id] = node

        return QueryData(
            type="GRAPH",
            data=GraphData(nodes=list(nodes.values()), relationships=list(edges.values())),
        )

    @classmethod
    def _extract_node(cls, value: Dict[str, Any]) -> Node:
        node_id = cls._first_present(value, cls.NODE_ID_KEYS) or ""
        labels_raw = cls._first_present(value, cls.NODE_LABEL_KEYS)
        if isinstance(labels_raw, list):
            labels = [str(l) for l in labels_raw]
        elif labels_raw:
            labels = [str(labels_raw)]
        else:
            labels = []
        properties = value.get("properties") or {}
        return Node(id=str(node_id), labels=labels, properties=properties)

    @classmethod
    def _extract_edge(cls, value: Dict[str, Any]) -> RelationshipData:
        edge_id = cls._first_present(value, cls.EDGE_ID_KEYS) or ""
        edge_type_raw = cls._first_present(value, cls.EDGE_TYPE_KEYS)
        if isinstance(edge_type_raw, list) and edge_type_raw:
            edge_type = str(edge_type_raw[-1])
        elif edge_type_raw:
            edge_type = str(edge_type_raw)
        else:
            edge_type = ""
        start_id = cls._first_present(value, cls.EDGE_SOURCE_KEYS) or ""
        end_id = cls._first_present(value, cls.EDGE_TARGET_KEYS) or ""
        properties = value.get("properties") or {}
        return RelationshipData(
            id=str(edge_id),
            type=edge_type,
            startNodeId=str(start_id),
            endNodeId=str(end_id),
            properties=properties,
        )

    @staticmethod
    def _first_present(value: Dict[str, Any], keys: tuple) -> Any:
        for k in keys:
            if k in value:
                return value[k]
        return None
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_rocketgraph_query_parser.py -v
```

Expected: All 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kineviz_database_proxy/drivers/rocketgraph.py tests/test_rocketgraph_query_parser.py
git commit -m "feat(rocketgraph): implement QueryParser with TABLE/GRAPH detection"
```

---

## Task 6: AuthClient Component (TDD with mocked httpx)

**Files:**
- Test: `tests/test_rocketgraph_auth_client.py`
- Modify: `src/kineviz_database_proxy/drivers/rocketgraph.py`

- [ ] **Step 1: Write failing tests for AuthClient**

Create `tests/test_rocketgraph_auth_client.py`:

```python
"""Tests for RocketGraph AuthClient (login, token caching, refresh)."""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from kineviz_database_proxy.drivers.rocketgraph import AuthClient
from kineviz_database_proxy.models.project import (
    AuthType,
    DatabaseConfig,
    DatabaseType,
    OAuthConfig,
    Project,
)


def _make_project(auth_type=AuthType.USERNAME_PASSWORD, token=None, last_refreshed=None, expires_in=None):
    return Project(
        id="p-1",
        name="test-project",
        database_type=DatabaseType.ROCKETGRAPH,
        database_config=DatabaseConfig(
            type=DatabaseType.ROCKETGRAPH,
            host="example.com",
            port=4368,
            graph_name="social",
            auth_type=auth_type,
            username="alice",
            password="secret",
            oauth_config=OAuthConfig(
                token=token,
                last_refreshed=last_refreshed,
                expires_in=expires_in,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_bearer_token_returns_static_token():
    project = _make_project(auth_type=AuthType.BEARER_TOKEN, token="static-token-xyz")
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    token = await client.get_token()

    assert token == "static-token-xyz"


@pytest.mark.asyncio
async def test_bearer_token_does_not_call_login():
    project = _make_project(auth_type=AuthType.BEARER_TOKEN, token="static-token")
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    with patch("httpx.AsyncClient.post") as mock_post:
        await client.get_token()
        mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_username_password_login_when_no_token():
    project = _make_project(auth_type=AuthType.USERNAME_PASSWORD, token=None)
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "new-jwt-token",
        "expires_in": 3600,
    }
    mock_response.raise_for_status = MagicMock()

    mock_project_service = MagicMock()
    mock_project_service.update_project_token = AsyncMock()

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        with patch("kineviz_database_proxy.drivers.rocketgraph.ProjectService", return_value=mock_project_service):
            token = await client.get_token()

    assert token == "new-jwt-token"
    mock_project_service.update_project_token.assert_called_once()
    call_kwargs = mock_project_service.update_project_token.call_args.kwargs
    assert call_kwargs["project_id"] == "p-1"
    assert call_kwargs["token"] == "new-jwt-token"
    assert call_kwargs["expires_in"] == 3600


@pytest.mark.asyncio
async def test_cached_token_used_when_not_expired():
    """If token was refreshed recently and is not near expiry, reuse it without login."""
    now = time.time()
    project = _make_project(
        auth_type=AuthType.USERNAME_PASSWORD,
        token="cached-token",
        last_refreshed=now - 60,  # 1 minute ago
        expires_in=3600,           # 1 hour validity
    )
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    with patch("httpx.AsyncClient.post") as mock_post:
        token = await client.get_token()
        mock_post.assert_not_called()

    assert token == "cached-token"


@pytest.mark.asyncio
async def test_expired_token_triggers_relogin():
    """Token near expiry (within 5 min buffer) triggers re-login."""
    now = time.time()
    project = _make_project(
        auth_type=AuthType.USERNAME_PASSWORD,
        token="old-token",
        last_refreshed=now - 3500,  # 58 min ago
        expires_in=3600,             # expires in 100 sec → within buffer
    )
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "fresh-token", "expires_in": 3600}
    mock_response.raise_for_status = MagicMock()

    mock_project_service = MagicMock()
    mock_project_service.update_project_token = AsyncMock()

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        with patch("kineviz_database_proxy.drivers.rocketgraph.ProjectService", return_value=mock_project_service):
            token = await client.get_token()

    assert token == "fresh-token"


@pytest.mark.asyncio
async def test_username_password_calls_correct_endpoint():
    """Verify login posts to /auth/xgt/basic with username/password JSON body."""
    project = _make_project(auth_type=AuthType.USERNAME_PASSWORD, token=None)
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "tok", "expires_in": 3600}
    mock_response.raise_for_status = MagicMock()
    mock_post = AsyncMock(return_value=mock_response)

    mock_project_service = MagicMock()
    mock_project_service.update_project_token = AsyncMock()

    with patch("httpx.AsyncClient.post", new=mock_post):
        with patch("kineviz_database_proxy.drivers.rocketgraph.ProjectService", return_value=mock_project_service):
            await client.get_token()

    # First positional arg is URL
    assert mock_post.call_args.args[0].endswith("/auth/xgt/basic")
    # JSON body has correct credentials
    assert mock_post.call_args.kwargs["json"] == {"username": "alice", "password": "secret"}


@pytest.mark.asyncio
async def test_invalidate_forces_refresh():
    """After invalidate(), next get_token re-logs in even if cached token would be valid."""
    now = time.time()
    project = _make_project(
        auth_type=AuthType.USERNAME_PASSWORD,
        token="cached",
        last_refreshed=now - 60,
        expires_in=3600,
    )
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "after-invalidate", "expires_in": 3600}
    mock_response.raise_for_status = MagicMock()

    mock_project_service = MagicMock()
    mock_project_service.update_project_token = AsyncMock()

    client.invalidate()

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        with patch("kineviz_database_proxy.drivers.rocketgraph.ProjectService", return_value=mock_project_service):
            token = await client.get_token()

    assert token == "after-invalidate"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_rocketgraph_auth_client.py -v
```

Expected: FAIL with `ImportError: cannot import name 'AuthClient'`.

- [ ] **Step 3: Add AuthClient to rocketgraph.py**

Append to `src/kineviz_database_proxy/drivers/rocketgraph.py`:

```python
import time
import httpx

from ..models.project import AuthType, Project
from ..services.project_service import ProjectService


class AuthClient:
    """Manages authentication tokens for RocketGraph requests.

    - USERNAME_PASSWORD: logs in via /auth/xgt/basic, caches JWT in oauth_config.
    - BEARER_TOKEN: uses static token from oauth_config.token, no refresh.
    """

    TOKEN_REFRESH_BUFFER_SECONDS = 300  # Refresh if expiring within 5 min

    def __init__(self, project: Project, base_url: str):
        self.project = project
        self.config = project.database_config
        self.base_url = base_url.rstrip("/")
        self._force_refresh = False

    def invalidate(self) -> None:
        """Force the next get_token() call to re-login."""
        self._force_refresh = True

    async def get_token(self) -> str:
        """Return a valid bearer token, refreshing if necessary."""
        if self.config.auth_type == AuthType.BEARER_TOKEN:
            token = self.config.oauth_config.token if self.config.oauth_config else None
            if not token:
                raise ValueError("BEARER_TOKEN auth type requires oauth_config.token to be set")
            return token

        if self.config.auth_type == AuthType.USERNAME_PASSWORD:
            if not self._force_refresh and self._has_valid_cached_token():
                return self.config.oauth_config.token
            return await self._login()

        raise ValueError(
            f"Unsupported auth type for RocketGraph: {self.config.auth_type}"
        )

    def _has_valid_cached_token(self) -> bool:
        oauth = self.config.oauth_config
        if not oauth or not oauth.token:
            return False
        if oauth.last_refreshed is None or oauth.expires_in is None:
            return False
        elapsed = time.time() - oauth.last_refreshed
        return elapsed < (oauth.expires_in - self.TOKEN_REFRESH_BUFFER_SECONDS)

    async def _login(self) -> str:
        if not self.config.username or not self.config.password:
            raise ValueError("USERNAME_PASSWORD auth requires both username and password")

        url = f"{self.base_url}/auth/xgt/basic"
        body = {"username": self.config.username, "password": self.config.password}

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            data = response.json()

        token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        now = time.time()

        # Persist token + timestamp
        project_service = ProjectService()
        await project_service.update_project_token(
            project_id=self.project.id,
            token=token,
            last_refreshed=now,
            expires_in=expires_in,
        )

        # Update in-memory config so subsequent calls in this driver use the new token
        if self.config.oauth_config is None:
            from ..models.project import OAuthConfig
            self.config.oauth_config = OAuthConfig()
        self.config.oauth_config.token = token
        self.config.oauth_config.last_refreshed = now
        self.config.oauth_config.expires_in = expires_in

        self._force_refresh = False
        return token
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_rocketgraph_auth_client.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kineviz_database_proxy/drivers/rocketgraph.py tests/test_rocketgraph_auth_client.py
git commit -m "feat(rocketgraph): implement AuthClient with token caching and refresh"
```

---

## Task 7: RocketGraphDriver Main Class

**Files:**
- Modify: `src/kineviz_database_proxy/drivers/rocketgraph.py`

- [ ] **Step 1: Add RocketGraphDriver class to rocketgraph.py**

Append to `src/kineviz_database_proxy/drivers/rocketgraph.py`:

```python
from .base import BaseDatabaseDriver
from ..models.project import (
    GraphSchemaResponse,
    QueryResponse,
    SampleDataResponse,
    SchemaResponse,
)


class RocketGraphDriver(BaseDatabaseDriver):
    """Driver for RocketGraph (XGT-based) graph database via REST API."""

    DEFAULT_STANDALONE_PATH = "/api/v1"
    DEFAULT_PLUGIN_PATH = "/api/xgt/v1"

    def __init__(self, project: Project):
        super().__init__(project)
        self._http: Optional[httpx.AsyncClient] = None
        self._base_url = self._build_base_url()
        self._auth = AuthClient(project, self._base_url)

    def _build_base_url(self) -> str:
        cfg = self.config
        scheme = "https" if cfg.use_tls else "http"
        host = cfg.host or "localhost"
        port = cfg.port or (4368 if cfg.deployment_mode != "plugin" else 8080)
        if cfg.api_base_path:
            base_path = cfg.api_base_path
        elif cfg.deployment_mode == "plugin":
            base_path = self.DEFAULT_PLUGIN_PATH
        else:
            base_path = self.DEFAULT_STANDALONE_PATH
        return f"{scheme}://{host}:{port}{base_path.rstrip('/')}"

    async def connect(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=60.0)
        # Trigger token acquisition so failures surface here
        await self._auth.get_token()

    async def disconnect(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def test_connection(self) -> bool:
        try:
            if self._http is None:
                await self.connect()
            token = await self._auth.get_token()
            response = await self._http.get(
                f"{self._base_url}/version",
                headers={"Authorization": f"Bearer {token}"},
            )
            return response.status_code == 200
        except Exception as exc:
            print(f"[RocketGraph] test_connection failed: {exc}")
            return False

    async def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> QueryResponse:
        start = time.time()
        try:
            if self._http is None:
                await self.connect()
            if not self.config.graph_name:
                raise ValueError("graph_name is required for RocketGraph queries")

            url = f"{self._base_url}/graphs/{self.config.graph_name}/query"
            body: Dict[str, Any] = {
                "query": query,
                "language": "cypher",
                "parameters": parameters or {},
            }

            response = await self._post_with_retry(url, body)
            response.raise_for_status()
            payload = response.json()

            data = QueryParser.parse(payload)
            return QueryResponse(
                success=True,
                data=data,
                execution_time=time.time() - start,
            )
        except httpx.HTTPStatusError as http_err:
            return QueryResponse(
                success=False,
                error=self._format_http_error(http_err),
                execution_time=time.time() - start,
            )
        except Exception as exc:
            return QueryResponse(
                success=False,
                error=str(exc),
                execution_time=time.time() - start,
            )

    async def get_graph_schema(self) -> GraphSchemaResponse:
        try:
            if self._http is None:
                await self.connect()
            if not self.config.graph_name:
                return GraphSchemaResponse(
                    success=False,
                    error="graph_name is required",
                )
            token = await self._auth.get_token()
            url = f"{self._base_url}/graphs/{self.config.graph_name}/schema"
            response = await self._http.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                params={"fully_qualified": "false"},
            )
            if response.status_code == 401:
                self._auth.invalidate()
                token = await self._auth.get_token()
                response = await self._http.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    params={"fully_qualified": "false"},
                )
            response.raise_for_status()
            payload = response.json()
            schema = SchemaMapper.map(payload)
            return GraphSchemaResponse(success=True, data=schema)
        except httpx.HTTPStatusError as http_err:
            return GraphSchemaResponse(success=False, error=self._format_http_error(http_err))
        except Exception as exc:
            return GraphSchemaResponse(success=False, error=str(exc))

    async def get_schema(self) -> SchemaResponse:
        return SchemaResponse(
            success=False,
            error="Table schema is not implemented for RocketGraph",
        )

    async def get_sample_data(self) -> SampleDataResponse:
        return SampleDataResponse(
            success=False,
            error="Sample data is not implemented for RocketGraph",
        )

    def get_api_info(self, project_name: str) -> Dict[str, Any]:
        base = f"/api/rocketgraph/{project_name}"
        return {
            "type": "rocketgraph",
            "api_urls": {
                "info": base,
                "query": f"{base}/query",
                "graphSchema": f"{base}/graphSchema",
                "test": f"{base}/test",
            },
            "version": "1.0",
            "features": {
                "property_graph": True,
                "cypher": True,
                "gql": True,
                "graph_schema": True,
            },
        }

    async def _post_with_retry(self, url: str, body: Dict[str, Any]) -> httpx.Response:
        token = await self._auth.get_token()
        response = await self._http.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            self._auth.invalidate()
            token = await self._auth.get_token()
            response = await self._http.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        return response

    @staticmethod
    def _format_http_error(err: httpx.HTTPStatusError) -> str:
        try:
            body = err.response.json()
            error_obj = body.get("error") if isinstance(body, dict) else None
            if isinstance(error_obj, dict):
                code = error_obj.get("code", "")
                msg = error_obj.get("message", "")
                return f"{code}: {msg}" if code else msg or str(err)
        except Exception:
            pass
        return f"HTTP {err.response.status_code}: {err.response.text[:300]}"
```

- [ ] **Step 2: Smoke-test the import**

```bash
python -c "from kineviz_database_proxy.drivers.rocketgraph import RocketGraphDriver, SchemaMapper, QueryParser, AuthClient; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Run all unit tests so far to make sure nothing regressed**

```bash
python -m pytest tests/ -v
```

Expected: All previously-passing tests still pass (SchemaMapper, QueryParser, AuthClient).

- [ ] **Step 4: Commit**

```bash
git add src/kineviz_database_proxy/drivers/rocketgraph.py
git commit -m "feat(rocketgraph): implement RocketGraphDriver (connect/query/schema)"
```

---

## Task 8: Register Driver in Factory

**Files:**
- Modify: `src/kineviz_database_proxy/drivers/factory.py`

- [ ] **Step 1: Update factory.py to register RocketGraphDriver**

Replace contents of `src/kineviz_database_proxy/drivers/factory.py`:

```python
"""
Driver factory for creating database drivers
"""

from typing import Dict, Type
from .base import BaseDatabaseDriver
from .spanner import SpannerDriver
from .rocketgraph import RocketGraphDriver
from ..models.project import Project, DatabaseConfig, DatabaseType


class DriverFactory:
    """Factory for creating database drivers"""

    _drivers: Dict[DatabaseType, Type[BaseDatabaseDriver]] = {
        DatabaseType.SPANNER: SpannerDriver,
        DatabaseType.ROCKETGRAPH: RocketGraphDriver,
    }

    @classmethod
    def create_driver(cls, project: Project) -> BaseDatabaseDriver:
        """Create a driver instance for the given database type"""
        config = project.database_config
        driver_class = cls._drivers.get(config.type)
        if not driver_class:
            raise ValueError(f"Unsupported database type: {config.type}")

        return driver_class(project)

    @classmethod
    def register_driver(cls, db_type: DatabaseType, driver_class: Type[BaseDatabaseDriver]) -> None:
        """Register a new driver type"""
        cls._drivers[db_type] = driver_class

    @classmethod
    def get_supported_types(cls) -> list[DatabaseType]:
        """Get list of supported database types"""
        return list(cls._drivers.keys())
```

- [ ] **Step 2: Smoke-test factory creates RocketGraph driver**

```bash
python -c "
from kineviz_database_proxy.drivers.factory import DriverFactory
from kineviz_database_proxy.models.project import Project, DatabaseConfig, DatabaseType, AuthType
p = Project(id='x', name='t', database_type=DatabaseType.ROCKETGRAPH,
            database_config=DatabaseConfig(type=DatabaseType.ROCKETGRAPH, host='h', port=4368,
                                            graph_name='g', auth_type=AuthType.BEARER_TOKEN))
d = DriverFactory.create_driver(p)
print(type(d).__name__)
print(d.get_api_info('t')['api_urls'])
"
```

Expected:
```
RocketGraphDriver
{'info': '/api/rocketgraph/t', 'query': '/api/rocketgraph/t/query', 'graphSchema': '/api/rocketgraph/t/graphSchema', 'test': '/api/rocketgraph/t/test'}
```

- [ ] **Step 3: Commit**

```bash
git add src/kineviz_database_proxy/drivers/factory.py
git commit -m "feat(rocketgraph): register RocketGraphDriver in DriverFactory"
```

---

## Task 9: Driver Integration Test (URL building + API info)

**Files:**
- Create: `tests/test_rocketgraph_driver.py`

- [ ] **Step 1: Write tests for URL construction and API info**

Create `tests/test_rocketgraph_driver.py`:

```python
"""Integration tests for RocketGraphDriver (URL building, api info, schema/sample_data stubs)."""
import pytest

from kineviz_database_proxy.drivers.rocketgraph import RocketGraphDriver
from kineviz_database_proxy.models.project import (
    AuthType,
    DatabaseConfig,
    DatabaseType,
    OAuthConfig,
    Project,
)


def _make_project(**cfg_kwargs):
    base = dict(
        type=DatabaseType.ROCKETGRAPH,
        host="example.com",
        port=4368,
        graph_name="social",
        auth_type=AuthType.BEARER_TOKEN,
        oauth_config=OAuthConfig(token="tok"),
    )
    base.update(cfg_kwargs)
    return Project(
        id="p", name="proj", database_type=DatabaseType.ROCKETGRAPH,
        database_config=DatabaseConfig(**base),
    )


def test_default_standalone_base_url():
    project = _make_project()
    driver = RocketGraphDriver(project)
    assert driver._base_url == "http://example.com:4368/api/v1"


def test_plugin_mode_base_url():
    project = _make_project(deployment_mode="plugin", port=8080)
    driver = RocketGraphDriver(project)
    assert driver._base_url == "http://example.com:8080/api/xgt/v1"


def test_tls_uses_https():
    project = _make_project(use_tls=True)
    driver = RocketGraphDriver(project)
    assert driver._base_url.startswith("https://")


def test_custom_api_base_path():
    project = _make_project(api_base_path="/custom/path")
    driver = RocketGraphDriver(project)
    assert driver._base_url == "http://example.com:4368/custom/path"


def test_get_api_info_shape():
    project = _make_project()
    driver = RocketGraphDriver(project)
    info = driver.get_api_info("proj")
    assert info["type"] == "rocketgraph"
    assert info["api_urls"]["info"] == "/api/rocketgraph/proj"
    assert info["api_urls"]["query"] == "/api/rocketgraph/proj/query"
    assert info["api_urls"]["graphSchema"] == "/api/rocketgraph/proj/graphSchema"


@pytest.mark.asyncio
async def test_get_schema_returns_not_implemented():
    project = _make_project()
    driver = RocketGraphDriver(project)
    result = await driver.get_schema()
    assert result.success is False
    assert "not implemented" in result.error.lower()


@pytest.mark.asyncio
async def test_get_sample_data_returns_not_implemented():
    project = _make_project()
    driver = RocketGraphDriver(project)
    result = await driver.get_sample_data()
    assert result.success is False
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_rocketgraph_driver.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 3: Run full test suite to catch any regressions**

```bash
python -m pytest tests/ -v
```

Expected: ALL tests PASS (SchemaMapper + QueryParser + AuthClient + Driver = ~30 tests).

- [ ] **Step 4: Commit**

```bash
git add tests/test_rocketgraph_driver.py
git commit -m "test(rocketgraph): add driver integration tests (URL building, api_info, stubs)"
```

---

## Task 10: Frontend Types Extension

**Files:**
- Modify: `frontend/src/types/project.ts`

- [ ] **Step 1: Update types/project.ts**

Replace contents of `frontend/src/types/project.ts`:

```typescript
export type DatabaseType = 'spanner' | 'rocketgraph' | 'postgresql' | 'mysql' | 'mongodb';

export type AuthType = 'oauth2' | 'service_account' | 'google_ADC' | 'username_password' | 'bearer_token';

export interface OAuthConfig {
  client_id?: string;
  client_secret?: string;
  redirect_uri?: string;
  scopes?: string[];
  token?: string;
  refresh_token?: string;
  expires_in?: number;
  last_refreshed?: number;
}

export interface DatabaseConfig {
  type: DatabaseType;
  host?: string;
  port?: number;
  project_id?: string;
  instance_id?: string;
  database_id?: string;
  graph_name?: string;
  auth_type: AuthType;
  username?: string;
  password?: string;
  oauth_config?: OAuthConfig;
  service_account_path?: string;
  // RocketGraph specific
  use_tls?: boolean;
  deployment_mode?: 'standalone' | 'plugin';
  api_base_path?: string;
  options: Record<string, any>;
}

export interface Project {
  id: string;
  name: string;
  database_type: DatabaseType;
  database_config: DatabaseConfig;
  create_time: string;
  update_time: string;
}

export interface ProjectCreate {
  name: string;
  database_type: DatabaseType;
  database_config: DatabaseConfig;
}

export interface ProjectUpdate {
  name?: string;
  database_config?: DatabaseConfig;
}

export interface APIInfo {
  type: DatabaseType;
  api_urls: Record<string, string>;
  version?: string;
}

export interface QueryRequest {
  query: string;
  parameters: Record<string, any>;
}

export interface QueryResponse {
  success: boolean;
  data?: any;
  error?: string;
  execution_time?: number;
}

export interface Category {
  name: string;
  props?: string[];
  keys?: string[];
  propsTypes?: Record<string, string>;
}

export interface Relationship {
  name: string;
  props?: string[];
  keys?: string[];
  propsTypes?: Record<string, string>;
  startCategory: string;
  endCategory: string;
}

export interface SchemaResponse {
  success: boolean;
  data?: Record<string, Record<string, string>>;
  error?: string;
}

export interface GraphSchemaResponse {
  success: boolean;
  data?: {
    categories: Category[];
    relationships: Relationship[];
  };
  error?: string;
}

export interface SampleDataResponse {
  success: boolean;
  data?: Record<string, any>;
  error?: string;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd e:/projects/graphxr-database-proxy/frontend && npx tsc --noEmit 2>&1 | head -30
```

Expected: No errors (or only pre-existing errors unrelated to the changes).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/project.ts
git commit -m "feat(rocketgraph): extend frontend types with rocketgraph and bearer_token"
```

---

## Task 11: Frontend ProjectForm — Add RocketGraph Option

**Files:**
- Modify: `frontend/src/components/ProjectForm.tsx`

- [ ] **Step 1: Add RocketGraph option to Database Type select**

In `frontend/src/components/ProjectForm.tsx`, locate the `<Select placeholder="Select database type" onChange={handleDatabaseTypeChange}>` block (around line 400-408) and add a new `<Option>`:

Replace:
```tsx
<Select
  placeholder="Select database type"
  onChange={handleDatabaseTypeChange}
>
  <Option value="spanner">Google Cloud Spanner</Option>
  {/* <Option value="postgresql">PostgreSQL</Option>
  <Option value="mysql">MySQL</Option>
  <Option value="mongodb">MongoDB</Option> */}
</Select>
```

With:
```tsx
<Select
  placeholder="Select database type"
  onChange={handleDatabaseTypeChange}
>
  <Option value="spanner">Google Cloud Spanner</Option>
  <Option value="rocketgraph">RocketGraph</Option>
  {/* <Option value="postgresql">PostgreSQL</Option>
  <Option value="mysql">MySQL</Option>
  <Option value="mongodb">MongoDB</Option> */}
</Select>
```

- [ ] **Step 2: Add bearer_token + username_password to Authentication Type select**

In the same file, locate the Auth type select (around line 419-428) and replace:

```tsx
<Select
  placeholder="Select authentication type"
  onChange={handleAuthTypeChange}
>
  <Option value="service_account">Service Account</Option>
  <Option value="google_ADC">Application Default Credentials (ADC)</Option>
  <Option value="oauth2">OAuth2</Option>
  {/* <Option value="username_password">Username/Password</Option> */}
</Select>
```

With:
```tsx
<Select
  placeholder="Select authentication type"
  onChange={handleAuthTypeChange}
>
  {databaseType === "spanner" && (
    <>
      <Option value="service_account">Service Account</Option>
      <Option value="google_ADC">Application Default Credentials (ADC)</Option>
      <Option value="oauth2">OAuth2</Option>
    </>
  )}
  {databaseType === "rocketgraph" && (
    <>
      <Option value="username_password">Username / Password</Option>
      <Option value="bearer_token">Bearer Token</Option>
    </>
  )}
</Select>
```

- [ ] **Step 3: Reset auth type when switching database type**

Find `handleDatabaseTypeChange` (around line 151-160) and replace it:

```tsx
const handleDatabaseTypeChange = (value: DatabaseType) => {
  setDatabaseType(value);
  form.resetFields([
    "project_id",
    "instance_id",
    "database_id",
    "host",
    "port",
    "rg_token",
    "use_tls",
    "deployment_mode",
    "api_base_path",
  ]);

  if (value === "rocketgraph") {
    const defaultAuth: AuthType = "username_password";
    setAuthType(defaultAuth);
    form.setFieldsValue({
      auth_type: defaultAuth,
      deployment_mode: "standalone",
      port: 4368,
      use_tls: false,
    });
  } else if (value === "spanner") {
    setAuthType(DefaultAuthType);
    form.setFieldsValue({ auth_type: DefaultAuthType });
  }
};
```

- [ ] **Step 4: Add RocketGraph configuration card**

After the existing Spanner card block (the `{databaseType === "spanner" && (...)}` block that ends around line 770+), and BEFORE the closing of the form, add a new RocketGraph card. Locate where Spanner card ends (search for `)}` closing the spanner block) and insert immediately after:

```tsx
{databaseType === "rocketgraph" && (
  <Card
    title="RocketGraph Configuration"
    size="small"
    style={{ marginBottom: 16 }}
  >
    <Row gutter={16}>
      <Col span={12}>
        <Form.Item
          label="Deployment Mode"
          name="deployment_mode"
          rules={[{ required: true, message: "Please select deployment mode" }]}
        >
          <Select
            onChange={(val) => {
              form.setFieldsValue({
                port: val === "plugin" ? 8080 : 4368,
              });
            }}
          >
            <Option value="standalone">Standalone (default port 4368)</Option>
            <Option value="plugin">Plugin in MC (default port 8080)</Option>
          </Select>
        </Form.Item>
      </Col>
      <Col span={6}>
        <Form.Item
          label="Use TLS"
          name="use_tls"
        >
          <Select>
            <Option value={false}>HTTP</Option>
            <Option value={true}>HTTPS</Option>
          </Select>
        </Form.Item>
      </Col>
    </Row>
    <Row gutter={16}>
      <Col span={16}>
        <Form.Item
          label="Host"
          name="host"
          rules={[{ required: true, message: "Please enter host" }]}
        >
          <Input placeholder="e.g. kineviz.rocketgraph.com" />
        </Form.Item>
      </Col>
      <Col span={8}>
        <Form.Item
          label="Port"
          name="port"
          rules={[{ required: true, message: "Please enter port" }]}
        >
          <InputNumber min={1} max={65535} style={{ width: "100%" }} />
        </Form.Item>
      </Col>
    </Row>
    <Row gutter={16}>
      <Col span={24}>
        <Form.Item
          label="Graph Name"
          name="graph_name"
          rules={[{ required: true, message: "Please enter graph name" }]}
        >
          <Input placeholder="e.g. social" />
        </Form.Item>
      </Col>
    </Row>
    <Row gutter={16}>
      <Col span={24}>
        <Form.Item
          label="API Base Path (optional)"
          name="api_base_path"
          tooltip="Leave empty to use default: /api/v1 (standalone) or /api/xgt/v1 (plugin)"
        >
          <Input placeholder="(default based on deployment mode)" />
        </Form.Item>
      </Col>
    </Row>

    {authType === "username_password" && (
      <Card title="Credentials" size="small" type="inner">
        <Row gutter={16}>
          <Col span={12}>
            <Form.Item
              label="Username"
              name="username"
              rules={[{ required: true, message: "Please enter username" }]}
            >
              <Input placeholder="username" />
            </Form.Item>
          </Col>
          <Col span={12}>
            <Form.Item
              label="Password"
              name="password"
              rules={[{ required: true, message: "Please enter password" }]}
            >
              <Input.Password placeholder="password" />
            </Form.Item>
          </Col>
        </Row>
      </Card>
    )}

    {authType === "bearer_token" && (
      <Card title="Bearer Token" size="small" type="inner">
        <Row gutter={16}>
          <Col span={24}>
            <Form.Item
              label="Token"
              name="rg_token"
              rules={[{ required: true, message: "Please enter bearer token" }]}
            >
              <Input.Password placeholder="Pre-acquired bearer token (e.g. MC session)" />
            </Form.Item>
          </Col>
        </Row>
      </Card>
    )}
  </Card>
)}
```

- [ ] **Step 5: Update handleSubmit to construct RocketGraph database_config**

In `handleSubmit` (around line 96-149), add a new branch after the spanner branch. Locate the line `if (database_type === "spanner") {` and add this after the closing `}` of the spanner block but before `const projectData`:

```tsx
if (database_type === "rocketgraph") {
  databaseConfig = {
    ...databaseConfig,
    host: dbConfigFields.host,
    port: dbConfigFields.port,
    graph_name: dbConfigFields.graph_name,
    use_tls: dbConfigFields.use_tls === true || dbConfigFields.use_tls === "true",
    deployment_mode: dbConfigFields.deployment_mode || "standalone",
    api_base_path: dbConfigFields.api_base_path || undefined,
  };

  if (authType === "username_password") {
    databaseConfig.username = dbConfigFields.username;
    databaseConfig.password = dbConfigFields.password;
    databaseConfig.oauth_config = {};
  } else if (authType === "bearer_token") {
    databaseConfig.oauth_config = {
      token: dbConfigFields.rg_token,
    };
  }
}
```

- [ ] **Step 6: Build the frontend to verify it compiles**

```bash
cd e:/projects/graphxr-database-proxy/frontend && npm run build 2>&1 | tail -20
```

Expected: Build succeeds (no TS errors related to RocketGraph code).

- [ ] **Step 7: Commit**

```bash
cd e:/projects/graphxr-database-proxy && git add frontend/src/components/ProjectForm.tsx
git commit -m "feat(rocketgraph): add RocketGraph option to project creation UI

- Add 'RocketGraph' to database type select
- Add username_password and bearer_token auth options
- Add RocketGraph configuration card (host/port/graph_name/deployment_mode/tls)
- Wire handleSubmit to construct rocketgraph database_config"
```

---

## Task 12: End-to-End Smoke Test (Manual, against kineviz.rocketgraph.com)

**Files:** None (manual verification)

- [ ] **Step 1: Start the proxy in dev mode**

```bash
cd e:/projects/graphxr-database-proxy && npm run dev
```

Expected: Proxy starts, frontend opens at http://localhost:8080 or http://localhost:9080.

- [ ] **Step 2: Create a RocketGraph project via UI**

In the web UI:
1. Click "Create New Project"
2. Project Name: `rg_test`
3. Database Type: `RocketGraph`
4. Deployment Mode: `Standalone`
5. Host: `kineviz.rocketgraph.com`
6. Port: `4368` (or appropriate port for the public demo)
7. Use TLS: `HTTPS`
8. Graph Name: (use one available in the demo, e.g. consult kineviz.rocketgraph.com UI)
9. Auth Type: `Username / Password`
10. Username / Password: (use demo credentials)
11. Click Create

Expected: Project created without errors.

- [ ] **Step 3: Verify API Info endpoint**

```bash
curl -s http://localhost:9080/api/rocketgraph/rg_test
```

Expected: JSON with `type: "rocketgraph"` and `api_urls` containing `query` and `graphSchema`.

- [ ] **Step 4: Verify Graph Schema endpoint**

```bash
curl -s http://localhost:9080/api/rocketgraph/rg_test/graphSchema | head -100
```

Expected: JSON with `success: true` and a non-empty `data.categories` and/or `data.relationships`.

- [ ] **Step 5: Verify Query endpoint with simple Cypher**

```bash
curl -s -X POST http://localhost:9080/api/rocketgraph/rg_test/query \
  -H "Content-Type: application/json" \
  -d '{"query": "MATCH (n) RETURN n LIMIT 5"}'
```

Expected: JSON with `success: true` and either `data.type: "GRAPH"` (with nodes array) or `data.type: "TABLE"` (with rows).

- [ ] **Step 6: Document smoke test results**

Append a section to `docs/superpowers/specs/2026-05-21-rocketgraph-support-design.md`:

```markdown
## 11. End-to-End Verification (kineviz.rocketgraph.com)

Date: <today>

- [✓ / ✗] API Info returned correct shape
- [✓ / ✗] Graph Schema returned non-empty categories/relationships
- [✓ / ✗] Query with `MATCH (n) RETURN n LIMIT 5` returned GRAPH type
- Notes on actual node/edge JSON structure observed:
  <fill in based on actual response>
```

- [ ] **Step 7: If QueryParser needed adjustments**

If the node/edge JSON shape from RocketGraph differs from what QueryParser detects (e.g., different field names), update `QueryParser.NODE_ID_KEYS`, `NODE_LABEL_KEYS`, `EDGE_SOURCE_KEYS`, etc. tuples, then add a new test case to `tests/test_rocketgraph_query_parser.py` covering the actual shape observed, and commit the fix.

- [ ] **Step 8: Commit any adjustments**

```bash
git add -A
git commit -m "docs(rocketgraph): record end-to-end verification against kineviz.rocketgraph.com"
```

---

## Self-Review Checklist (run before handoff)

1. **Spec coverage**:
   - DatabaseType.ROCKETGRAPH added → Task 1 ✓
   - BEARER_TOKEN auth → Task 1 ✓
   - Config fields (use_tls, deployment_mode, api_base_path) → Task 1 ✓
   - httpx dependency → Task 2 ✓
   - SchemaMapper → Task 4 ✓
   - QueryParser → Task 5 ✓
   - AuthClient (login, caching, refresh, persistence) → Task 6 ✓
   - RocketGraphDriver (connect/query/schema/api_info) → Task 7 ✓
   - Factory registration → Task 8 ✓
   - Frontend types → Task 10 ✓
   - Frontend UI → Task 11 ✓
   - E2E verification → Task 12 ✓
   - Not implemented (schema/sampleData) returns clear error → Task 7 ✓
   - 401 retry with re-login → Task 7 (`_post_with_retry`) ✓
   - Tests for: QueryParser, SchemaMapper, AuthClient, URL building → Tasks 4/5/6/9 ✓

2. **Placeholder scan**: No TBDs, no "implement later", no "similar to". All code blocks are complete.

3. **Type consistency**: `SchemaMapper.map()`, `QueryParser.parse()`, `AuthClient.get_token()`, `AuthClient.invalidate()`, `RocketGraphDriver._base_url`, `_auth`, `_http` — used consistently across tasks 4-9.

---

## Notes for Implementer

- **The RocketGraph node/edge JSON shape is not fully specified** in the docs. QueryParser is built to handle multiple aliases (`id`/`identifier`, `labels`/`label`, `source`/`source_node_identifier`). Task 12 step 7 covers the contingency where actual shapes differ — extend the tuples and add tests.
- **Demo credentials for kineviz.rocketgraph.com**: The plan assumes the user knows them or will obtain them. If unavailable, skip Task 12 steps 2-7 (only run a syntactic smoke test against a stub server).
- **Use TLS** uses a Select with boolean values to keep TLS explicit. If you prefer a Switch component, replace the Select with `<Switch />` and add `valuePropName="checked"` to the Form.Item.
- **Don't add features beyond what's in the plan** — schema endpoint, sample data, async query, etc. are explicitly excluded.
