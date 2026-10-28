# -*- coding: utf-8 -*-
"""
Kuzu driver, over an embedded store.

The head of the embedded family; ``LadybugDriver`` subclasses this the way
``MemgraphDriver`` subclasses ``Neo4jDriver``, and for the same reason -- same
Cypher, same catalog procedures, same result shapes. What differs between the two
is data, not behaviour: the magic bytes in the file, the key casing in the results,
and which releases exist.

There is no connection here in the usual sense. A store is a file, and the
question ``connect`` actually answers is *which build of the engine can open this
file* -- read from the header, resolved to a release, downloaded if it is missing,
and run in its own process because two engine builds cannot share one. All of that
lives under ``drivers/embedded``; this module is the driver contract on top of it.

Two things are load-bearing and easy to undo by accident:

  - **Node ids are ``<Label>:<primary key>``.** ``ID(n)`` reads back fine but has no
    writable literal -- ``n._id`` is "reserved for system usage" -- so an id the
    client sends back could not be matched by identity. Node tables always have a
    primary key, so this form always exists. See ``KUZU_DIALECT``.
  - **``disconnect`` does not stop the worker.** The engine process is owned by the
    pool and outlives the driver, which the API rebuilds on every request. Stopping
    it here would put an interpreter start and an engine import in front of every
    call GraphXR makes.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseDatabaseDriver
from .embedded.engine_service import ENGINE_SERVICE, EngineResolutionError
from .embedded.kuzu_mapping import result_to_query_data, rows_hold_graph
from .embedded.kuzu_schema import (
    build_table_schema,
    load_catalog,
    load_kuzu_schema,
)
from .embedded.pool import EngineWorkerError
from .embedded.store_probe import StoreFingerprint, StoreProbeError, probe_store
from .graph_support import KuzuGraphIntents
# The row cap is Cypher's, not bolt's: a bare `MATCH (n) RETURN n` streams the whole
# store into the browser on any of these backends.
from .neo4j import MAX_QUERY_RESULTS, enforce_limit
from ..models.project import (
    GraphSchemaResponse,
    Project,
    QueryData,
    QueryResponse,
    SampleDataResponse,
    SchemaResponse,
)

#: Rows a sample takes per category.
SAMPLE_ROWS = 10


class KuzuDriver(KuzuGraphIntents, BaseDatabaseDriver):
    """Kuzu driver over a version-matched engine subprocess."""

    #: The URL segment, the label, and the PyPI package name -- all the same word.
    database_type = "kuzu"
    engine = "kuzu"

    #: What the file's magic bytes must say for this driver to accept it.
    expected_engine = "kuzu"

    def __init__(self, project: Project):
        super().__init__(project)
        self._worker = None
        self._runtime = None
        self._fingerprint: Optional[StoreFingerprint] = None

        if not self.config.read_only:
            # Shadow the class record with an instance copy. The class attribute is
            # what `tests/test_driver_intents.py` reads and what the capability
            # record means in general; this project simply happens to be writable.
            capabilities = type(self).graph_capabilities.model_copy(deep=True)
            capabilities.write = True
            self.graph_capabilities = capabilities

    # -- the store ----------------------------------------------------------

    @property
    def store_path(self) -> str:
        path = (self.config.database_path or "").strip()
        if not path:
            raise ValueError(
                f"a {self.database_type} project needs database_path: the store file "
                f"or the directory holding it"
            )
        return str(Path(path).expanduser())

    @property
    def read_only(self) -> bool:
        return bool(self.config.read_only)

    def _fingerprint_store(self) -> StoreFingerprint:
        """
        Read the header, and let it decide the engine even when it is the other family.

        The two families are one codebase with two names: same Cypher, same catalog
        procedures, same result shapes, and token-identical dialects. So a Kuzu
        project pointed at a Ladybug store is not an error to refuse -- it is a
        store the proxy can serve perfectly well, using Ladybug's package and
        Ladybug's release series. The project type picks the URL and the label; the
        file picks the engine.

        Reading the header first is still what makes that possible. Handing a store
        to the wrong build fails, and fails obscurely: Kuzu 0.10 on a 0.11 file
        raises a ``UnicodeDecodeError`` from inside its catalog reader.
        """
        fingerprint = probe_store(self.store_path)
        if fingerprint.engine != self.expected_engine:
            print(
                f"[{self.database_type}] {fingerprint.header_path} is a "
                f"{fingerprint.describe()}; serving it with the {fingerprint.engine} "
                f"engine instead of {self.expected_engine}"
            )
        return fingerprint

    # -- connection ---------------------------------------------------------

    async def connect(self) -> None:
        if self._worker is not None and self._worker.alive:
            return
        try:
            self._fingerprint = self._fingerprint_store()
            # A pin was written for the family the user picked. If the file turns out
            # to be the other one, the pin names a release series that does not exist
            # there -- "0.11" is a Kuzu line, and there is no Ladybug 0.11 -- so it is
            # dropped rather than turned into a failed install.
            pin = (
                self.config.engine_version
                if self._fingerprint.engine == self.expected_engine
                else None
            )
            self._runtime, self._worker = await ENGINE_SERVICE.open_store(
                self._fingerprint, read_only=self.read_only, pin=pin
            )
        except (StoreProbeError, EngineResolutionError, EngineWorkerError) as exc:
            raise ConnectionError(str(exc)) from exc

    async def disconnect(self) -> None:
        """
        Let go of the worker without stopping it.

        The pool owns the process and reuses it across requests; the driver is
        rebuilt per request and must not take the engine down with it.
        """
        self._worker = None

    async def test_connection(self) -> bool:
        try:
            await self.connect()
            await self._run("RETURN 1 AS ok")
            return True
        except Exception as exc:
            print(f"[{self.database_type}] test_connection failed: {exc}")
            return False

    # -- query --------------------------------------------------------------

    def _key_by_label(self) -> Dict[str, str]:
        """Category -> its primary key property, for building node ids."""
        keys: Dict[str, str] = {}
        for name, category in self.graph_categories().items():
            declared = (category or {}).get("keys") or []
            if declared:
                keys[str(name)] = str(declared[0])
        return keys

    async def _raw(self, statement: str, parameters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """One statement, as the worker's raw result sets."""
        if self._worker is None or not self._worker.alive:
            await self.connect()
        payload: Dict[str, Any] = {"statement": statement, "max_rows": MAX_QUERY_RESULTS}
        if parameters:
            payload["parameters"] = parameters
        response = await self._worker.request("query", **payload)
        return list(response.get("results") or [])

    async def _run(self, statement: str, parameters: Optional[Dict[str, Any]] = None) -> QueryData:
        """
        One statement, as the contract's result shape.

        A statement list yields one result set per statement; the last one is the
        answer, which is what a client that sent ``a; b`` means by "the result".
        """
        results = await self._raw(statement, parameters)
        if not results:
            return QueryData(type="TABLE", data=[[]])

        last = results[-1]
        if rows_hold_graph(last.get("rows") or []):
            # Node ids are built from each category's primary key, so the schema has
            # to be in hand before a graph result can be mapped. It is cached, and
            # only a statement that actually returned entities pays for it.
            await self._ensure_graph_categories_loaded()
        return result_to_query_data(last, self._key_by_label())

    async def _ensure_graph_categories_loaded(self) -> None:
        if self.graph_categories():
            return
        response = await self.get_graph_schema_cached()
        if response.success and response.data:
            self.remember_graph_categories(
                {category.name: category.model_dump() for category in response.data.categories}
            )

    async def execute_query(self, query: str, parameters: Dict[str, Any] = None) -> QueryResponse:
        start = time.time()
        try:
            data = await self._run(enforce_limit(query), parameters)
            return QueryResponse(success=True, data=data, execution_time=time.time() - start)
        except Exception as exc:
            return QueryResponse(success=False, error=str(exc), execution_time=time.time() - start)

    async def _probe(self, statement: str) -> Optional[QueryData]:
        """
        One catalog statement.

        A statement the engine *rejects* is an answer, not a fault: an older build
        may not have ``show_connection``, and a store whose relationship tables
        cannot be introspected still yields usable categories. A worker that has
        died is a different thing and is allowed through, so an engine that fell
        over is not reported as a graph with nothing in it.
        """
        try:
            return await self._run(statement)
        except EngineWorkerError as exc:
            if not (self._worker is None or self._worker.alive):
                raise
            print(f"[{self.database_type}] probe rejected: {statement} -> {exc}")
            return None

    # -- schema -------------------------------------------------------------

    async def get_graph_schema(self) -> GraphSchemaResponse:
        try:
            await self.connect()
            schema = await load_kuzu_schema(self._probe)
            self.remember_graph_categories(
                {category.name: category.model_dump() for category in schema.categories}
            )
            return GraphSchemaResponse(success=True, data=schema)
        except Exception as exc:
            return GraphSchemaResponse(success=False, error=str(exc))

    async def get_schema(self) -> SchemaResponse:
        """
        The relational view, which these engines can genuinely answer.

        Unlike the bolt family, a Kuzu table is declared, so ``/schema`` reports real
        column types instead of refusing.
        """
        try:
            await self.connect()
            catalog = await load_catalog(self._probe)
            return SchemaResponse(
                success=True, data=build_table_schema(catalog["tables"], catalog["table_info"])
            )
        except Exception as exc:
            return SchemaResponse(success=False, error=str(exc))

    async def get_sample_data(self) -> SampleDataResponse:
        try:
            await self.connect()
            schema_response = await self.get_graph_schema_cached()
            if not schema_response.success:
                return SampleDataResponse(success=False, error=schema_response.error)

            sample: Dict[str, Any] = {}
            for category in schema_response.data.categories:
                label = self.graph_dialect.quote_identifier(category.name)
                result = await self._probe(
                    f"MATCH (n:{label}) RETURN n SKIP 0 LIMIT {SAMPLE_ROWS}"
                )
                sample[category.name] = self._sample_rows(result)
            return SampleDataResponse(success=True, data=sample)
        except Exception as exc:
            return SampleDataResponse(success=False, error=str(exc))

    @staticmethod
    def _sample_rows(result: Optional[QueryData]) -> List[Dict[str, Any]]:
        """A sample statement's nodes, as plain property dicts."""
        if result is None or result.type != "GRAPH" or result.data is None:
            return []
        return [dict(node.properties) for node in getattr(result.data, "nodes", [])]

    # -- api ----------------------------------------------------------------

    @property
    def engine_in_use(self) -> str:
        """
        The engine actually serving this project.

        Usually the project's own type; the other family when the file said so. The
        capability record keeps reporting the project type, because that is what the
        route is and the two records are identical in everything but the name -- but
        the substitution is never silent, and this is where it is stated.
        """
        if self._runtime is not None:
            return self._runtime.engine
        if self._fingerprint is not None:
            return self._fingerprint.engine
        return self.engine

    def get_api_info(self, project_name: str) -> Dict[str, Any]:
        base_url = f"/api/{self.database_type}/{project_name}"
        return {
            "type": self.database_type,
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
                "test": f"{base_url}/test",
            },
            "version": "1.0",
            "features": {
                "property_graph": True,
                "cypher": True,
                "graph_schema": True,
                "table_schema": True,
                "sample_data": True,
                "embedded": True,
                "fulltext_search": False,
                "multi_database": False,
                "read_only": self.read_only,
                # Which family and which release is behind this project, which is not
                # always the type in the URL: a Kuzu project holding a Ladybug store
                # is served by Ladybug.
                "engine": self.engine_in_use,
                "engine_version": self._runtime.version if self._runtime else None,
            },
        }
