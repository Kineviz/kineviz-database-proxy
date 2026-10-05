# -*- coding: utf-8 -*-
"""
The reads a client asks for before it has a query: schema, samples, discovery, direction.

``/schema`` answers in the engine's own spelling (``nvarchar(100)``) because it is the
relational view, and ``/graphSchema`` normalises the same mapping into the shared
vocabulary; both are served from ``effective_mapping``, so a schema page costs that
resolution once. Every read here bounds itself at the source — a name page for the
catalog, a ``LIMIT`` inside the sample statement — because a full scan of a table someone
is only looking at is not a mistake the proxy gets to make on a customer's server.

The direction preview belongs here rather than with the statement builders because it is
the awaited half: ``reads.py`` builds the two joins, this runs them against real data and
reports the verdict ``reads.verdict_of`` defines.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from ...models.project import GraphSchemaResponse, SampleDataResponse, SchemaResponse
from .catalog import Catalog, propose
from .driver_query import _failure, _problems_or
from .reads import (
    DIRECTION_DECLARED,
    DIRECTION_FLIPPED,
    PREVIEW_ROWS_MAX,
    plain_cell,
    verdict_of,
)
from .schemas import NodeSchema

#: Rows sampled per label. A sample is a look at the shape of a table, not an export.
SAMPLE_ROWS = 10


class RelationalSchemaReadSurface:
    """
    The read surfaces other than the query path. ``self.access``, ``self.spelling`` and
    ``self.effective_mapping`` are required; ``RelationalDriver`` provides them.
    """

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
