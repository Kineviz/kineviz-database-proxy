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
from typing import Any, Dict, Mapping, Optional, Sequence, Set, Tuple, Union

from ...models.graph_mapping import GraphMapping
from .catalog import SchemaTable, propose
from .mapping import EffectiveMapping, from_document
from .mapping_index import names_a_type
from .schemas import NodeSchema, RelSchema


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
        schema = self.access.schema
        if document is None:
            resolved = from_document(None, await self.draft_document())
        else:
            document = document.model_copy(
                update={"schema_name": document.schema_name or schema}
            )
            resolved = from_document(document)
            if not resolved.valid:
                draft = await self.draft_document(schema=document.schema_name)
                resolved = from_document(document, draft)
        # Reflection and generated SELECTs must resolve an unqualified table to the
        # same schema, even when the database login has a different default schema.
        resolved = replace(
            resolved,
            nodes={
                name: replace(node, schema=node.schema or schema)
                for name, node in resolved.nodes.items()
            },
            edges={
                name: replace(edge, schema=edge.schema or schema)
                for name, edge in resolved.edges.items()
            },
        )
        if document is not None and document.version == 2:
            resolved = await self.with_source_types(resolved)
        self._mapping = self._remember(await self.with_key_types(resolved))
        return self._mapping

    async def mapped_tables(
        self, records: Sequence[Union[NodeSchema, RelSchema]]
    ) -> Dict[Tuple[Optional[str], str], Optional[SchemaTable]]:
        """Reflect only these tables, in schema-specific pages, once per request.

        A bare table name cannot identify a physical table across schemas. Missing
        tables are cached too; failed reads are left uncached so a later read can
        report the connection error rather than silently treating it as absence.
        """
        wanted = {
            (record.schema or self.access.schema, record.table) for record in records
        }
        groups: Dict[Optional[str], Set[str]] = {}
        for schema, table in sorted(wanted, key=lambda item: (item[0] or "", item[1])):
            if (schema, table) not in self._catalog_tables:
                groups.setdefault(schema, set()).add(table)
        size = max(1, int(self.spelling.names_page_size))
        for schema, names in groups.items():
            ordered = sorted(names)
            for start in range(0, len(ordered), size):
                page = ordered[start : start + size]
                catalog = await self.access.catalog(
                    schema=schema, names=page, page_size=len(page)
                )
                found = catalog.by_name
                for table in page:
                    self._catalog_tables[(schema, table)] = found.get(table)
        return {key: self._catalog_tables[key] for key in wanted}

    async def with_source_types(self, mapping: EffectiveMapping) -> EffectiveMapping:
        """Resolve v2 graph types against the mapped physical columns, in pages.

        GraphXR's INT64 and STRING describe values, not SQL parameter types.
        Native types stay in the execution mapping; the saved file stays portable.
        """
        tables = await self.mapped_tables([*mapping.nodes.values(), *mapping.edges.values()])

        def typed(record: Union[NodeSchema, RelSchema]) -> Union[NodeSchema, RelSchema]:
            table = tables.get((record.schema or self.access.schema, record.table))
            if table is None:
                return record
            props = {
                name: table.columns.get(record.property_columns.get(name, name), declared)
                for name, declared in record.props.items()
            }
            updates: Dict[str, Any] = {"props": props}
            if record.key:
                updates["key_type"] = table.columns.get(record.key, record.key_type)
            if isinstance(record, RelSchema):
                updates["endpoints"] = [
                    replace(pair,
                        src=replace(pair.src, column_type=table.columns.get(pair.src.column, pair.src.column_type)),
                        dst=replace(pair.dst, column_type=table.columns.get(pair.dst.column, pair.dst.column_type)),
                    ) for pair in record.endpoints
                ]
            return replace(record, **updates)

        return replace(mapping,
            nodes={name: typed(node) for name, node in mapping.nodes.items()},
            edges={name: typed(edge) for name, edge in mapping.edges.items()},
        )

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
        try:
            tables = await self.mapped_tables([*nodes, *edges])
        except Exception:
            return mapping
        filled = dict(mapping.nodes)
        for node in nodes:
            entry = tables.get((node.schema or self.access.schema, node.table))
            declared = _declared_key({node.table: entry}, node.table, node.key)
            if declared:
                filled[node.label] = replace(node, key_type=declared)
        filled_edges = dict(mapping.edges)
        for edge in edges:
            entry = tables.get((edge.schema or self.access.schema, edge.table))
            declared = _declared_key({edge.table: entry}, edge.table, str(edge.key))
            if declared:
                filled_edges[edge.type] = replace(edge, key_type=declared)
        return replace(mapping, nodes=filled, edges=filled_edges)

    async def draft_document(self, schema: Optional[str] = None) -> Optional[GraphMapping]:
        """
        Discovery's proposal for the live schema, with its caps and skips kept.

        ``propose`` takes the ``Catalog`` rather than an inspector because the warnings are
        about what was *not* mapped — a 3 000-table schema capped at 200 labels, the views
        that cannot be an end — and a document has nowhere else to put them.
        """
        return propose(await self.access.catalog(schema=schema))

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

    ``NULL`` joins the placeholder set for the same reason the property path puts it there:
    it is what every engine's reflection resolver writes for a ``sqltypes.NULLTYPE`` after
    warning "Did not recognize type", and reading it as a declaration hands a non-name to
    ``spelling.column_type`` — which returns ``None`` for every engine — and the label is
    refused at every statement that reaches it. ``names_a_type`` is the property path's
    test, and the two predicates have to agree, so the key path follows it.
    """
    return not names_a_type(key_type)


def _declared_key(tables: Mapping[str, Any], table: str, key: str) -> Optional[str]:
    """The column's own declared spelling as the catalog read it, or None when it did not."""
    entry = tables.get(table)
    if entry is None:
        return None
    declared = str((entry.columns or {}).get(key) or "").strip()
    return declared or None
