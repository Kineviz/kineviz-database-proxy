# -*- coding: utf-8 -*-
"""
Google BigQuery driver, for datasets that carry a property graph.

The Python twin of ``modules/graphdb/databases/bigquery/service.js`` and
``modules/graphDBConnect/bigquery.js`` in graphxr-dev. BigQuery speaks the same
GQL as Spanner, so the traversal statements come from ``BIGQUERY_DIALECT`` and
only three things are genuinely BigQuery's own:

  - a query names its graph with ``GRAPH <dataset>.<graph>`` rather than
    ``GRAPH <graph>``, and carries a processing *location*;
  - there is no ``ELEMENT_ID()``; an element's identity lives in its JSON form,
    which is why graph queries are rewritten to project ``TO_JSON(v) AS v``
    per variable rather than Spanner's single ``SAFE_TO_JSON(path)`` column;
  - ``INFORMATION_SCHEMA.PROPERTY_GRAPHS`` spells its metadata differently from
    Spanner's — ``labelAndProperties`` instead of ``propertyDeclarations`` plus
    ``propertyDefinitions``.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as _datetime
import decimal
import json
import re
import time
from typing import Any, Dict, List, Sequence, Tuple

from .base import BaseDatabaseDriver
from .graph_support import BigQueryGraphIntents
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
from ..common.util import get_default_oauth_config
from ..services.project_service import ProjectService

#: Rows a query is capped at when it does not cap itself, so a stray
#: ``MATCH (n) RETURN n`` cannot pull a whole dataset into memory.
MAX_QUERY_RESULTS = 20000

#: Rows sampled per table by ``get_sample_data``.
SAMPLE_ROW_LIMIT = 10

#: Tables ``get_sample_data`` will look at, so a wide dataset is bounded at the
#: source rather than by whatever the caller does with the answer.
SAMPLE_TABLE_LIMIT = 200

DEFAULT_LOCATION = "US"

BIGQUERY_SCOPES = [
    "https://www.googleapis.com/auth/bigquery",
    "https://www.googleapis.com/auth/cloud-platform",
]


# ---------------------------------------------------------------------------
# Identifier and statement handling — pure, so tests can assert on it directly
# ---------------------------------------------------------------------------

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_MATCH_KW_RE = re.compile(r"\bMATCH\b", re.IGNORECASE)
_RETURN_KW_RE = re.compile(r"\bRETURN\b", re.IGNORECASE)
_WHERE_KW_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_LIMIT_KW_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_LEADING_MATCH_RE = re.compile(r"^\s*MATCH\b", re.IGNORECASE)
_LEADING_GRAPH_RE = re.compile(r"^\s*GRAPH\b", re.IGNORECASE)
_RETURN_TAIL_RE = re.compile(r"\b(ORDER\s+BY|SKIP|OFFSET|LIMIT)\b", re.IGNORECASE)
_BARE_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: A variable bound by a pattern: ``(n``, ``(n:Label``, ``[r:TYPE``, ``[r]``.
_PATTERN_VAR_RE = re.compile(r"[(\[]\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?=[:)\]\s{])")


class InvalidIdentifierError(ValueError):
    """A dataset or graph name that cannot be pasted into a statement safely."""


def validate_identifier(name: str, what: str) -> str:
    """
    Dataset and graph names reach the statement by interpolation — BigQuery has no
    parameter form for the ``GRAPH`` clause — so they are checked rather than
    trusted. They come from the project config, not from a request body, but a
    typo containing a backtick should fail loudly here instead of producing a
    statement that means something else.
    """
    text = str(name or "")
    if not _IDENTIFIER_RE.match(text):
        raise InvalidIdentifierError(f"Invalid BigQuery {what}: {name!r}")
    return text


def split_top_level_commas(text: str) -> List[str]:
    """Split on commas at the top paren/brace/bracket depth."""
    parts: List[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def pattern_variables(pattern: str) -> List[str]:
    """The variables a MATCH pattern binds, first-seen order."""
    found: List[str] = []
    for match in _PATTERN_VAR_RE.finditer(pattern):
        name = match.group(1)
        if name not in found:
            found.append(name)
    return found


def rewrite_graph_query(query: str, graph_namespace: str, max_results: int = MAX_QUERY_RESULTS) -> Tuple[str, bool]:
    """
    Normalise a statement for BigQuery, and say whether it yields a graph.

    Graph intent means: one ``MATCH`` followed by a ``RETURN`` that is ``*`` or a
    plain list of variable names. That is the only shape whose result can be read
    back as nodes and edges, so it is the only one rewritten — the projection
    becomes ``TO_JSON(v) AS v`` per variable, which is how identity, labels and
    properties reach the driver.

    Anything else — ``RETURN n.name``, ``RETURN count(*)``, plain SQL — is left
    alone and read as a table. A statement that starts at ``MATCH`` still gets the
    ``GRAPH`` clause prepended, and one that returns without a ``LIMIT`` gets one,
    because BigQuery will otherwise stream far more than the client can hold.
    """
    cleaned = query.strip().rstrip(";").strip()
    if not cleaned:
        return cleaned, False

    statement, is_graph = _rewrite_projection(cleaned)

    if not _LEADING_GRAPH_RE.match(statement) and _LEADING_MATCH_RE.match(statement):
        statement = f"GRAPH {graph_namespace}\n{statement}"

    if _RETURN_KW_RE.search(statement) and not _LIMIT_KW_RE.search(statement):
        statement = f"{statement}\nLIMIT {max_results}"

    return statement, is_graph


def _rewrite_projection(cleaned: str) -> Tuple[str, bool]:
    match_kw = _MATCH_KW_RE.search(cleaned)
    return_kw = _RETURN_KW_RE.search(cleaned)
    if not match_kw or not return_kw or return_kw.start() < match_kw.end():
        return cleaned, False

    head = cleaned[: match_kw.start()].strip()
    # The only clause allowed before MATCH is the graph namespace; anything else
    # (a WITH, a subquery) is a shape this rewrite does not understand.
    if head and not _LEADING_GRAPH_RE.match(head):
        return cleaned, False

    body = cleaned[match_kw.start() : return_kw.start()].strip()
    after_return = cleaned[return_kw.end() :]
    tail_kw = _RETURN_TAIL_RE.search(after_return)
    return_body = (after_return[: tail_kw.start()] if tail_kw else after_return).strip()
    tail = (after_return[tail_kw.start() :] if tail_kw else "").strip()
    if not body or not return_body:
        return cleaned, False

    where_kw = _WHERE_KW_RE.search(body)
    pattern = body[: where_kw.start()] if where_kw else body
    bound = pattern_variables(pattern)

    if return_body == "*":
        return_vars = bound
    else:
        parts = [part.strip() for part in split_top_level_commas(return_body) if part.strip()]
        if not parts or not all(_BARE_VAR_RE.match(part) for part in parts):
            return cleaned, False
        return_vars = parts

    if not return_vars:
        return cleaned, False

    projection = ", ".join(f"TO_JSON({var}) AS {var}" for var in return_vars)
    lines = [line for line in (head, body, f"RETURN {projection}", tail) if line]
    return "\n".join(lines), True


# ---------------------------------------------------------------------------
# Result mapping
# ---------------------------------------------------------------------------


def json_safe(value: Any) -> Any:
    """
    A BigQuery cell as something ``QueryResponse`` can serialise.

    The client library hands back ``datetime``, ``Decimal`` and ``bytes`` for the
    temporal, numeric and binary types; none of those survive JSON encoding as-is.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (_datetime.datetime, _datetime.date, _datetime.time)):
        return value.isoformat()
    if isinstance(value, _datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return str(value)


def as_graph_elements(value: Any) -> List[Dict[str, Any]]:
    """
    The graph elements inside one cell.

    ``TO_JSON`` reaches the client library as a parsed object, but a driver or a
    view that hands it over as text is accepted too, and a cell holding an array
    (a path) is flattened.
    """
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, list):
        elements: List[Dict[str, Any]] = []
        for item in value:
            elements.extend(as_graph_elements(item))
        return elements
    if isinstance(value, dict) and value.get("kind") in ("node", "edge"):
        return [value]
    return []


