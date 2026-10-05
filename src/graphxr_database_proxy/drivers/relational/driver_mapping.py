# -*- coding: utf-8 -*-
"""
What a project's tables mean as a graph, resolved once per request.

An override that stands on its own is not paid for with a round trip. One that leans on
discovery — a record with no ``key``, a link whose endpoints only the live schema can name
— is checked against it, because that is exactly the case where the schema has the answer
the file left out.

The one thing a mapping reliably leaves out is the key's **declared type**, and it is the
one thing that cannot be guessed: it decides whether the seed predicate quotes a numeric
id, and what type the parameter is bound as. ``convert.py`` and ``compile.py`` get theirs
from the mapping's declared types; this reads the schema for the ones nobody named,
bounded to the mapping's own table list and paged by the engine's name-page size, because
one reflection call over forty tables is a load spike on someone's live server.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Mapping, Optional

from ...models.graph_mapping import GraphMapping
from .catalog import propose
from .mapping import EffectiveMapping, from_document
from .schema_surface import UNKNOWN_TYPE


class RelationalMappingSurface:
    """
    The mapping half of the driver. ``self.config``, ``self.access``, ``self.spelling`` and
    the intent layer's ``remember_graph_categories`` are required; ``RelationalDriver``
    provides them, and caches what this resolves so a page never reads a schema twice.
    """

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
