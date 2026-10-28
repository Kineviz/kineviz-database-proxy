# -*- coding: utf-8 -*-
"""
Neo4j driver, over bolt.

The head of the bolt family: Memgraph subclasses this rather than reimplementing
it, because the two speak the same protocol and the same Cypher. What is here is
the connection, the query, and full-text search; the statements come from
``NEO4J_DIALECT``, the record mapping from ``bolt_mapping`` and the schema probe
from ``bolt_schema``.

Two notes on identity, both load-bearing:

  - Ids are bolt internal ids (``ID(n)``), not ``elementId(n)``. The latter only
    exists from Neo4j 5 and has no Memgraph equivalent, and the ids the proxy hands
    the client have to be the ones its own predicate can match again.
  - A result is read as a graph when it *contains* graph entities. The GQL backends
    have to guess that from the statement's syntax; Cypher hands back typed objects,
    so there is nothing to guess.
"""

from __future__ import annotations

import re
import socket
import time
from typing import Any, Dict, Optional, Tuple

try:  # pragma: no cover - exercised by whichever install is present
    from neo4j.addressing import ResolvedAddress
    from neo4j.exceptions import AuthError, Neo4jError
except ImportError:  # the bolt driver is optional; `connect` reports its absence
    ResolvedAddress = None

    class Neo4jError(Exception):
        """Stand-in so ``except Neo4jError`` is simply inert without the package."""

    class AuthError(Neo4jError):
        """Stand-in; see above."""


from .base import BaseDatabaseDriver
from .bolt_mapping import records_hold_graph, records_to_graph, records_to_table
from .bolt_schema import load_neo4j_schema
from .dialect import backtick
from .graph_support import Neo4jGraphIntents
from ..contract.intents import FulltextSearchRequest
from ..models.project import (
    GraphSchemaResponse,
    Project,
    QueryData,
    QueryResponse,
    SampleDataResponse,
    SchemaResponse,
)

#: Rows a statement is capped at when it does not cap itself. Mirrors
#: ``AppConfig.maxQueryResults`` in graphxr-dev.
MAX_QUERY_RESULTS = 20000

DEFAULT_BOLT_PORT = 7687

#: The index a search falls back to when the request does not name one. Matches
#: the name GraphXR creates its own full-text index under.
DEFAULT_FULLTEXT_INDEX = "graphxr_fulltext_index"

#: Seconds to spend opening a bolt connection, and then acquiring one from the
#: pool. The driver's own defaults are 30s and 60s, which turn a host that
#: silently drops packets -- a firewall, a stopped container, a VPN that is down
#: -- into a request that hangs for a minute before answering. These are sized
#: for a form the user is waiting in front of: fail while they are still looking
#: at it. They bound *connecting only*; a long-running query is unaffected.
CONNECTION_TIMEOUT_SECONDS = 10.0
CONNECTION_ACQUISITION_TIMEOUT_SECONDS = 15.0
MAX_TRANSACTION_RETRY_SECONDS = 15.0