def _element_properties(element: Dict[str, Any]) -> Dict[str, Any]:
    """
    An element's properties, without the nulls.

    A property graph is built over columns, so every element carries every column
    of its table — the ones it does not use arrive as null and would otherwise
    show up as empty properties on the canvas.
    """
    properties = element.get("properties") or {}
    if not isinstance(properties, dict):
        return {}
    return {key: json_safe(value) for key, value in properties.items() if value is not None}


def parse_graph_rows(rows: Sequence[Any]) -> QueryData:
    """Fold ``TO_JSON`` columns into nodes and relationships, de-duplicated by identity."""
    nodes: Dict[str, Node] = {}
    relationships: Dict[str, RelationshipData] = {}

    for row in rows:
        values = row.values() if hasattr(row, "values") else row
        for cell in values:
            for element in as_graph_elements(cell):
                identifier = str(element.get("identifier") or "")
                if not identifier:
                    continue
                labels = [str(label) for label in (element.get("labels") or [])]
                properties = _element_properties(element)
                if element.get("kind") == "node":
                    nodes.setdefault(
                        identifier,
                        Node(id=identifier, labels=labels, properties=properties),
                    )
                else:
                    relationships.setdefault(
                        identifier,
                        RelationshipData(
                            id=identifier,
                            type=labels[0] if labels else "",
                            startNodeId=str(element.get("source_node_identifier") or ""),
                            endNodeId=str(element.get("destination_node_identifier") or ""),
                            properties=properties,
                        ),
                    )

    return QueryData(
        type="GRAPH",
        data=GraphData(nodes=list(nodes.values()), relationships=list(relationships.values())),
    )


