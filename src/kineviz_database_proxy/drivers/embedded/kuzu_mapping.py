# -*- coding: utf-8 -*-
"""
Worker rows -> the proxy's ``QueryData``.

The twin of ``bolt_mapping``, for the embedded engines. Two things make it its own
module rather than a branch in that one.

**The key casing differs between the two engines.** Kuzu hands back
``{"_id": ..., "_label": ...}``; Ladybug hands back ``{"_ID": ..., "_LABEL": ...}``.
Same shape, same meaning, different spelling, and a driver that only knew one of
them would see a graph result as an ordinary table of dicts. Every lookup here
accepts both.

**A node's id has to be built, not read.** The engines expose ``ID(n)`` as an
``INTERNAL_ID`` -- ``{"table": 0, "offset": 3}`` -- but there is no way to write one
back into a statement: ``n._id`` is rejected as "reserved for system usage". An id
the proxy hands the client must be one the dialect's own predicate can match again,
so it is ``<Label>:<primary key>``, exactly as RocketGraph does for the same reason.
Node tables always have a primary key -- ``CREATE NODE TABLE T(a INT64)`` fails with
"Can not find primary key" -- so the id always exists.

That has one consequence worth stating plainly: a relationship's endpoints arrive
as internal ids, so the nodes at both ends must be in the result for the edge to be
placed. Every statement the dialect builds returns them, which is why
``return_vars`` is ``("n", "r", "m")``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ...models.project import GraphData, Node, QueryData, RelationshipData

#: Kuzu spells these lowercase, Ladybug uppercase. Both are checked, always.
ID_KEYS = ("_id", "_ID")
LABEL_KEYS = ("_label", "_LABEL")
SRC_KEYS = ("_src", "_SRC")
DST_KEYS = ("_dst", "_DST")
#: A recursive relationship value -- what ``-[r*1..2]-`` returns.
PATH_NODE_KEYS = ("_nodes", "_NODES")
PATH_REL_KEYS = ("_rels", "_RELS")

#: Removed from the properties handed to the client: they are the engine's
#: bookkeeping, not the user's data.
META_KEYS = frozenset(ID_KEYS + LABEL_KEYS + SRC_KEYS + DST_KEYS)


def _first(value: Mapping, keys: Sequence[str]) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return None


def _has_any(value: Mapping, keys: Sequence[str]) -> bool:
    return any(key in value for key in keys)


def is_node(value: Any) -> bool:
    """A node value: it has an identity and a label, and no endpoints."""
    return (
        isinstance(value, Mapping)
        and _has_any(value, ID_KEYS)
        and _has_any(value, LABEL_KEYS)
        and not _has_any(value, SRC_KEYS)
    )


def is_relationship(value: Any) -> bool:
    """A relationship value: it has both endpoints."""
    return (
        isinstance(value, Mapping)
        and _has_any(value, SRC_KEYS)
        and _has_any(value, DST_KEYS)
    )


def is_recursive_relationship(value: Any) -> bool:
    """What ``-[r*1..2]-`` returns: the nodes and edges of a walk, in two lists."""
    return (
        isinstance(value, Mapping)
        and _has_any(value, PATH_NODE_KEYS)
        and _has_any(value, PATH_REL_KEYS)
    )


def internal_key(value: Any) -> str:
    """
    ``{"table": 0, "offset": 3}`` -> ``"0:3"``.

    Only ever used to join a relationship to the nodes in the same result. It never
    reaches the client, because it is not something a statement can match on.
    """
    if isinstance(value, Mapping):
        return f"{value.get('table')}:{value.get('offset')}"
    return str(value)


def labels_of(value: Mapping) -> List[str]:
    """
    A node's labels.

    Kuzu gives exactly one; Ladybug added multiple labels per node after the fork,
    and hands back a list when a node has more than one.
    """
    label = _first(value, LABEL_KEYS)
    if isinstance(label, (list, tuple, set)):
        return [str(item) for item in label if item is not None]
    return [str(label)] if label is not None else []


def properties_of(value: Mapping) -> Dict[str, Any]:
    """The user's data, with the engine's bookkeeping keys removed."""
    return {str(key): item for key, item in value.items() if key not in META_KEYS}


def node_id(value: Mapping, key_by_label: Mapping[str, str]) -> str:
    """
    ``<Label>:<primary key>``, the only id form the dialect can match again.

    Falls back to the internal id when the label's key property is unknown. That is
    a corner the driver closes by loading the schema before it maps a graph, and the
    fallback is there so an unexpected label still renders rather than vanishing --
    it just cannot be expanded from.
    """
    labels = labels_of(value)
    label = labels[0] if labels else ""
    key_property = key_by_label.get(label)
    if key_property is not None and key_property in value:
        return f"{label}:{value[key_property]}"
    return f"{label}:#{internal_key(_first(value, ID_KEYS))}"


def relationship_id(value: Mapping) -> str:
    """
    ``<Type>:<table>:<offset>``.

    The engines have no relationship-identity literal either, so this is never fed
    back into a statement -- which is exactly why ``excludeRelationshipIds`` is
    declared false. It only has to be unique inside one result, and it is, including
    for two edges of the same type between the same pair of nodes.
    """
    labels = labels_of(value)
    return f"{labels[0] if labels else ''}:{internal_key(_first(value, ID_KEYS))}"


def graph_entities(value: Any) -> Iterable[Mapping]:
    """Every node and relationship reachable from one cell, at any nesting depth."""
    if is_node(value) or is_relationship(value):
        yield value
        return
    if is_recursive_relationship(value):
        for item in _first(value, PATH_NODE_KEYS) or ():
            yield from graph_entities(item)
        for item in _first(value, PATH_REL_KEYS) or ():
            yield from graph_entities(item)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from graph_entities(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from graph_entities(item)


def rows_hold_graph(rows: Sequence[Sequence[Any]]) -> bool:
    """
    Whether the result carries graph entities.

    Read from the values, not guessed from the statement: a Cypher result says what
    it holds, so there is nothing to infer from syntax the way the GQL backends must.
    """
    for row in rows:
        for cell in row:
            for _ in graph_entities(cell):
                return True
    return False


def rows_to_graph(
    rows: Sequence[Sequence[Any]],
    key_by_label: Optional[Mapping[str, str]] = None,
) -> Tuple[QueryData, int]:
    """
    Every entity in the result, de-duplicated, first-seen order.

    Returns the data and the number of relationships that had to be dropped because
    a node at one end was not in the result. That only happens for a hand-written
    statement like ``MATCH ()-[r]->() RETURN r``: an edge's endpoints arrive as
    internal ids, and turning one into a client-facing id needs the node itself.
    Inventing a placeholder node would be worse -- it would put something on the
    canvas that is not in the graph -- so the count is reported instead.
    """
    keys = key_by_label or {}
    nodes: Dict[str, Node] = {}
    #: internal id -> the client-facing id, so an edge can find its ends.
    by_internal: Dict[str, str] = {}
    pending: List[Mapping] = []

    for row in rows:
        for cell in row:
            for entity in graph_entities(cell):
                if is_node(entity):
                    identity = node_id(entity, keys)
                    by_internal.setdefault(internal_key(_first(entity, ID_KEYS)), identity)
                    nodes.setdefault(
                        identity,
                        Node(
                            id=identity,
                            labels=labels_of(entity),
                            properties=properties_of(entity),
                        ),
                    )
                else:
                    pending.append(entity)

    relationships: Dict[str, RelationshipData] = {}
    dropped = 0
    for entity in pending:
        start = by_internal.get(internal_key(_first(entity, SRC_KEYS)))
        end = by_internal.get(internal_key(_first(entity, DST_KEYS)))
        if start is None or end is None:
            dropped += 1
            continue
        labels = labels_of(entity)
        relationships.setdefault(
            relationship_id(entity),
            RelationshipData(
                id=relationship_id(entity),
                type=labels[0] if labels else "",
                startNodeId=start,
                endNodeId=end,
                properties=properties_of(entity),
            ),
        )

    return (
        QueryData(
            type="GRAPH",
            data=GraphData(nodes=list(nodes.values()), relationships=list(relationships.values())),
        ),
        dropped,
    )


def rows_to_table(rows: Sequence[Sequence[Any]], columns: Sequence[str] = ()) -> QueryData:
    """A 2D array whose first row is the column headers, as the contract expects."""
    table: List[List[Any]] = [[str(column) for column in columns]]
    for row in rows:
        table.append(list(row))
    return QueryData(type="TABLE", data=table)


def result_to_query_data(
    result: Mapping[str, Any],
    key_by_label: Optional[Mapping[str, str]] = None,
) -> QueryData:
    """One worker result set, as a graph if it holds entities and a table otherwise."""
    rows = result.get("rows") or []
    columns = result.get("columns") or []
    if rows_hold_graph(rows):
        data, dropped = rows_to_graph(rows, key_by_label)
        if dropped:
            data.summary = {
                **data.summary,
                "droppedRelationships": str(dropped),
                "droppedReason": "both endpoint nodes must be returned for an edge to be placed",
            }
        return data
    data = rows_to_table(rows, columns)
    if result.get("truncated"):
        data.summary = {**data.summary, "truncated": "true"}
    return data
