# -*- coding: utf-8 -*-
"""
Answering one statement, and shaping what the fan-out returns.

``query.py`` prepares a statement; this is the half that runs it. Three jobs live here
because nothing else can do them:

* **probe recognition before the guard.** ``prepare`` refuses ``CALL`` by design — an
  engine procedure is not a read this proxy plans — so a statement that is really asking
  "what is your schema?" has to be read here, first, or the refusal is a dead end.
* **the fan-out's total.** ``prepare`` bounds one statement, and only here is the *total*
  knowable: an expand across several categories, or a polymorphic link that enumerates per
  pair, is several branches, each of which can split into chunks for a value list the
  server cannot carry. The ceiling is the project's ``max_branches``, handed to the planner
  so it refuses with a message naming the pattern that blew it.
* **what the compiled statements mean.** A ``graph`` plan becomes ``QueryData(type="GRAPH")``
  merged by id across the statements that ran, a ``scalar`` or ``aggregate`` plan the
  contract's 2D array, and ``summary`` says how many ran and whether the limit cut the total.

``_failure`` and ``_problems_or`` sit at module level because ``driver_schema.py`` answers
the same way: a refusal or an engine error comes back as ``QueryResponse(success=false,
error=...)`` or a ``success=false`` response, never as an exception, because the routes have
no wrapper and anything that escapes becomes a 500 in the middle of someone's canvas.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ...models.project import GraphData, QueryData, QueryResponse
from ..intents import merge_graph_results
from . import query as query_layer
from .mapping import EffectiveMapping
from .schema_surface import problems_text
from .sql_pipeline import PIPELINE, PipelineMissing

#: ``CALL <name>(...)``, recognised before the guard runs, because the guard refuses CALL.
_CALL_HEAD = re.compile(
    r"^\s*CALL\s+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*(?:\(([^)]*)\))?",
    re.IGNORECASE,
)

#: The probe names this driver answers itself. Anything else a client puts behind ``CALL``
#: stays refused, with the route that does have the answer named in its place.
_SCHEMA_PROBES = ("schema", "graphschema", "graphschemavisualization", "visualization")


class RelationalQuerySurface:
    """
    The execution half. ``self.access``, ``self.spelling``, ``self.effective_mapping`` and
    the tuning surface (``default_limit``, ``max_limit``, ``max_branches``, ``refusals``) are
    required; ``RelationalDriver`` provides them.
    """

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
