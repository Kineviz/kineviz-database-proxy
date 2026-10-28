# -*- coding: utf-8 -*-
"""
The catalog probe for Kuzu and Ladybug.

Three procedures answer everything, and both engines spell them identically::

    CALL show_tables()          -> id, name, type, database name, comment
    CALL table_info('Person')   -> property id, name, type, default expression, primary key
    CALL show_connection('Rel') -> source table name, destination table name,
                                   source table primary key, destination table primary key

This is a better schema source than any of the other backends have. Kuzu tables are
declared, so the types are read rather than sampled -- the bolt family has to look
at values to guess -- and ``table_info`` marks the primary key outright, which is
exactly what a ``label-key`` identity needs and what stops ``/expand`` from having
to guess which property to match on.

One quirk, found rather than read: ``CALL show_tables() WHERE type = 'NODE'``
returns relationship tables too on Ladybug 0.19.1. The filtering is done here, in
Python, for that reason.

The pure builders take recorded catalog rows, so the tests assert on the real
output of a real engine without needing one.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ...models.project import Category, GraphSchema, QueryData, Relationship
from ..bolt_mapping import normalize_type_name, table_rows

#: ``show_tables().type`` for a node table.
NODE_TABLE_TYPE = "NODE"
#: A plain relationship table, and the grouped form that connects several pairs.
REL_TABLE_TYPES = ("REL", "REL_GROUP")

#: Kuzu's own type names -> GraphXR's vocabulary. The shared ``normalize_type_name``
#: already folds ``INT``/``FLOAT``; these are the spellings only these engines use.
KUZU_TYPE_ALIASES: Dict[str, str] = {
    "BOOL": "BOOLEAN",
    "INT8": "INT64",
    "INT16": "INT64",
    "INT32": "INT64",
    "INT128": "INT64",
    "UINT8": "INT64",
    "UINT16": "INT64",
    "UINT32": "INT64",
    "UINT64": "INT64",
    "SERIAL": "INT64",
    "FLOAT": "DOUBLE",
    "TIMESTAMP": "DATETIME",
    "BLOB": "BYTEARRAY",
}


def normalize_kuzu_type(type_name: Any) -> str:
    """
    A declared column type, in the vocabulary the client understands.

    Parameterised and compound types -- ``DECIMAL(18,3)``, ``STRING[]``,
    ``STRUCT(a INT64)`` -- are left alone past the base name: the client shows them
    verbatim and there is nothing to gain from flattening a list to ``LIST``.
    """
    text = str(type_name or "").strip()
    if not text:
        return ""
    base = text.split("(")[0].strip().upper()
    if base != text.upper():  # parameterised: keep the declaration as written
        return text
    return KUZU_TYPE_ALIASES.get(base, normalize_type_name(base) or base)


def build_category(name: str, properties: Sequence[Mapping[str, Any]]) -> Category:
    """One node table's ``table_info`` rows, as a category."""
    props: List[str] = []
    keys: List[str] = []
    props_types: Dict[str, str] = {}
    keys_types: Dict[str, str] = {}

    for row in properties:
        column = str(row.get("name") or "")
        if not column:
            continue
        declared = normalize_kuzu_type(row.get("type"))
        props.append(column)
        props_types[column] = declared
        if _is_true(row.get("primary key")):
            keys.append(column)
            keys_types[column] = declared

    return Category(
        name=name, props=props, keys=keys, keysTypes=keys_types, propsTypes=props_types
    )


def build_relationships(
    name: str,
    properties: Sequence[Mapping[str, Any]],
    connections: Sequence[Mapping[str, Any]],
) -> List[Relationship]:
    """
    One relationship table, as one entry per pair of endpoints it connects.

    A ``REL_GROUP`` connects several pairs under one name, and the contract's
    ``Relationship`` names a single start and end -- so a group becomes several
    entries sharing a name, which is the same shape Neo4j's schema probe produces.
    """
    props: List[str] = []
    props_types: Dict[str, str] = {}
    for row in properties:
        column = str(row.get("name") or "")
        if not column:
            continue
        props.append(column)
        props_types[column] = normalize_kuzu_type(row.get("type"))

    built: List[Relationship] = []
    for row in connections:
        start = str(row.get("source table name") or "")
        end = str(row.get("destination table name") or "")
        if not start or not end:
            continue
        built.append(
            Relationship(
                name=name,
                props=props,
                keys=[],
                keysTypes={},
                propsTypes=props_types,
                startCategory=start,
                endCategory=end,
            )
        )
    return built


