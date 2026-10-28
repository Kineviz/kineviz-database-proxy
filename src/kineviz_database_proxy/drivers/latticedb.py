# -*- coding: utf-8 -*-
"""
LatticeDB driver, over an embedded store.

The third embedded engine, and the first that is not a Kuzu. Ladybug subclasses
``KuzuDriver`` because the two really are one codebase with two names; LatticeDB
shares nothing with either but the word MATCH, so it is a driver of its own that
happens to sit on the same machinery underneath -- the header probe, the version
map, the per-release install, and the worker process.

Three things here are load-bearing:

  - **A store of another family is refused, not adopted.** ``KuzuDriver`` serves a
    Ladybug file when it finds one, because either engine understands it and the
    dialects are token-identical. That reasoning does not reach across to here:
    LatticeDB cannot open a Kuzu file, and even if it could, the statements this
    driver builds would mean nothing to Kuzu. So the header decides whether to
    proceed at all, and says which project type the file actually wants.
  - **Node ids are the engine's own integers.** ``id(n)`` matches as well as reads
    -- ``WHERE id(n) IN [1,2]`` is accepted -- so unlike the Kuzu family there is
    no ``<Label>:<key>`` to construct and no schema to load before a result can be
    mapped. That is why ``_run`` has no equivalent of Kuzu's
    ``_ensure_graph_categories_loaded``.
  - **``disconnect`` does not stop the worker.** The pool owns the process and
    reuses it across requests; the API rebuilds the driver every time. Stopping it
    here would put an interpreter start and an engine load in front of every call.

One consequence of having no node value type is worth stating plainly: a
hand-written statement gets a **table** unless it projects entities the way the
dialect does. ``MATCH (n) RETURN n`` cannot come back as a graph from this engine
under any mapping, because what it returns is the number 1. The intent routes --
``/expand``, ``/pullCategory``, ``/pullRelationship`` -- always project properly
and always answer a graph.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import BaseDatabaseDriver
from .embedded.engine_service import ENGINE_SERVICE, EngineResolutionError
from .embedded.lattice_mapping import result_to_query_data
from .embedded.lattice_query import explain, prepare_statement
from .embedded.lattice_schema import (
    SAMPLE_ROWS,
    load_lattice_schema,
    node_sample_statement,
)
from .embedded.pool import EngineWorkerError
from .embedded.store_probe import (
    FAMILY_NAMES,
    StoreFingerprint,
    StoreProbeError,
    probe_store,
)
from .graph_support import LatticeDbGraphIntents
# The row cap is Cypher's, not bolt's: a bare `MATCH (n) RETURN id(n)` streams the
# whole store into the browser here just as readily.
from .neo4j import MAX_QUERY_RESULTS, enforce_limit
from ..models.project import (
    GraphSchemaResponse,
    Project,
    QueryData,
    QueryResponse,
    SampleDataResponse,
    SchemaResponse,
)

#: Nodes a ``/sampleData`` call reads per category.
SAMPLE_NODES = 10


class LatticeDbDriver(LatticeDbGraphIntents, BaseDatabaseDriver):
    """LatticeDB driver over a version-matched engine subprocess."""

    #: The URL segment, the label, and the PyPI package name -- all the same word.
    database_type = "latticedb"
    engine = "latticedb"

    #: What the file's magic bytes must say for this driver to accept it.
    expected_engine = "latticedb"

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
                f"a {self.database_type} project needs database_path: the store file"
            )
        return str(Path(path).expanduser())

    @property
    def read_only(self) -> bool:
        return bool(self.config.read_only)

    def _fingerprint_store(self) -> StoreFingerprint:
        """
        Read the header, and refuse a store this engine cannot open.

        The Kuzu driver substitutes across its own family here, because a Kuzu
        project holding a Ladybug store is still a store it can serve: same Cypher,
        same result shapes, token-identical dialects. None of that is true across
        this boundary. LatticeDB refuses a Kuzu file outright, and the statements
        this driver builds -- ``labels(n)``, ``properties(n)``, ``type(r)`` -- are
        not Kuzu's. Naming the project type the file actually wants is the only
        useful thing to do with it.
        """
        fingerprint = probe_store(self.store_path)
        if fingerprint.engine != self.expected_engine:
            family = FAMILY_NAMES.get(fingerprint.engine, fingerprint.engine)
            raise StoreProbeError(
                f"{fingerprint.header_path} is a {fingerprint.describe()}, not a "
                f"{FAMILY_NAMES[self.expected_engine]} store. {family} and "
                f"{FAMILY_NAMES[self.expected_engine]} are separate engines with "
                f"separate formats and separate Cypher; serve this file from a "
                f"'{fingerprint.engine}' project instead."
            )
        return fingerprint

    # -- connection ---------------------------------------------------------

    async def connect(self) -> None:
        if self._worker is not None and self._worker.alive:
            return
        try:
            self._fingerprint = self._fingerprint_store()
            self._runtime, self._worker = await ENGINE_SERVICE.open_store(
                self._fingerprint,
                read_only=self.read_only,
                pin=self.config.engine_version,
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
            # Not `RETURN 1`: LatticeDB cannot plan a RETURN with nothing to read
            # from, and answers "could not create execution plan". UNWIND gives it
            # something, and touches no data.
            await self._raw("UNWIND [1] AS ok RETURN ok")
            return True
        except Exception as exc:
            print(f"[{self.database_type}] test_connection failed: {exc}")
            return False

    # -- query --------------------------------------------------------------

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

        No schema is loaded first, unlike the Kuzu family: nothing in a LatticeDB
        result has to be joined to a category before it can be given an id.
        """
        results = await self._raw(statement, parameters)
        if not results:
            return QueryData(type="TABLE", data=[[]])
        return result_to_query_data(results[-1])

    async def _read_rows(self, statement: str) -> Optional[List[Dict[str, Any]]]:
        """
        One statement, as row dicts, or None if the engine refused it.

        The schema loader works in named columns rather than positions, and a
        refused statement is an answer there -- a store with no relationships still
        has categories. A worker that has *died* is a different thing and is allowed
        through, so an engine that fell over is not reported as an empty graph.
        """
        try:
            results = await self._raw(statement)
        except EngineWorkerError as exc:
            if not (self._worker is None or self._worker.alive):
                raise
            print(f"[{self.database_type}] probe rejected: {statement} -> {exc}")
            return None

        if not results:
            return []
        last = results[-1]
        columns = [str(column) for column in last.get("columns") or []]
        return [dict(zip(columns, row)) for row in last.get("rows") or []]

    async def execute_query(self, query: str, parameters: Dict[str, Any] = None) -> QueryResponse:
        """
        A statement the caller wrote, run as close to what they meant as possible.

        ``prepare_statement`` is why this is not a straight pass-through. Three
        things a Cypher client may reasonably write have no spelling here: a
        backtick, a ``RETURN *``, and a bare variable standing for an entity. The
        first is an invalid token, the second stops at the parser, and the third is
        the dangerous one -- it parses and answers integers. Each is turned into
        something this engine has, and anything unreadable is passed through
        untouched; ``explain`` then puts the reason back on the error.
        """
        start = time.time()
        try:
            data = await self._run(enforce_limit(prepare_statement(query)), parameters)
            return QueryResponse(success=True, data=data, execution_time=time.time() - start)
        except Exception as exc:
            return QueryResponse(
                success=False,
                error=explain(query, str(exc)),
                execution_time=time.time() - start,
            )

    # -- schema -------------------------------------------------------------

    async def get_graph_schema(self) -> GraphSchemaResponse:
        try:
            await self.connect()
            schema = await load_lattice_schema(self._read_rows)
            self.remember_graph_categories(
                {category.name: category.model_dump() for category in schema.categories}
            )
            return GraphSchemaResponse(success=True, data=schema)
        except Exception as exc:
            return GraphSchemaResponse(success=False, error=str(exc))

    async def get_schema(self) -> SchemaResponse:
        """
        The graph *is* the schema here, as it is for the bolt family.

        Kuzu can answer this because a Kuzu table is declared. A LatticeDB node is
        created without declaring a label or a property, so there is no relational
        layout to report -- ``/graphSchema`` is the route that answers, and it
        infers rather than reads.
        """
        return SchemaResponse(
            success=False,
            error=(
                f"Table schema is not available for {self.database_type}: the store "
                f"declares no tables or columns. Use /graphSchema."
            ),
        )

    async def get_sample_data(self) -> SampleDataResponse:
        try:
            await self.connect()
            schema_response = await self.get_graph_schema_cached()
            if not schema_response.success:
                return SampleDataResponse(success=False, error=schema_response.error)

            sample: Dict[str, Any] = {}
            for category in schema_response.data.categories:
                rows = await self._read_rows(
                    node_sample_statement(category.name, SAMPLE_NODES)
                )
                sample[category.name] = [
                    dict(row.get("properties(n)") or {})
                    for row in rows or ()
                    if isinstance(row.get("properties(n)"), dict)
                ]
            return SampleDataResponse(success=True, data=sample)
        except Exception as exc:
            return SampleDataResponse(success=False, error=str(exc))

    # -- api ----------------------------------------------------------------

    @property
    def engine_in_use(self) -> str:
        """
        The engine serving this project, which is always this one.

        Kept for symmetry with the Kuzu driver, where it is a real question because
        that family substitutes across itself. Here the header either says
        ``latticedb`` or the project never connected.
        """
        if self._runtime is not None:
            return self._runtime.engine
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
                # Inferred from the data, so there is no declared table layout to
                # report and /schema says so rather than guessing one.
                "table_schema": False,
                "sample_data": True,
                "embedded": True,
                # The engine has BM25 search, but its index is filled per node by an
                # explicit call, so the proxy does not offer it. See the capability
                # record in graph_support.py.
                "fulltext_search": False,
                "multi_database": False,
                "read_only": self.read_only,
                "engine": self.engine_in_use,
                "engine_version": self._runtime.version if self._runtime else None,
            },
        }
