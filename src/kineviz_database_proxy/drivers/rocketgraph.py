# -*- coding: utf-8 -*-
"""
RocketGraph driver — connects to RocketGraph REST API (XGT-based graph DB).

Supports Standalone (JWT) and Plugin (Bearer Token) deployment modes.
Implements Info, Query, GraphSchema endpoints per doc/API_Reference.md.
"""

import re
import time
import json
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .base import BaseDatabaseDriver
from .graph_support import RocketGraphIntents
from ..models.project import (
    AuthType,
    Category,
    GraphData,
    GraphSchema,
    GraphSchemaResponse,
    Node,
    Project,
    QueryData,
    QueryResponse,
    Relationship,
    RelationshipData,
    SampleDataResponse,
    SchemaResponse,
)
from ..services.project_service import ProjectService


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

        props_types: Dict[str, str] = {
            p["name"]: p.get("type", "TEXT") for p in properties if p.get("name")
        }
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

        props_types: Dict[str, str] = {
            p["name"]: p.get("type", "TEXT") for p in properties if p.get("name")
        }
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


class QueryParser:
    """Parses a RocketGraph query response into TABLE or GRAPH form.

    RocketGraph only emits a true graph structure when the query returns a
    path variable (e.g. `MATCH p=(n)-[r]->(m) RETURN p`). In that case each
    cell is an array alternating node, edge, node, edge, ... — every entry
    is a dict with `id`, `properties`, and `metadata`. Nodes carry
    `metadata.key`; edges carry `metadata.source_key` and
    `metadata.target_key`. Edge endpoints are inferred from positional
    adjacency within the path (no explicit source/target fields).

    Any other return shape — scalar columns, bare variables flattened into
    `n_propname` columns, aggregations — comes back as a TABLE.
    """

    @classmethod
    def parse(cls, response: Dict[str, Any], is_graph: bool) -> QueryData:
        """Parse a RocketGraph payload.

        ``is_graph`` is decided upstream from the query syntax (see
        ``_rewrite_for_graph_intent``); the parser does not re-derive it
        from the response shape.
        """
        columns: List[str] = response.get("columns") or []
        data: List[List[Any]] = response.get("data") or []

        if is_graph:
            return cls._to_graph(data)
        return cls._to_table(columns, data)

    @staticmethod
    def _is_metadata_node(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        md = value.get("metadata")
        if not isinstance(md, dict):
            return False
        return (
            "id" in value
            and "properties" in value
            and "key" in md
            and "source_key" not in md
        )

    @staticmethod
    def _is_metadata_edge(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        md = value.get("metadata")
        if not isinstance(md, dict):
            return False
        return (
            "id" in value
            and "properties" in value
            and "source_key" in md
            and "target_key" in md
        )

    @classmethod
    def _to_table(cls, columns: List[str], data: List[List[Any]]) -> QueryData:
        """Return a 2D array with column headers as the first row.

        Each data row is padded to the column count, and any list cells are
        joined into a comma-separated string so the UI can render array
        values in a single cell.
        """
        col_count = len(columns)
        rows: List[List[Any]] = [list(columns)]
        rows.extend(
            [cls._normalize_cell(row[i] if i < len(row) else None)
             for i in range(col_count)]
            for row in data
        )
        return QueryData(type="TABLE", data=list(rows))

    @staticmethod
    def _normalize_cell(val: Any) -> Any:
        if isinstance(val, list):
            return ", ".join(str(item) for item in val)
        return val

    @classmethod
    def _to_graph(cls, data: List[List[Any]]) -> QueryData:
        nodes: Dict[str, Node] = {}
        edges: Dict[str, RelationshipData] = {}

        for row in data:
            for cell in row:
                if isinstance(cell, list):
                    cls._extract_path(cell, nodes, edges)

        return QueryData(
            type="GRAPH",
            data=GraphData(
                nodes=list(nodes.values()),
                relationships=list(edges.values()),
            ),
        )

    @classmethod
    def _extract_path(
        cls,
        path: List[Any],
        nodes: Dict[str, Node],
        edges: Dict[str, RelationshipData],
    ) -> None:
        """Walk an alternating node/edge/node/... array.

        Pending edges are paired with the previous node (source) and the
        next node (target) as the walk continues.
        """
        prev_node_id: Optional[str] = None
        pending_edge: Optional[Dict[str, Any]] = None

        for item in path:
            if not isinstance(item, dict):
                continue
            if cls._is_metadata_node(item):
                node = cls._extract_node(item)
                if node.id not in nodes:
                    nodes[node.id] = node
                if pending_edge is not None and prev_node_id is not None:
                    edge = cls._extract_edge(pending_edge, prev_node_id, node.id)
                    if edge.id not in edges:
                        edges[edge.id] = edge
                    pending_edge = None
                prev_node_id = node.id
            elif cls._is_metadata_edge(item):
                pending_edge = item

    @classmethod
    def _extract_node(cls, value: Dict[str, Any]) -> Node:
        node_id = str(value.get("id", ""))
        md = value.get("metadata") or {}
        name = md.get("name")
        labels = [str(name)] if name else []
        properties = value.get("properties") or {}
        return Node(id=node_id, labels=labels, properties=properties)

    @classmethod
    def _extract_edge(
        cls,
        value: Dict[str, Any],
        start_id: str,
        end_id: str,
    ) -> RelationshipData:
        edge_id = str(value.get("id", ""))
        md = value.get("metadata") or {}
        edge_type = str(md.get("name") or "")
        properties = value.get("properties") or {}
        return RelationshipData(
            id=edge_id,
            type=edge_type,
            startNodeId=start_id,
            endNodeId=end_id,
            properties=properties,
        )


class AuthClient:
    """Manages authentication tokens for RocketGraph requests.

    - USERNAME_PASSWORD: logs in via /auth/xgt/basic, caches JWT in oauth_config.
    - BEARER_TOKEN: uses static token from oauth_config.token, no refresh.
    - Empty credentials (no username/password OR no token) → anonymous access:
      get_token() returns None and callers should skip the Authorization header.
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

    async def get_token(self) -> Optional[str]:
        """Return a valid bearer token, refreshing if necessary.

        Returns None when credentials are intentionally absent — the caller
        should then send the request without an Authorization header.
        """
        if self.config.auth_type == AuthType.BEARER_TOKEN:
            return self.config.oauth_config.token if self.config.oauth_config else None

        if self.config.auth_type == AuthType.USERNAME_PASSWORD:
            # No credentials → anonymous access.
            if not self.config.username and not self.config.password:
                return None
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
        """POST /auth/xgt/basic, JWT in JSON body.

        Sends whatever username/password are configured (either may be empty).
        The anonymous opt-out — both fields empty — is handled upstream in
        get_token() before this method runs.
        """
        url = f"{self.base_url}/auth/xgt/basic"
        body = {
            "username": self.config.username or "",
            "password": self.config.password or "",
        }

        async with httpx.AsyncClient(timeout=30.0) as http_client:
            response = await http_client.post(url, json=body)
            response.raise_for_status()
            data = response.json()

        token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        await self._persist_token(token, expires_in)
        return token

    async def _persist_token(self, token: str, expires_in: int) -> None:
        """Cache token in oauth_config and write it to the project config file."""
        now = time.time()

        project_service = ProjectService()
        await project_service.update_project_token(
            project_id=self.project.id,
            token=token,
            last_refreshed=now,
            expires_in=expires_in,
        )

        if self.config.oauth_config is None:
            from ..models.project import OAuthConfig
            self.config.oauth_config = OAuthConfig()
        self.config.oauth_config.token = token
        self.config.oauth_config.last_refreshed = now
        self.config.oauth_config.expires_in = expires_in

        self._force_refresh = False


_NODE_VAR_RE = re.compile(r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?=[:)\s{])")
_REL_VAR_RE = re.compile(r"\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?=[:\]\s*{])")
_PATH_VAR_PREFIX_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*")
_BARE_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MATCH_KW_RE = re.compile(r"\bMATCH\b", re.IGNORECASE)
_RETURN_KW_RE = re.compile(r"\bRETURN\b", re.IGNORECASE)
_WHERE_KW_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_RETURN_CLAUSE_END_RE = re.compile(
    r"\b(ORDER\s+BY|SKIP|LIMIT)\b",
    re.IGNORECASE,
)
_FORBIDDEN_PRE_RETURN_CLAUSE_RE = re.compile(
    r"\b(OPTIONAL\s+MATCH|MERGE|WITH|UNWIND|CREATE|DELETE|REMOVE|SET|"
    r"CALL|FOREACH|UNION)\b",
    re.IGNORECASE,
)


def _split_top_level_commas(s: str) -> List[str]:
    """Split on commas at the top paren/brace/bracket depth."""
    parts: List[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(s):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(s[start:i])
            start = i + 1
    parts.append(s[start:])
    return parts


def _rewrite_for_graph_intent(query: str) -> Tuple[str, bool]:
    """Classify a query as graph or table intent and normalize it.

    Returns ``(query_to_execute, is_graph_intent)``.

    Graph intent applies when:
    - exactly one MATCH clause is present (no OPTIONAL MATCH / MERGE / WITH /
      UNWIND / CREATE / DELETE / UNION / etc.),
    - the RETURN clause is either ``*`` or a comma-separated list of bare
      variable names (no AS aliases, property access, or function calls),
    - every RETURN variable is bound by the MATCH clause,
    - the user has not already bound ``p`` to a node or relationship.

    On graph intent the MATCH pattern is wrapped in ``p=...`` (renaming any
    existing path variable to ``p``) and the RETURN clause is replaced with
    ``RETURN p`` — the only shape that yields RocketGraph's native graph
    payload. Anything else is left intact and treated as a table.
    """
    cleaned = query.rstrip().rstrip(";").rstrip()

    return_m = _RETURN_KW_RE.search(cleaned)
    if return_m is None:
        return cleaned, False

    pre_return = cleaned[: return_m.start()]
    after_return_text = cleaned[return_m.end():]
    end_m = _RETURN_CLAUSE_END_RE.search(after_return_text)
    if end_m:
        return_body_stripped = after_return_text[: end_m.start()].strip()
        post_return = after_return_text[end_m.start():]
    else:
        return_body_stripped = after_return_text.strip()
        post_return = ""

    if not return_body_stripped:
        return cleaned, False

    if return_body_stripped == "*":
        return_vars: List[str] = []
    else:
        return_vars = [
            v.strip()
            for v in _split_top_level_commas(return_body_stripped)
            if v.strip()
        ]
        if not return_vars or not all(_BARE_VAR_RE.match(v) for v in return_vars):
            return cleaned, False

    if _FORBIDDEN_PRE_RETURN_CLAUSE_RE.search(pre_return):
        return cleaned, False

    match_clauses = list(_MATCH_KW_RE.finditer(pre_return))
    if len(match_clauses) != 1:
        return cleaned, False

    match_kw_end = match_clauses[0].end()
    where_m = _WHERE_KW_RE.search(pre_return[match_kw_end:])
    pattern_end = (
        match_kw_end + where_m.start() if where_m else len(pre_return)
    )
    pattern_section = pre_return[match_kw_end:pattern_end].strip()
    if not pattern_section:
        return cleaned, False

    path_var_m = _PATH_VAR_PREFIX_RE.match(pattern_section)
    if path_var_m and pattern_section[path_var_m.end():].startswith(("(", "[")):
        path_var_name: Optional[str] = path_var_m.group(1)
        pattern_text = pattern_section[path_var_m.end():]
    else:
        path_var_name = None
        pattern_text = pattern_section

    if not pattern_text:
        return cleaned, False

    pattern_vars: set = set()
    for vm in _NODE_VAR_RE.finditer(pattern_text):
        pattern_vars.add(vm.group(1))
    for vm in _REL_VAR_RE.finditer(pattern_text):
        pattern_vars.add(vm.group(1))

    # `p` collides with the path variable we're about to introduce.
    if "p" in pattern_vars:
        return cleaned, False

    bound_vars = pattern_vars | ({path_var_name} if path_var_name else set())
    if return_vars and not all(v in bound_vars for v in return_vars):
        return cleaned, False

    where_and_rest = pre_return[pattern_end:].lstrip()
    post_sep = (
        "" if not post_return or post_return[0].isspace() else " "
    )
    rewritten = (
        f"{pre_return[:match_kw_end]} p={pattern_text} "
        f"{where_and_rest}"
        f"RETURN p{post_sep}{post_return}"
    )
    return rewritten, True


class RocketGraphDriver(RocketGraphIntents, BaseDatabaseDriver):
    """Driver for RocketGraph (XGT-based) graph database via REST API.

    Two deployment modes:
    - standalone: standalone xgtrest service (default base /api/v1, port 4368).
    - plugin: xgt-rest plugin inside MC backend (base /api/xgt/v1, port 8080).
    """

    DEFAULT_STANDALONE_PATH = "/api/v1"
    DEFAULT_PLUGIN_PATH = "/api/xgt/v1"

    MODE_PLUGIN = "plugin"

    def __init__(self, project: Project):
        super().__init__(project)
        self._http: Optional[httpx.AsyncClient] = None
        self._base_url = self._build_base_url()
        self._auth = AuthClient(project, self._base_url)

    def _build_base_url(self) -> str:
        cfg = self.config
        scheme = "https" if cfg.use_tls else "http"
        host = cfg.host or "localhost"
        port = cfg.port or (8080 if cfg.deployment_mode == self.MODE_PLUGIN else 4368)
        if cfg.api_base_path:
            base_path = cfg.api_base_path
        elif cfg.deployment_mode == self.MODE_PLUGIN:
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
            response = await self._get_with_retry(f"{self._base_url}/version")
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

            # Classify graph vs table from the query itself and normalize:
            # graph intent → `MATCH p=... RETURN p` so the server emits its
            # native path payload; table intent → query unchanged.
            query, is_graph_intent = _rewrite_for_graph_intent(query)

            # replace id(n) to elementId(n) for rocketgraph
            query = re.sub(r"\bid\(([^)]+)\)", r"elementId(\1)", query)

            body: Dict[str, Any] = {
                "query": query,
                "language": "cypher",
                "parameters": parameters or {},
            }
            # print(f"RocketGraph QUERY: {query}")

            response = await self._post_with_retry(url, body)
            response.raise_for_status()
            payload = response.json()

            # print(f"RocketGraph RESPONSE: {payload}")

            data = QueryParser.parse(payload, is_graph=is_graph_intent)
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

            url = f"{self._base_url}/graphs/{self.config.graph_name}/schema"
            response = await self._get_with_retry(url, params={"fully_qualified": "false"})
            response.raise_for_status()
            payload = response.json()
            schema = SchemaMapper.map(payload)
            # XGT has no node-identity function, so an intent statement re-selects a
            # seed by its category's key — which only this call knows.
            self.remember_graph_categories(
                {category.name: category.model_dump() for category in schema.categories}
            )
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

    async def _auth_headers(self) -> Dict[str, str]:
        """Build auth headers; empty dict when no credentials are configured."""
        token = await self._auth.get_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _post_with_retry(self, url: str, body: Dict[str, Any]) -> httpx.Response:
        headers = await self._auth_headers()
        response = await self._http.post(url, json=body, headers=headers)
        # Only retry on 401 if we actually had credentials to refresh.
        if response.status_code == 401 and headers:
            self._auth.invalidate()
            headers = await self._auth_headers()
            response = await self._http.post(url, json=body, headers=headers)
        return response

    async def _get_with_retry(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        headers = await self._auth_headers()
        response = await self._http.get(url, headers=headers, params=params)
        if response.status_code == 401 and headers:
            self._auth.invalidate()
            headers = await self._auth_headers()
            response = await self._http.get(url, headers=headers, params=params)
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