def _is_true(value: Any) -> bool:
    """``table_info``'s primary-key column, whichever way the engine spells a boolean."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


def split_tables(rows: Sequence[Mapping[str, Any]]) -> Dict[str, List[str]]:
    """
    ``show_tables()`` rows split into node and relationship tables.

    Filtered here rather than with a ``WHERE`` on the call: ``CALL show_tables()
    WHERE type = 'NODE'`` returns relationship tables as well on Ladybug 0.19.1.
    """
    nodes: List[str] = []
    relationships: List[str] = []
    for row in rows:
        name = str(row.get("name") or "")
        kind = str(row.get("type") or "").strip().upper()
        if not name:
            continue
        if kind == NODE_TABLE_TYPE:
            nodes.append(name)
        elif kind in REL_TABLE_TYPES:
            relationships.append(name)
    return {"nodes": nodes, "relationships": relationships}


def build_graph_schema(
    tables: Sequence[Mapping[str, Any]],
    table_info: Mapping[str, Sequence[Mapping[str, Any]]],
    connections: Mapping[str, Sequence[Mapping[str, Any]]],
) -> GraphSchema:
    """The whole schema, from recorded catalog rows. Pure, so the tests can assert it."""
    split = split_tables(tables)
    categories = [
        build_category(name, table_info.get(name) or ()) for name in split["nodes"]
    ]
    relationships: List[Relationship] = []
    for name in split["relationships"]:
        relationships.extend(
            build_relationships(name, table_info.get(name) or (), connections.get(name) or ())
        )
    return GraphSchema(categories=categories, relationships=relationships)


def build_table_schema(
    tables: Sequence[Mapping[str, Any]],
    table_info: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Dict[str, Dict[str, str]]:
    """
    The relational view for ``/schema``.

    The bolt family cannot answer this at all -- there is no declaration to report --
    but a Kuzu table is declared, so both node and relationship tables are listed
    with their columns.
    """
    schema: Dict[str, Dict[str, str]] = {}
    split = split_tables(tables)
    for name in split["nodes"] + split["relationships"]:
        columns: Dict[str, str] = {}
        for row in table_info.get(name) or ():
            column = str(row.get("name") or "")
            if column:
                columns[column] = normalize_kuzu_type(row.get("type"))
        schema[name] = columns
    return schema


Runner = Callable[[str], Any]


def quote_literal(name: str) -> str:
    """A table name as a Cypher string literal, for ``table_info('...')``."""
    return "'" + str(name).replace("\\", "\\\\").replace("'", "\\'") + "'"


async def load_catalog(run: Runner) -> Dict[str, Any]:
    """
    Read the catalog: one call for the table list, then two per table.

    ``run`` returns a ``QueryData`` or None; a probe that the engine rejects is an
    answer rather than a fault, the same way the bolt schema probe treats one, so a
    store whose relationship tables cannot be introspected still yields categories.
    """
    tables = table_rows(await run("CALL show_tables() RETURN *"))
    split = split_tables(tables)

    table_info: Dict[str, List[Mapping[str, Any]]] = {}
    connections: Dict[str, List[Mapping[str, Any]]] = {}

    for name in split["nodes"] + split["relationships"]:
        table_info[name] = table_rows(await run(f"CALL table_info({quote_literal(name)}) RETURN *"))

    for name in split["relationships"]:
        connections[name] = table_rows(
            await run(f"CALL show_connection({quote_literal(name)}) RETURN *")
        )

    return {"tables": tables, "table_info": table_info, "connections": connections}


async def load_kuzu_schema(run: Runner) -> GraphSchema:
    """The graph schema, read from the catalog."""
    catalog = await load_catalog(run)
    return build_graph_schema(catalog["tables"], catalog["table_info"], catalog["connections"])


async def load_kuzu_table_schema(run: Runner) -> Dict[str, Dict[str, str]]:
    """The relational schema, read from the same catalog."""
    catalog = await load_catalog(run)
    return build_table_schema(catalog["tables"], catalog["table_info"])