def parse_table_rows(columns: Sequence[str], rows: Sequence[Any]) -> QueryData:
    """A 2D array whose first row is the column headers, as the contract expects."""
    header = [str(column) for column in columns]
    table: List[List[Any]] = [header]
    for row in rows:
        table.append([json_safe(row[column]) for column in header])
    return QueryData(type="TABLE", data=table)


def map_graph_metadata(metadata: Dict[str, Any]) -> GraphSchema:
    """
    ``property_graph_metadata_json`` -> the contract's graph schema.

    BigQuery nests a table's label and its properties together under
    ``labelAndProperties``, where Spanner keeps a separate declaration table; and
    it points at an edge's endpoints with ``sourceNodeReference.nodeTable``, where
    Spanner says ``sourceNodeTable.nodeTableName``. Endpoint references name the
    *table*, so they are resolved through the table -> label map before they can
    be used as categories.
    """
    categories: Dict[str, Category] = {}
    relationships: Dict[str, Relationship] = {}
    label_by_table: Dict[str, str] = {}

    for node_table in metadata.get("nodeTables") or []:
        table_name = str(node_table.get("name") or "")
        label, props_types = _label_and_properties(node_table, table_name)
        if not label:
            continue
        label_by_table[table_name] = label
        keys = [str(key) for key in (node_table.get("keyColumns") or [])]
        categories[label] = Category(
            name=label,
            props=list(props_types.keys()),
            keys=keys,
            keysTypes={key: props_types.get(key, "STRING") for key in keys},
            propsTypes=props_types,
        )

    for edge_table in metadata.get("edgeTables") or []:
        table_name = str(edge_table.get("name") or "")
        label, props_types = _label_and_properties(edge_table, table_name)
        if not label:
            continue
        keys = [str(key) for key in (edge_table.get("keyColumns") or [])]
        start_table = str((edge_table.get("sourceNodeReference") or {}).get("nodeTable") or "")
        end_table = str((edge_table.get("destinationNodeReference") or {}).get("nodeTable") or "")
        relationships[label] = Relationship(
            name=label,
            props=list(props_types.keys()),
            keys=keys,
            keysTypes={key: props_types.get(key, "STRING") for key in keys},
            propsTypes=props_types,
            startCategory=label_by_table.get(start_table, start_table),
            endCategory=label_by_table.get(end_table, end_table),
        )

    return GraphSchema(
        categories=list(categories.values()),
        relationships=list(relationships.values()),
    )