_RETURN_KW_RE = re.compile(r"\bRETURN\b", re.IGNORECASE)
_LIMIT_KW_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_HAS_SCHEME_RE = re.compile(r"^(bolt|neo4j)(\+s|\+ssc)?://", re.IGNORECASE)
_WORD_KEYWORD_RE = re.compile(r"^[a-z0-9_]+$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def build_bolt_uri(host: Optional[str], port: Optional[int], use_tls: bool) -> str:
    """
    The bolt URI to dial.

    A host that already names a scheme is used verbatim — that is how an Aura
    connection string (``neo4j+s://xxx.databases.neo4j.io``) is given, and it
    carries its own routing and TLS decisions. Otherwise ``bolt://`` is built, or
    ``bolt+ssc://`` when TLS is asked for: ``+ssc`` rather than ``+s`` so a
    self-signed certificate still connects, which is the common case for a
    customer-hosted server behind this proxy.
    """
    text = str(host or "").strip()
    if not text:
        raise ValueError("host is required for a bolt connection")
    if _HAS_SCHEME_RE.match(text):
        return text.rstrip("/")
    scheme = "bolt+ssc" if use_tls else "bolt"
    if ":" in text:  # host already carries its port
        return f"{scheme}://{text}"
    return f"{scheme}://{text}:{port or DEFAULT_BOLT_PORT}"


def prefer_ipv4(address):
    """
    The addresses to try for one host, IPv4 before IPv6.

    On Windows ``localhost`` resolves to ``::1`` first, while a container started
    the way Docker Desktop starts one publishes on ``127.0.0.1`` and has nothing
    listening on the IPv6 side. Connecting there does not fail -- it *hangs* --
    so every connection the pool opened paid a full connect timeout before
    falling back. Measured against a server that answers in 0.12s: a project
    configured with host ``localhost`` took **20s** for one ``/graphSchema``,
    two stalls of ten seconds, one per pooled connection.

    Trying IPv4 first is safe in the other direction. A host reachable only over
    IPv6 *refuses* the IPv4 attempt rather than dropping it, which costs a round
    trip rather than a timeout.

    Each address keeps the hostname it was resolved from, so TLS still verifies
    against the name the user configured and not the address behind it.
    """
    if ResolvedAddress is None:  # pragma: no cover - depends on the install
        return [address]
    # The attribute is public in neo4j 6 and private in 5; `host` is the fallback
    # for anything else. Getting it wrong only costs the hostname TLS checks
    # against, so it is worth reading defensively rather than pinning a version.
    host_name = (
        getattr(address, "host_name", None)
        or getattr(address, "_host_name", None)
        or address.host
    )
    try:
        infos = socket.getaddrinfo(address.host, address.port, type=socket.SOCK_STREAM)
    except OSError:
        # Let the driver's own resolution report the failure.
        return [address]

    ordered = sorted(infos, key=lambda info: 0 if info[0] == socket.AF_INET else 1)
    resolved = []
    try:
        for family, _, _, _, sockaddr in ordered:
            if family == socket.AF_INET6 and len(sockaddr) > 3 and sockaddr[3]:
                # A scoped IPv6 address, which the driver skips too.
                continue
            candidate = ResolvedAddress(sockaddr, host_name=host_name)
            if candidate not in resolved:
                resolved.append(candidate)
    except TypeError:  # pragma: no cover - a driver whose signature moved again
        return [address]
    return resolved or [address]


def enforce_limit(statement: str, max_results: int = MAX_QUERY_RESULTS) -> str:
    """
    Cap a returning statement that did not cap itself.

    Without this a bare ``MATCH (n) RETURN n`` streams the whole store into the
    browser. Mirrors ``modules/graphdb/core/enforceLimit.ts``.
    """
    cleaned = statement.strip().rstrip(";").strip()
    if _RETURN_KW_RE.search(cleaned) and not _LIMIT_KW_RE.search(cleaned):
        return f"{cleaned} LIMIT {max_results}"
    return cleaned


def build_search_pattern(keyword: str) -> str:
    """
    A user's keyword as a Lucene query.

    Four cases, all of them fixes rather than style:

      - a single bare word becomes a regex, so it matches as a substring;
      - pure CJK is quoted, because Neo4j tokenizes every character separately and
        an unquoted phrase would match any one of them;
      - anything containing a space is quoted, because unquoted spaces mean OR;
      - otherwise only the apostrophe is escaped.
    """
    trimmed = str(keyword).strip()
    if _WORD_KEYWORD_RE.match(trimmed):
        return f"/(.+)?{trimmed}(.+)?/"
    if trimmed and all(ord(char) >= 0x4E00 for char in trimmed):
        return f'"{trimmed}"'
    if re.search(r"\s", trimmed):
        return '"' + re.sub(r"(\\+)?'", r"\\'", trimmed, count=1) + '"'
    return re.sub(r"(\\+)?'", r"\\'", trimmed, count=1)


def build_apoc_search_pattern(keyword: str) -> str:
    """APOC's index search wants a glob, with the punctuation Lucene reserves wildcarded."""
    reserved = r"[\\'\"()\[\]{},=:*.!^\-|#]"
    return f"**{re.sub(reserved, '*', str(keyword))}**"


def build_search_statement(request: FulltextSearchRequest) -> Tuple[str, Dict[str, Any]]:
    """The search statement and its parameters, for a native or an APOC index."""
    index_name = request.indexName or DEFAULT_FULLTEXT_INDEX
    if request.useApoc:
        head = f"CALL apoc.index.search($indexName, $searchQuery, {MAX_QUERY_RESULTS}) YIELD node "
        params: Dict[str, Any] = {
            "indexName": index_name,
            "searchQuery": build_apoc_search_pattern(request.keyword),
        }
    else:
        head = "CALL db.index.fulltext.queryNodes($indexName, $searchQuery) YIELD node "
        params = {"indexName": index_name, "searchQuery": build_search_pattern(request.keyword)}

    if request.countOnly:
        return (
            f"{head}WITH node LIMIT {MAX_QUERY_RESULTS} "
            "RETURN labels(node) as label, count(node) as count ORDER BY label",
            params,
        )

    categories = request.categories or []
    label_filter = (
        "WHERE labels(node) IN [['" + "'],['".join(categories) + "']] " if categories else ""
    )
    return (
        f"{head}{label_filter}RETURN node SKIP {int(request.skip)} LIMIT {int(request.limit)}",
        params,
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class Neo4jDriver(Neo4jGraphIntents, BaseDatabaseDriver):
    """Neo4j driver over the official async bolt driver."""

    #: What the schema probe calls; Memgraph overrides it with its own.
    schema_loader = staticmethod(load_neo4j_schema)

    #: The URL segment and the label this driver answers under.
    database_type = "neo4j"

    def __init__(self, project: Project):
        super().__init__(project)
        self._driver = None

    # -- connection ---------------------------------------------------------

    @property
    def uri(self) -> str:
        return build_bolt_uri(self.config.host, self.config.port, self.config.use_tls)

    @property
    def database(self) -> Optional[str]:
        """
        The database to open, or None for the server's default.

        Memgraph has no multi-database concept and rejects the parameter, so it
        overrides this to None.
        """
        return self.config.database_id or None

    async def connect(self) -> None:
        if self._driver is not None:
            return
        try:
            from neo4j import AsyncGraphDatabase
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ConnectionError(
                "the neo4j package is not installed; run `uv pip install neo4j`"
            ) from exc

        # Both empty means anonymous access — a server started with auth disabled
        # rejects credentials rather than ignoring them.
        auth = (
            (self.config.username, self.config.password)
            if (self.config.username or self.config.password)
            else None
        )
        try:
            self._driver = AsyncGraphDatabase.driver(
                self.uri,
                auth=auth,
                connection_timeout=CONNECTION_TIMEOUT_SECONDS,
                connection_acquisition_timeout=CONNECTION_ACQUISITION_TIMEOUT_SECONDS,
                max_transaction_retry_time=MAX_TRANSACTION_RETRY_SECONDS,
                resolver=prefer_ipv4,
            )
        except Exception as exc:
            raise ConnectionError(f"Failed to connect to {self.database_type}: {exc}") from exc

    async def disconnect(self) -> None:
        driver = self._driver
        self._driver = None
        if driver is not None:
            try:
                await driver.close()
            except Exception:
                pass

    async def test_connection(self) -> bool:
        try:
            if self._driver is None:
                await self.connect()
            await self._driver.verify_connectivity()
            await self._run("RETURN 1 AS ok")
            return True
        except Exception as exc:
            print(f"[{self.database_type}] test_connection failed: {exc}")
            return False

    # -- query --------------------------------------------------------------

    async def _run(self, statement: str, parameters: Optional[Dict[str, Any]] = None) -> QueryData:
        """Run one statement and read its records into the contract's result shape."""
        if self._driver is None:
            await self.connect()

        session_args = {"database": self.database} if self.database else {}
        async with self._driver.session(**session_args) as session:
            result = await session.run(statement, parameters or {})
            keys = list(result.keys())
            records = [record async for record in result]

        if records_hold_graph(records):
            return records_to_graph(records)
        return records_to_table(records, keys)

    async def execute_query(self, query: str, parameters: Dict[str, Any] = None) -> QueryResponse:
        start = time.time()
        try:
            data = await self._run(enforce_limit(query), parameters)
            return QueryResponse(success=True, data=data, execution_time=time.time() - start)
        except Exception as exc:
            return QueryResponse(success=False, error=str(exc), execution_time=time.time() - start)

    async def _probe(self, statement: str) -> Optional[QueryData]:
        """
        One probe statement.

        A statement the server *rejects* is an answer here, not a fault: ``SHOW
        SCHEMA INFO`` throws on a Memgraph started without
        ``--schema-info-enabled``, and a store that refuses the property sample
        still yields usable categories.

        A server that cannot be reached is a different thing, and is allowed
        through. Swallowing it too turned an unreachable database into a
        *successful* response carrying an empty schema -- which the client cannot
        tell apart from a graph that genuinely has nothing in it, so the user is
        shown an empty panel instead of the connection error.
        """
        try:
            return await self._run(statement)
        except Neo4jError as exc:
            # Bad credentials are the server answering, but they are not this
            # statement's fault and every later probe will fail the same way.
            if isinstance(exc, AuthError):
                raise
            return None

    # -- schema -------------------------------------------------------------

    async def get_graph_schema(self) -> GraphSchemaResponse:
        try:
            if self._driver is None:
                await self.connect()
            schema = await type(self).schema_loader(self._probe)
            self.remember_graph_categories(
                {category.name: category.model_dump() for category in schema.categories}
            )
            return GraphSchemaResponse(success=True, data=schema)
        except Exception as exc:
            return GraphSchemaResponse(success=False, error=str(exc))

    async def get_schema(self) -> SchemaResponse:
        """
        The graph *is* the schema here.

        ``/schema`` reports a relational table layout, which a bolt store does not
        have; ``/graphSchema`` is the one that answers for these backends.
        """
        return SchemaResponse(
            success=False,
            error=f"Table schema is not available for {self.database_type}; use /graphSchema",
        )

    async def get_sample_data(self) -> SampleDataResponse:
        """A handful of nodes per label, which is the closest thing to a table sample."""
        try:
            schema_response = await self.get_graph_schema_cached()
            if not schema_response.success:
                return SampleDataResponse(success=False, error=schema_response.error)

            sample: Dict[str, Any] = {}
            for category in schema_response.data.categories:
                result = await self._probe(
                    f"MATCH (n:{backtick(category.name)}) RETURN properties(n) AS props LIMIT 10"
                )
                rows = result.data[1:] if result is not None and isinstance(result.data, list) else []
                sample[category.name] = [row[0] for row in rows if row]
            return SampleDataResponse(success=True, data=sample)
        except Exception as exc:
            return SampleDataResponse(success=False, error=str(exc))

    # -- intents ------------------------------------------------------------

    async def fulltext_search(self, request: FulltextSearchRequest) -> QueryData:
        """
        Query an existing full-text index.

        The proxy never creates or drops one: index management is not part of the
        proxy contract, which is why ``fulltextSearch.manageIndex`` is declared
        false.
        """
        statement, parameters = build_search_statement(request)
        return await self._run(statement, parameters)

    def get_api_info(self, project_name: str) -> Dict[str, Any]:
        base_url = f"/api/{self.database_type}/{project_name}"
        api_urls = {
            "info": base_url,
            "query": f"{base_url}/query",
            "graphSchema": f"{base_url}/graphSchema",
            "sampleData": f"{base_url}/sampleData",
            "capabilities": f"{base_url}/capabilities",
            "expand": f"{base_url}/expand",
            "pullCategory": f"{base_url}/pullCategory",
            "pullRelationship": f"{base_url}/pullRelationship",
            "test": f"{base_url}/test",
        }
        if self.graph_capabilities.fulltextSearch.supported:
            api_urls["search"] = f"{base_url}/search"
        return {
            "type": self.database_type,
            "api_urls": api_urls,
            "version": "1.0",
            "features": {
                "property_graph": True,
                "cypher": True,
                "graph_schema": True,
                "sample_data": True,
                "fulltext_search": self.graph_capabilities.fulltextSearch.supported,
                "multi_database": self.graph_capabilities.multiDatabase,
            },
        }
