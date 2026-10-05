# -*- coding: utf-8 -*-
"""
``RelationalDriver`` — the contract surface of a project whose database is tables.

One class answers all of ``BaseDatabaseDriver`` for mssql, postgresql and mysql: the
three differ in SQL spelling, which ``engines/*.py`` owns, and in nothing else, so this
reads its engine through ``EngineSpelling`` rather than branching on a name.

Three duties ``query.py`` deliberately does **not** own, and this module therefore does:

* **probe recognition before the guard.** ``prepare`` refuses ``CALL`` by design — an
  engine procedure is not a read this proxy plans — so a statement that is really asking
  "what is your schema?" has to be read here, first, or the refusal is a dead end.
* **the fan-out's total, and what the compiled statements mean.** The ceiling is the
  project's ``max_branches`` handed to the planner, which refuses with a message naming the
  pattern that blew it; a chunk of a branch is not capped, because it is part of one answer.
  Then a ``graph`` plan becomes ``QueryData(type="GRAPH")`` merged by id across the statements
  that ran, a ``scalar`` or ``aggregate`` plan the contract's 2D array, and ``summary`` says
  how many ran and whether the limit cut the total.
* **the key's declared type.** A mapping records the columns around a key and not the key
  itself, and the key's type is the one thing that cannot be guessed: it decides whether the
  seed predicate quotes a numeric id, and what type the parameter is bound as. ``convert.py``
  and ``compile.py`` get their own from the mapping's declared types; this reads the schema for
  the ones nobody named.

Two things it owns because nothing else can: every ``Category`` carries a non-empty
``keys`` — ``schema_surface.py`` explains what an empty one does to ``/expand`` — and a
refusal or an engine error comes back as ``QueryResponse(success=false, error=...)``,
because the intent routes in ``api/database.py`` have no exception wrapper and anything
that escapes here becomes a 500 in the middle of someone's canvas.

The Cypher-to-SQL stages are named only in ``sql_pipeline.py``. Nothing here imports a
DBAPI, and ``get_api_info`` opens no connection: a pool is not somewhere to spend a page
load.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ...models.graph_mapping import GraphMapping
from ...models.project import (
    GraphData,
    GraphSchemaResponse,
    Project,
    QueryData,
    QueryResponse,
    SampleDataResponse,
    SchemaResponse,
)
from ..base import BaseDatabaseDriver
from ..intents import merge_graph_results
from . import query as query_layer
from .catalog import Catalog, propose
from .engine import engines_for
from .mapping import EffectiveMapping, from_document
from .pool import DbapiMissing, RelationalAccess
from .reads import (
    DIRECTION_DECLARED,
    DIRECTION_FLIPPED,
    PREVIEW_ROWS_MAX,
    RelationalReads,
    plain_cell,
    verdict_of,
)
from .schema_surface import UNKNOWN_TYPE, RelationalSchemaSurface, problems_text
from .schemas import NodeSchema
from .sql_pipeline import PIPELINE, PipelineMissing

#: Rows sampled per label. A sample is a look at the shape of a table, not an export.
SAMPLE_ROWS = 10

#: ``CALL <name>(...)``, recognised before the guard runs, because the guard refuses CALL.
_CALL_HEAD = re.compile(
    r"^\s*CALL\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*(?:\(([^)]*)\))?",
    re.IGNORECASE,
)

#: The probe names this driver answers itself. Anything else a client puts behind ``CALL``
#: stays refused, with the route that does have the answer named in its place.
_SCHEMA_PROBES = ("schema", "graphschema", "graphschemavisualization", "visualization")


class RelationalDriver(RelationalReads, RelationalSchemaSurface, BaseDatabaseDriver):
    """
    The whole ``BaseDatabaseDriver`` surface for a relational project.

    A subclass sets two words — ``database_type`` and ``engine_name`` — and mixes in one
    engine's capability record and dialect. Everything else is here, including the mapping,
    which is what makes a table a label at all.
    """

    #: The URL segment, and the engine registry key when a driver names no engine.
    database_type = ""
    #: The ``EngineSpelling`` this driver compiles against.
    engine_name = ""

    def __init__(self, project: Project) -> None:
        super().__init__(project)
        self._access: Optional[RelationalAccess] = None
        self._mapping: Optional[EffectiveMapping] = None
        self._document: Optional[GraphMapping] = None
        self._spelling: Optional[Any] = None

    # -- engine and connection ---------------------------------------------

    @property
    def spelling(self) -> Any:
        """
        This project's engine spelling: the only place the three engines differ at all.

        Resolved once per driver rather than per call, because the query path reads it for
        every statement it compiles and the answer cannot change underneath one request.
        """
        if self._spelling is None:
            self._spelling = engines_for(
                self.engine_name or self.database_type or str(self.config.type)
            )
        return self._spelling

    @property
    def access(self) -> RelationalAccess:
        """
        The connection machinery for this project.

        Built per driver and holding no connection: the pool underneath is what outlives a
        request, which is why nothing else here may cache one.
        """
        if self._access is None:
            self._access = self.make_access()
        return self._access

    def make_access(self) -> RelationalAccess:
        """How this driver reaches a database. One line, so a test can hand a fake pool."""
        return RelationalAccess(self.project, self.spelling)

    async def connect(self) -> None:
        """
        Make sure this project's engine exists — which is all "connect" means here.

        A DBAPI that is not installed surfaces as ``ConnectionError`` carrying the engine's
        install sentence: that is a server-configuration fault, not something a query can
        answer, and no amount of retrying a statement installs ``pymssql``.
        """
        try:
            self.access.engine()
        except DbapiMissing as exc:
            raise ConnectionError(str(exc)) from exc

    async def disconnect(self) -> None:
        """Let go of the engine without closing it."""
        self._access = None

    async def test_connection(self) -> bool:
        """One version probe: the cheapest statement that proves this is that server."""
        try:
            await self.access.probe()
        except Exception as exc:
            print("[{}] test_connection failed: {}".format(self.database_type, exc))
            return False
        return True

    # -- the mapping --------------------------------------------------------

    def override_document(self) -> Optional[GraphMapping]:
        """
        The stored override, or None when this project is left to discovery.

        Parsed once per driver: the query path reads its ``tuning`` from here on every
        statement, and re-validating a document that has already loaded would make the cheap
        path cost a schema parse per query.
        """
        if self._document is not None:
            return self._document
        raw = self.config.graph_mapping
        if not raw:
            return None
        self._document = GraphMapping.model_validate(raw)
        return self._document

    async def effective_mapping(self) -> EffectiveMapping:
        """
        What this project's tables mean as a graph, resolved once per request.

        An override that stands on its own is not paid for with a round trip. One that leans
        on discovery — a record with no ``key``, a link whose endpoints only the live schema
        can name — is checked against it, because that is exactly the case where the schema
        has the answer the file left out.
        """
        if self._mapping is not None:
            return self._mapping
        document = self.override_document()
        if document is None:
            resolved = from_document(None, await self.draft_document())
        else:
            resolved = from_document(document)
            if not resolved.valid:
                resolved = from_document(document, await self.draft_document())
        self._mapping = self._remember(await self.with_key_types(resolved))
        return self._mapping

    async def with_key_types(self, mapping: EffectiveMapping) -> EffectiveMapping:
        """
        Fill in the declared type of any key a document left unnamed.

        A key's type is the one thing in the mapping that cannot be guessed wrong: it is
        what ``Category.keysTypes`` reports, so it decides whether ``render_key_literal``
        quotes a seed — and a numeric key quoted into an ``IN`` list is a round trip that
        matches nothing — and it is what ``column_type`` binds the parameter as, so getting
        it wrong on SQL Server converts the column and turns an index seek into a full scan.
        Discovery records the columns *around* a key and not the key itself, because a
        node's ``properties`` is the whitelist of what GraphXR gets to see, so this is the
        one read a mapping often needs: one page of the tables it already names.

        Nothing is asked when every key already has a type, and a database that will not
        answer leaves the mapping as it was — an unavailable schema is not a reason a
        project's graph becomes unresolvable, and the query that follows will say so itself.
        """
        nodes = [node for node in mapping.nodes.values() if _key_unnamed(node.key_type)]
        edges = [
            edge
            for edge in mapping.edges.values()
            if edge.key and _key_unnamed(edge.key_type or "")
        ]
        if not nodes and not edges:
            return mapping
        wanted = sorted({node.table for node in nodes} | {edge.table for edge in edges})
        declared: Dict[str, Any] = {}
        size = max(1, int(self.spelling.names_page_size))
        try:
            # The mapping's own table list is the bound, and the engine's page size is the
            # unit: a reviewed file that leaves every key untyped names forty tables, and one
            # reflection call of forty tables is a load spike on someone's live server.
            for start in range(0, len(wanted), size):
                page = list(wanted[start : start + size])
                declared.update(
                    (await self.access.catalog(names=page, page_size=len(page))).by_name
                )
        except Exception:
            return mapping
        tables = declared
        filled = dict(mapping.nodes)
        for node in nodes:
            declared = _declared_key(tables, node.table, node.key)
            if declared:
                filled[node.label] = replace(node, key_type=declared)
        filled_edges = dict(mapping.edges)
        for edge in edges:
            declared = _declared_key(tables, edge.table, str(edge.key))
            if declared:
                filled_edges[edge.type] = replace(edge, key_type=declared)
        return replace(mapping, nodes=filled, edges=filled_edges)

    async def draft_document(self) -> Optional[GraphMapping]:
        """
        Discovery's proposal for the live schema, with its caps and skips kept.

        ``propose`` takes the ``Catalog`` rather than an inspector because the warnings are
        about what was *not* mapped — a 3 000-table schema capped at 200 labels, the views
        that cannot be an end — and a document has nowhere else to put them.
        """
        return propose(await self.access.catalog())

    def _remember(self, mapping: EffectiveMapping) -> EffectiveMapping:
        """
        Hand the categories to the intent layer the moment the mapping resolves.

        ``graph_categories()`` and ``graph_key_types()`` are then answered from the mapping,
        so a ``/query`` that loaded it leaves a following ``/expand`` on the same driver able
        to resolve its seeds without probing again.
        """
        self.remember_graph_categories(self.mapping_categories(mapping))
        return mapping

    # -- tuning and limits --------------------------------------------------

    def tuning(self, key: str, default: Any) -> Any:
        """
        One ``mapping.tuning`` override, then the project's ``options``, then the default.

        The document carries per-project query tuning because a person who knows their schema
        can say things discovery cannot — how many ids fit an ``IN`` here, how deep a fan-out
        may go. The config's ``options`` carry the same keys for a project with no mapping
        file yet, which is most of them on the first day.
        """
        document = self.override_document()
        table = (document.tuning if document is not None else None) or {}
        if key in table:
            return table[key]
        options = self.config.options or {}
        if key in options:
            return options[key]
        return default

    def default_limit(self) -> int:
        """The limit an absent ``LIMIT`` gets. Zero or less means the caller wants none."""
        return _positive_int(self.tuning("default_limit", None), query_layer.DEFAULT_LIMIT)

    def max_limit(self) -> Optional[int]:
        """The ceiling a hand-typed ``LIMIT`` cannot walk past, or None for no ceiling."""
        stated = self.tuning("max_limit", None)
        if stated is None:
            return None
        return _positive_int(stated, 0) or None

    def max_branches(self) -> int:
        """
        How many readings of one pattern the planner may enumerate: the query's total bound.

        The tuning key overrides ``plan.DEFAULT_MAX_BRANCHES``, which is the number the
        planner's refusal message quotes, so going through it is what keeps that message true.
        Refusing over the ceiling is right where trimming is not: a dropped branch is an answer
        that is quietly wrong. The chunks a branch splits into for a value list this server
        cannot carry are not capped — they are pieces of one branch's answer, and dropping one
        would drop the rows in it.
        """
        return _positive_int(self.tuning("max_branches", None), PIPELINE.default_max_branches())

    def refusals(self) -> query_layer.Refusals:
        """
        The engine's own additions to the shared refusal list.

        T-SQL has no regular-expression operator at all, so ``=~`` there has to be refused by
        name rather than answered with an empty canvas — which is the whole reason
        ``Refusals`` is a caller-supplied list instead of a rule inside ``query.py``.
        """
        if self.spelling.supports_regex:
            return query_layer.NO_REFUSALS
        return query_layer.Refusals.of(query_layer.REGEX_REFUSAL)

    # -- the query path -----------------------------------------------------

    async def execute_query(
        self, query: str, parameters: Optional[Dict[str, Any]] = None
    ) -> QueryResponse:
        """
        One statement, answered — and never by raising.

        A refusal, a missing DBAPI, an unmappable pattern and an engine error all come back
        as ``success=false`` carrying the sentence that explains them.
        """
        start = time.time()
        statement = str(query or "")
        try:
            probe = await self._probe_statement(statement)
            if probe is not None:
                return probe
            prepared = query_layer.prepare(
                statement,
                default_limit=self.default_limit(),
                max_limit=self.max_limit(),
                refusals=self.refusals(),
            )
            mapping = await self.effective_mapping()
            if not mapping.valid:
                return self._refused(
                    _problems_or(mapping.problems, "this project has no valid mapping"), start
                )
            return await self._run(prepared, mapping, parameters or {}, start)
        except (query_layer.RefusedQuery, PipelineMissing) as exc:
            return self._refused(str(exc), start)
        except Exception as exc:
            return self._refused(_failure(exc), start)

    @staticmethod
    def _refused(error: str, start: float) -> QueryResponse:
        return QueryResponse(success=False, error=error, execution_time=time.time() - start)

    async def _run(
        self,
        prepared: Any,
        mapping: EffectiveMapping,
        parameters: Dict[str, Any],
        start: float,
    ) -> QueryResponse:
        """
        Bound the fan-out, compile every branch, and run them under one limit.

        ``prepare`` bounds one statement and only here is the *total* knowable: an expand
        across several categories, or a polymorphic link that enumerates per pair, is several
        branches, and each of those can split into chunks for a value list the server cannot
        carry. The ceiling is handed to the planner, which refuses with a message naming the
        pattern that blew it — refusing is right where trimming is not, because a dropped
        branch is an answer that is quietly wrong.
        """
        branches = PIPELINE.branches(prepared.plan, mapping, self.max_branches())
        graph = prepared.kind == "graph"
        if not branches:
            # A pattern consistent with the mapping in every name and still unjoinable: an
            # untyped connector whose every pair contradicts the pattern's labels. That is
            # "no such graph", not a failure to answer, so it is an empty answer said so.
            empty = QueryData(
                type="GRAPH" if graph else "TABLE",
                data=GraphData() if graph else [[]],
                summary=_summary(dict(_shape_notes(prepared, 0), **{"relational.branches": 0})),
            )
            return QueryResponse(success=True, data=empty, execution_time=time.time() - start)
        server = await self.access.probe()
        statements: List[Any] = []
        for branch in branches:
            statements.extend(
                PIPELINE.statements(branch, mapping, self.spelling, server, prepared.spec)
            )
        return await self._execute(
            statements, prepared, graph, len(branches), parameters, start
        )

    async def _execute(
        self,
        statements: Sequence[Any],
        prepared: Any,
        graph: bool,
        branch_count: int,
        parameters: Dict[str, Any],
        start: float,
    ) -> QueryResponse:
        """
        Run the compiled statements, stopping once the limit is paid for.

        Each statement carries its own ``LIMIT``, so the first one that fills the total has
        answered as much as the caller asked for and the rest are not sent. That is a
        *best-effort* page across a fan-out — the branches are ordered by nothing the person
        chose — and ``summary`` says so instead of leaving it to be noticed.
        """
        limit = prepared.limit
        pieces: List[QueryData] = []
        counts: Dict[str, int] = {}
        rows_seen = 0
        ran = 0
        for compiled in statements:
            rows = await self.access.rows(
                compiled.select, {**(compiled.parameters or {}), **parameters}
            )
            ran += 1
            rows_seen += len(rows)
            if graph:
                conversion = PIPELINE.graph(rows, compiled.manifest)
                pieces.append(QueryData(type="GRAPH", data=conversion.graph))
                for key, value in (conversion.summary or {}).items():
                    counts[key] = counts.get(key, 0) + int(value or 0)
            else:
                pieces.append(
                    QueryData(type="TABLE", data=PIPELINE.table(rows, compiled.columns))
                )
            if limit and rows_seen >= int(limit):
                break
        data = _combine(pieces, graph, limit)
        notes: Dict[str, Any] = dict(_shape_notes(prepared, ran))
        notes["relational.branches"] = branch_count
        notes["relational.trimmed"] = bool(limit and rows_seen > int(limit))
        notes["relational.paging"] = "best-effort" if prepared.skip and len(statements) > 1 else ""
        notes.update(counts)
        return QueryResponse(
            success=True,
            data=QueryData(
                type="GRAPH" if graph else "TABLE", data=data.data, summary=_summary(notes)
            ),
            execution_time=time.time() - start,
        )

    async def _probe_statement(self, statement: str) -> Optional[QueryResponse]:
        """
        A schema probe, recognised before the guard.

        Answered from the mapping rather than by running anything: a client that asked
        ``CALL db.schema()`` wants what ``/graphSchema`` already returns, and the tables are
        one relational route away. A procedure *with arguments* is refused outright, because
        an argument is a request for something this proxy does not have.
        """
        head = _CALL_HEAD.match(statement)
        if not head:
            return None
        named = str(head.group(1) or "")
        if str(head.group(2) or "").strip():
            return self._refused(
                "CALL {} is refused: this proxy runs no engine procedure, with arguments or "
                "without.".format(named),
                time.time(),
            )
        if named.rsplit(".", 1)[-1].lower() not in _SCHEMA_PROBES:
            return self._refused(
                "CALL {} is refused: this proxy runs no engine procedure. The schema is "
                "/graphSchema, the tables are GET /api/relational/<project>/tables, and "
                "counting and sampling are /graphSchema and /sampleData.".format(named),
                time.time(),
            )
        schema = await self.get_graph_schema()
        if not schema.success:
            return self._refused(schema.error or "the schema is unavailable", time.time())
        rows: List[List[Any]] = [["kind", "name", "key", "ends"]]
        for category in schema.data.categories:
            rows.append(["node", category.name, (category.keys or [""])[0], ""])
        for relationship in schema.data.relationships:
            rows.append(
                [
                    "edge",
                    relationship.name,
                    "",
                    "{} -> {}".format(relationship.startCategory, relationship.endCategory),
                ]
            )
        return QueryResponse(
            success=True,
            data=QueryData(
                type="TABLE", data=rows, summary=_summary({"relational.probe": "schema"})
            ),
            execution_time=time.time(),
        )

    # -- direction preview --------------------------------------------------

    async def preview(
        self, table: str, pair: Mapping[str, Any], edge: str = "", limit: int = 20
    ) -> Dict[str, Any]:
        """
        Run one link both ways against real data, and say which way round it goes.

        The two directions are the same join with the records swapped between the two edge
        columns, so rows on one side and not the other are evidence rather than an opinion.
        ``edge`` names the type for the response only: the columns are what the join uses,
        and a draft being edited has no saved type yet.

        The answer is the page's: ``forwards`` is the mapping's own reading and ``backwards``
        the swapped one, each with its rows and how many there were, and ``verdict`` is the
        closed set those two counts add up to. One direction per field, because a reply that
        carried them as a list would make the reader remember which end of the array it drew
        its picture from.
        """
        wanted = max(1, min(int(limit or 20), PREVIEW_ROWS_MAX))
        mapping = await self.effective_mapping()
        directions: List[Dict[str, Any]] = []
        for name in (DIRECTION_DECLARED, DIRECTION_FLIPPED):
            rows = await self.access.rows(self.preview_statement(mapping, table, pair, wanted, name))
            directions.append(
                {
                    "direction": name,
                    "rows": [{key: plain_cell(value) for key, value in row.items()} for row in rows],
                    "count": len(rows),
                }
            )
        forwards, backwards = directions
        return {
            "table": table,
            "edge": edge,
            "verdict": verdict_of(directions),
            "forwards": {"rows": forwards["rows"], "count": forwards["count"]},
            "backwards": {"rows": backwards["rows"], "count": backwards["count"]},
        }

    # -- the schema surfaces ------------------------------------------------

    async def get_graph_schema(self) -> GraphSchemaResponse:
        """
        The mapping document as the contract's schema.

        Served through ``get_graph_schema_cached``, whose key digests the whole config — so
        saving a new mapping invalidates this by itself, with no bust for the save path to
        remember.
        """
        try:
            mapping = await self.effective_mapping()
        except Exception as exc:
            return GraphSchemaResponse(success=False, error=_failure(exc))
        if not mapping.valid:
            return GraphSchemaResponse(
                success=False,
                error=_problems_or(mapping.problems, "this project has no valid mapping"),
            )
        return GraphSchemaResponse(success=True, data=self.graph_schema(mapping))

    async def get_schema(self) -> SchemaResponse:
        """
        Table, column, declared type — for exactly the tables the mapping uses.

        Paged by name page, so a mapping that names forty tables costs forty tables of
        reflection rather than a walk of the customer's whole library. The types here are the
        engine's own spelling (``nvarchar(100)``): ``/schema`` is the relational view, and
        the shared vocabulary is what ``/graphSchema`` normalises into.
        """
        try:
            mapping = await self.effective_mapping()
        except Exception as exc:
            return SchemaResponse(success=False, error=_failure(exc))
        wanted = sorted(
            {node.table for node in mapping.nodes.values()}
            | {edge.table for edge in mapping.edges.values()}
        )
        data: Dict[str, Dict[str, str]] = {}
        size = max(1, int(self.spelling.names_page_size))
        try:
            for start in range(0, len(wanted), size):
                page = list(wanted[start : start + size])
                catalog = await self.access.catalog(names=page, page_size=len(page))
                for table in catalog.tables:
                    if table.columns:
                        data[table.name] = dict(table.columns)
        except Exception as exc:
            return SchemaResponse(success=False, error=_failure(exc))
        return SchemaResponse(success=True, data=data)

    async def get_sample_data(self) -> SampleDataResponse:
        """
        A bounded look at each label, with the ``LIMIT`` at the source.

        Never a full scan, and never a column the mapping left out: what a sample shows is
        what GraphXR will be able to show for that record.
        """
        try:
            mapping = await self.effective_mapping()
        except Exception as exc:
            return SampleDataResponse(success=False, error=_failure(exc))
        if not mapping.valid:
            return SampleDataResponse(
                success=False,
                error=_problems_or(mapping.problems, "this project has no valid mapping"),
            )
        sample: Dict[str, Any] = {}
        try:
            for node in mapping.nodes.values():
                sample[node.label] = await self._sample_rows(node)
        except Exception as exc:
            return SampleDataResponse(success=False, error=_failure(exc))
        return SampleDataResponse(success=True, data=sample)

    async def _sample_rows(self, node: NodeSchema) -> List[Dict[str, Any]]:
        """
        One label's bounded look, cells already safe for the wire.

        The ``LIMIT`` is in the statement and the projection is the mapping's own columns, so
        a blob nobody mapped is never fetched in order to be dropped.
        """
        rows = await self.access.rows(self.sample_statement(node, SAMPLE_ROWS))
        return [{name: plain_cell(value) for name, value in row.items()} for row in rows]

    async def discover(self, schema: Optional[str] = None) -> Dict[str, Any]:
        """
        Discovery as the mapping page wants it: the proposed model, and what was not used.

        ``propose`` writes the draft and the catalog carries the caps and skips — a
        three-thousand-table schema becomes two hundred labels, and the views that cannot be
        an end of a link are not in the list at all. Those go in ``warnings``, because a page
        that quietly offered forty of three hundred tables is a page that lied.
        """
        catalog: Catalog = await self.access.catalog(schema=schema)
        document = propose(catalog)
        warnings = list(catalog.warnings)
        if catalog.skipped:
            shown = ", ".join(catalog.skipped[:20])
            warnings.append(
                "{} table(s) were skipped as views or engine internals: {}{}".format(
                    len(catalog.skipped), shown, "…" if len(catalog.skipped) > 20 else ""
                )
            )
        return {"model": document.model_dump(by_alias=True), "source": "draft", "warnings": warnings}

    # -- api ----------------------------------------------------------------

    def get_api_info(self, project_name: str) -> Dict[str, Any]:
        """
        The route table for this project.

        Synchronous and connection-free on purpose: opening a pool to tell someone what their
        URLs are would make an unreachable database a slow page rather than an honest error
        on one.
        """
        base = "/api/{}/{}".format(self.database_type or str(self.config.type), project_name)
        return {
            "type": self.database_type,
            "api_urls": {
                "info": base,
                "query": "{}/query".format(base),
                "schema": "{}/schema".format(base),
                "graphSchema": "{}/graphSchema".format(base),
                "sampleData": "{}/sampleData".format(base),
                "capabilities": "{}/capabilities".format(base),
                "expand": "{}/expand".format(base),
                "pullCategory": "{}/pullCategory".format(base),
                "pullRelationship": "{}/pullRelationship".format(base),
                "test": "{}/test".format(base),
                "mapping": "/api/relational/{}".format(project_name),
            },
            "version": "1.0",
            "features": {
                "property_graph": True,
                "cypher": True,
                "sql": True,
                "graph_schema": True,
                "table_schema": True,
                "sample_data": True,
                "relational": True,
                "read_only": True,
                "fulltext_search": False,
                "multi_database": False,
                "engine": self.spelling.name,
                "schema": self.access.schema,
                "mapping": bool(self.config.graph_mapping),
            },
        }


# ---------------------------------------------------------------------------
# shaping one answer
# ---------------------------------------------------------------------------


def _combine(pieces: Sequence[QueryData], graph: bool, limit: Optional[int]) -> QueryData:
    """
    Merge the statements that ran into the one answer the caller asked for.

    A graph merges by id, which is what makes the same seed reached from two chunks one node
    on the canvas rather than two. Trimming it then drops the relationships whose ends did
    not survive: a relationship with a missing endpoint is not a smaller graph, it is an edge
    a canvas cannot draw at all.
    """
    if not pieces:
        return QueryData(type="GRAPH" if graph else "TABLE", data=GraphData() if graph else [[]])
    if not graph:
        rows: List[Any] = []
        for piece in pieces:
            body = list(piece.data or [])
            if not body:
                continue
            if not rows:
                rows = body
            else:
                rows.extend(body[1:])
        if limit:
            rows = rows[: int(limit) + 1]
        return QueryData(type="TABLE", data=rows)
    merged = merge_graph_results(pieces)
    if limit and merged.data:
        wanted = int(limit)
        nodes = list(merged.data.nodes[:wanted])
        kept = {str(node.id) for node in nodes}
        relationships = [
            item
            for item in merged.data.relationships
            if str(item.startNodeId) in kept and str(item.endNodeId) in kept
        ]
        merged = QueryData(type="GRAPH", data=GraphData(nodes=nodes, relationships=relationships))
    return merged


def _shape_notes(prepared: Any, ran: int) -> Dict[str, Any]:
    """What one answer says about the limit it was given."""
    notes: Dict[str, Any] = {
        "relational.kind": prepared.kind,
        "relational.statements": ran,
        "relational.limit": prepared.limit if prepared.limit is not None else "none",
    }
    if prepared.auto_limited:
        notes["relational.autoLimit"] = prepared.limit
    if prepared.capped:
        notes["relational.capped"] = prepared.limit
    if prepared.skip:
        notes["relational.skip"] = prepared.skip
    return notes


def _summary(notes: Mapping[str, Any]) -> Dict[str, str]:
    """``QueryData.summary``, with the version every other driver reports kept intact."""
    out: Dict[str, str] = {"version": "4.0.1"}
    for key, value in notes.items():
        if value == "" or value is None:
            continue
        out[key] = str(value)
    return out


def _problems_or(problems: Sequence[Any], otherwise: str) -> str:
    return problems_text(problems) or otherwise


def _failure(exc: Exception) -> str:
    """
    An engine or driver error, said plainly.

    The exception's own type joins the sentence when its text is empty, because "nothing
    matched" and "could not ask" have to be tellable apart from what a canvas shows, and
    ``str`` of some exceptions is nothing at all.
    """
    text = str(exc).strip()
    if text:
        return text
    return "{}: {}".format(type(exc).__name__, exc.__doc__ or "the database returned an error")


def _key_unnamed(key_type: str) -> bool:
    """
    Whether this key's type is a placeholder rather than a declaration.

    ``STRING`` is the value both ``UNKNOWN_TYPE`` and a normalisation miss leave behind, so
    the honest reading of it is "nobody looked": the extra check costs one bounded catalog
    read and rules out the alternative reading, "this key really is text", being taken for
    the whole schema.
    """
    return not key_type or key_type.strip().upper() == UNKNOWN_TYPE


def _declared_key(tables: Mapping[str, Any], table: str, key: str) -> Optional[str]:
    """The column's own declared spelling as the catalog read it, or None when it did not."""
    entry = tables.get(table)
    if entry is None:
        return None
    declared = str((entry.columns or {}).get(key) or "").strip()
    return declared or None


def _positive_int(value: Any, default: int) -> int:
    try:
        found = int(float(str(value)))
    except (TypeError, ValueError):
        return default
    return found if found > 0 else default