def _label_and_properties(table: Dict[str, Any], fallback: str) -> Tuple[str, Dict[str, str]]:
    entry = (table.get("labelAndProperties") or [{}])[0] or {}
    label = str(entry.get("label") or fallback or "")
    props_types: Dict[str, str] = {}
    for field in entry.get("properties") or []:
        name = field.get("name")
        if not name:
            continue
        props_types[str(name)] = str((field.get("dataType") or {}).get("typeKind") or "STRING")
    return label, props_types


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class BigQueryDriver(BigQueryGraphIntents, BaseDatabaseDriver):
    """Google BigQuery driver."""

    def __init__(self, project: Project):
        super().__init__(project)
        self.client = None

    # -- connection ---------------------------------------------------------

    @property
    def location(self) -> str:
        return self.config.location or DEFAULT_LOCATION

    @property
    def dataset_id(self) -> str:
        return validate_identifier(self.config.database_id, "dataset")

    def graph_namespace(self) -> str:
        """``<dataset>.<graph>``, the argument of a GQL ``GRAPH`` clause."""
        graph_name = validate_identifier(self.config.graph_name, "graph name")
        return f"{self.dataset_id}.{graph_name}"

    async def connect(self) -> None:
        if self.client is not None:
            return
        try:
            from google.cloud import bigquery
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ConnectionError(
                "google-cloud-bigquery is not installed; run `uv pip install google-cloud-bigquery`"
            ) from exc

        try:
            credentials = await self._credentials()
            self.client = bigquery.Client(
                project=self.config.project_id,
                credentials=credentials,
                location=self.location,
            )
        except Exception as exc:
            raise ConnectionError(f"Failed to connect to BigQuery: {exc}") from exc

    async def disconnect(self) -> None:
        client = self.client
        self.client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    async def _credentials(self):
        """
        The same three auth types Spanner offers, with BigQuery's scopes.

        ADC returns ``None`` rather than a credentials object: the client library
        discovers ambient credentials itself, and passing them explicitly would
        strip the project id it also discovers.
        """
        from google.oauth2 import service_account
        import google.auth
        import google.oauth2.credentials

        auth_type = self.config.auth_type

        if auth_type == AuthType.GOOGLE_ADC:
            credentials, _ = google.auth.default(scopes=BIGQUERY_SCOPES)
            return credentials

        oauth = self.config.oauth_config
        if not oauth:
            raise ValueError(f"OAuth config is required for BigQuery auth type {auth_type}")

        if auth_type == AuthType.SERVICE_ACCOUNT:
            if not (oauth.private_key and oauth.client_email):
                raise ValueError("Service account information is incomplete in oauth_config")
            return service_account.Credentials.from_service_account_info(
                {
                    "type": oauth.type or "service_account",
                    "project_id": oauth.project_id or self.config.project_id,
                    "private_key_id": oauth.private_key_id,
                    "private_key": oauth.private_key,
                    "client_email": oauth.client_email,
                    "client_id": oauth.client_id,
                    "auth_uri": oauth.auth_uri or "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": oauth.token_uri or "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": oauth.auth_provider_x509_cert_url
                    or "https://www.googleapis.com/oauth2/v1/certs",
                    "client_x509_cert_url": oauth.client_x509_cert_url,
                },
                scopes=BIGQUERY_SCOPES,
            )

        if auth_type == AuthType.OAUTH2:
            if not oauth.client_id or not oauth.client_secret:
                default_oauth = get_default_oauth_config() or {}
                if not default_oauth:
                    raise ValueError(
                        "OAuth configuration file not found and client_id/client_secret not provided"
                    )
                oauth.client_id = oauth.client_id or default_oauth.get("client_id")
                oauth.client_secret = oauth.client_secret or default_oauth.get("client_secret")

            credentials = google.oauth2.credentials.Credentials(
                token=oauth.token,
                refresh_token=oauth.refresh_token or None,
                token_uri=oauth.token_uri or "https://oauth2.googleapis.com/token",
                client_id=oauth.client_id,
                client_secret=oauth.client_secret,
                scopes=BIGQUERY_SCOPES,
            )
            if self._token_expires_soon() and oauth.refresh_token:
                credentials = await self._refresh_oauth_token(credentials)
            return credentials

        raise ValueError(f"Unsupported auth type for BigQuery: {auth_type}")

    def _token_expires_soon(self) -> bool:
        oauth = self.config.oauth_config
        if not oauth or not oauth.expires_in or not oauth.last_refreshed:
            return False
        return (time.time() - oauth.last_refreshed) >= (oauth.expires_in - 300)

    async def _refresh_oauth_token(self, credentials):
        """Refresh, then write the new token back so the next request starts fresh."""
        from google.auth.transport.requests import Request

        await asyncio.to_thread(credentials.refresh, Request())
        now = time.time()
        await ProjectService().update_project_token(
            project_id=self.project.id,
            token=credentials.token,
            last_refreshed=now,
            expires_in=getattr(credentials, "expires_in", 3600),
        )
        if self.config.oauth_config:
            self.config.oauth_config.token = credentials.token
            self.config.oauth_config.last_refreshed = now
        return credentials

    def get_token_status(self) -> Dict[str, Any]:
        """Token lifetime as the admin UI reports it, mirroring the Spanner driver."""
        oauth = self.config.oauth_config
        if not oauth:
            return {"status": "no_oauth_config"}
        now = time.time()
        status: Dict[str, Any] = {
            "has_token": bool(oauth.token),
            "has_refresh_token": bool(oauth.refresh_token),
            "expires_in": oauth.expires_in,
            "last_refreshed": oauth.last_refreshed,
            "current_time": now,
        }
        if oauth.last_refreshed and oauth.expires_in:
            elapsed = now - oauth.last_refreshed
            remaining = oauth.expires_in - elapsed
            status.update(
                {
                    "time_since_refresh": elapsed,
                    "time_until_expiry": remaining,
                    "is_expired": remaining <= 0,
                    "expires_soon": remaining <= 300,
                }
            )
        return status

    # -- query --------------------------------------------------------------

    async def _run(self, statement: str) -> Tuple[List[str], List[Any]]:
        """
        Run one statement and read it fully.

        The BigQuery client is synchronous, so it goes to a worker thread rather
        than blocking the event loop for the length of a scan.
        """
        if self.client is None:
            await self.connect()

        def execute():
            result = self.client.query(statement, location=self.location).result()
            return [field.name for field in (result.schema or [])], list(result)

        return await asyncio.to_thread(execute)

    async def test_connection(self) -> bool:
        try:
            if self.client is None:
                await self.connect()
            if self.config.graph_name:
                await self._run(f"GRAPH {self.graph_namespace()}\nRETURN '1' AS connected")
            else:
                await self._run("SELECT 1 AS connected")
            return True
        except Exception as exc:
            print(f"[BigQuery] test_connection failed: {exc}")
            return False

    async def execute_query(self, query: str, parameters: Dict[str, Any] = None) -> QueryResponse:
        start = time.time()
        try:
            if not self.config.graph_name:
                # No property graph configured: the project is a plain SQL one, so
                # the statement is run untouched rather than wrapped in a GRAPH
                # clause there is no name for.
                statement, is_graph = query.strip().rstrip(";").strip(), False
            else:
                statement, is_graph = rewrite_graph_query(query, self.graph_namespace())

            columns, rows = await self._run(statement)
            data = parse_graph_rows(rows) if is_graph else parse_table_rows(columns, rows)
            return QueryResponse(success=True, data=data, execution_time=time.time() - start)
        except Exception as exc:
            return QueryResponse(success=False, error=str(exc), execution_time=time.time() - start)

    # -- schema -------------------------------------------------------------

    async def get_graph_schema(self) -> GraphSchemaResponse:
        try:
            if not self.config.graph_name:
                return GraphSchemaResponse(success=False, error="graph_name is required")

            dataset = self.dataset_id
            graph_name = validate_identifier(self.config.graph_name, "graph name")
            _, rows = await self._run(
                "SELECT property_graph_metadata_json AS metadata\n"
                f"FROM `{dataset}.INFORMATION_SCHEMA.PROPERTY_GRAPHS`\n"
                f"WHERE property_graph_name = '{graph_name}'"
            )
            if not rows:
                return GraphSchemaResponse(
                    success=False,
                    error=f"Property graph '{graph_name}' not found in dataset '{dataset}'",
                )

            metadata = rows[0]["metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata or "{}")
            schema = map_graph_metadata(metadata or {})

            # Intent statements need each category's key, and this is the only call
            # that knows it — remember it so /expand needs no extra round-trip.
            self.remember_graph_categories(
                {
                    category.name: category.model_dump()
                    for category in schema.categories
                }
            )
            return GraphSchemaResponse(success=True, data=schema)
        except Exception as exc:
            return GraphSchemaResponse(success=False, error=str(exc))

    async def get_schema(self) -> SchemaResponse:
        try:
            dataset = self.dataset_id
            _, rows = await self._run(
                "SELECT table_name, column_name, data_type\n"
                f"FROM `{dataset}.INFORMATION_SCHEMA.COLUMNS`"
            )
            schema: Dict[str, Dict[str, str]] = {}
            for row in rows:
                schema.setdefault(row["table_name"], {})[row["column_name"]] = row["data_type"]
            return SchemaResponse(success=True, data=schema)
        except Exception as exc:
            return SchemaResponse(success=False, error=str(exc))

    async def get_sample_data(self) -> SampleDataResponse:
        try:
            dataset = self.dataset_id
            _, table_rows = await self._run(
                "SELECT table_name\n"
                f"FROM `{dataset}.INFORMATION_SCHEMA.TABLES`\n"
                "WHERE table_type = 'BASE TABLE'\n"
                f"ORDER BY table_name\nLIMIT {SAMPLE_TABLE_LIMIT}"
            )

            sample: Dict[str, Any] = {}
            for row in table_rows:
                table_name = row["table_name"]
                try:
                    validate_identifier(table_name, "table name")
                    columns, rows = await self._run(
                        f"SELECT * FROM `{dataset}.{table_name}` LIMIT {SAMPLE_ROW_LIMIT}"
                    )
                    sample[table_name] = parse_table_rows(columns, rows).data
                except Exception:
                    # A table the caller cannot read should not sink the whole sample.
                    sample[table_name] = []
            return SampleDataResponse(success=True, data=sample)
        except Exception as exc:
            return SampleDataResponse(success=False, error=str(exc))

    def get_api_info(self, project_name: str) -> Dict[str, Any]:
        base_url = f"/api/bigquery/{project_name}"
        return {
            "type": "bigquery",
            "api_urls": {
                "info": base_url,
                "query": f"{base_url}/query",
                "schema": f"{base_url}/schema",
                "graphSchema": f"{base_url}/graphSchema",
                "sampleData": f"{base_url}/sampleData",
                "capabilities": f"{base_url}/capabilities",
                "expand": f"{base_url}/expand",
                "pullCategory": f"{base_url}/pullCategory",
                "pullRelationship": f"{base_url}/pullRelationship",
                "tokenStatus": f"{base_url}/token-status",
                "test": f"{base_url}/test",
            },
            "version": "1.0",
            "features": {
                "property_graph": True,
                "gql": True,
                "sql": True,
                "schema": True,
                "graph_schema": True,
                "sample_data": True,
                "token_management": True,
            },
        }
