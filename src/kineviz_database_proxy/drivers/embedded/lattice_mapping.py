# -*- coding: utf-8 -*-
"""
Worker rows -> the proxy's ``QueryData``, for LatticeDB.

Its own module rather than a branch in ``kuzu_mapping`` because the graph is in a
different place. On every other backend here an entity arrives as a *value*: a bolt
``Node`` object, a Kuzu ``{"_id": ..., "_label": ...}`` dict, a GQL element. On
LatticeDB there is no such value to arrive. Its type system is null, bool, int,
float, string, bytes, vector, list and map -- there is no node type and no
relationship type -- so ``RETURN n`` is accepted and answers ``{"n": 1}``: the id,
and nothing else.

What comes back instead is whatever the statement projected, and the dialect
projects the parts an entity is made of. So the graph is read from the **column
names**, not from the values::

    id(n), labels(n), properties(n)                 -> a node bound to ``n``
    id(r), type(r), properties(r), r_src, r_dst     -> a relationship bound to ``r``

``labels`` versus ``type`` is what separates the two, and neither is optional: a
result that projects only ``id(n)`` is a table of numbers, because one number is
not a node.

The endpoint columns are the other half of the arrangement. A LatticeDB edge does
not carry its ends and cannot be asked for them -- ``startNode(r)`` and
``endNode(r)`` are not functions it has, and ``properties(r)`` holds only the
user's data -- so the only thing that knows which nodes an edge joins is the
pattern that matched it. The dialect writes that knowledge into the RETURN as
``id(n) AS r_src, id(m) AS r_dst``, which is what lets this module place an edge
without knowing anything about the statement that produced it.

Ids need no construction here, unlike the Kuzu family's ``<Label>:<key>``.
``id(n)`` reads back *and* matches -- ``WHERE id(n) IN [1,2]`` is accepted -- so
the integer the engine gives is the integer the client sends back. Node ids and
edge ids share one counter, which is harmless: the contract keeps nodes and
relationships in separate collections, and an edge id only has to be unique among
edges to be excluded by ``excludeRelationshipIds``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ...models.project import GraphData, Node, QueryData, RelationshipData

#: ``id(n)`` / ``labels(n)`` / ``type(r)`` / ``properties(r)``. Lowercase because
#: that is the only spelling LatticeDB has -- ``ID(n)`` is not a function here.
_ENTITY_COLUMN = re.compile(r"^(id|labels|type|properties)\(([A-Za-z_][A-Za-z0-9_]*)\)$")

#: ``r_src`` / ``r_dst``, the aliases the dialect writes for an edge's endpoints.
_ENDPOINT_COLUMN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)_(src|dst)$")


class EntityColumns:
    """Where one pattern variable's parts sit in a row."""

    __slots__ = ("variable", "id", "labels", "type", "properties", "src", "dst")

    def __init__(self, variable: str):
        self.variable = variable
        self.id: Optional[int] = None
        self.labels: Optional[int] = None
        self.type: Optional[int] = None
        self.properties: Optional[int] = None
        self.src: Optional[int] = None
        self.dst: Optional[int] = None

    @property
    def is_node(self) -> bool:
        return self.id is not None and self.labels is not None

    @property
    def is_relationship(self) -> bool:
        return self.id is not None and self.type is not None

    @property
    def placeable(self) -> bool:
        """A relationship can only be placed when both of its ends are in the row."""
        return self.src is not None and self.dst is not None


def entity_columns(columns: Sequence[str]) -> List[EntityColumns]:
    """
    The entities a result describes, in the order their variables first appear.

    Reads the header rather than the rows, which is the whole difference from the
    other mapping modules: on LatticeDB the values cannot say what they are, so
    only the projection can.
    """
    found: Dict[str, EntityColumns] = {}

    for index, column in enumerate(columns):
        name = str(column)

        match = _ENTITY_COLUMN.match(name)
        if match:
            part, variable = match.group(1), match.group(2)
            entry = found.setdefault(variable, EntityColumns(variable))
            # First occurrence wins: a variable projected twice -- once bare and
            # once under an alias -- is the same value in both places.
            if getattr(entry, part) is None:
                setattr(entry, part, index)
            continue

        endpoint = _ENDPOINT_COLUMN.match(name)
        if endpoint:
            variable, end = endpoint.group(1), endpoint.group(2)
            entry = found.setdefault(variable, EntityColumns(variable))
            if getattr(entry, end) is None:
                setattr(entry, end, index)

    return [entry for entry in found.values() if entry.is_node or entry.is_relationship]


def columns_hold_graph(columns: Sequence[str]) -> bool:
    """Whether this result's header describes any entity at all."""
    return bool(entity_columns(columns))


def _cell(row: Sequence[Any], index: Optional[int]) -> Any:
    if index is None or index >= len(row):
        return None
    return row[index]


def _labels_of(value: Any) -> List[str]:
    """
    A node's labels.

    ``labels(n)`` answers a list, and an empty one is a real answer: a LatticeDB
    node may carry no label at all, which nothing in the store forbids.
    """
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item is not None]
    return [str(value)] if value is not None else []


def _properties_of(value: Any) -> Dict[str, Any]:
    """``properties(n)`` answers a map; anything else means no properties."""
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def rows_to_graph(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> Tuple[QueryData, int]:
    """
    Every entity the projection describes, de-duplicated, first-seen order.

    Returns the data and the number of relationships that could not be placed
    because the projection did not name their endpoints. That happens only for a
    hand-written statement -- everything the dialect builds names them -- and the
    count is reported rather than papered over with an invented node.
    """
    entities = entity_columns(columns)
    nodes: Dict[str, Node] = {}
    relationships: Dict[str, RelationshipData] = {}
    dropped = 0

    for row in rows:
        for entity in entities:
            identity = _cell(row, entity.id)
            if identity is None:
                continue

            if entity.is_node:
                key = str(identity)
                nodes.setdefault(
                    key,
                    Node(
                        id=key,
                        labels=_labels_of(_cell(row, entity.labels)),
                        properties=_properties_of(_cell(row, entity.properties)),
                    ),
                )
                continue

            start, end = _cell(row, entity.src), _cell(row, entity.dst)
            if start is None or end is None:
                dropped += 1
                continue

            key = str(identity)
            relationships.setdefault(
                key,
                RelationshipData(
                    id=key,
                    type=str(_cell(row, entity.type) or ""),
                    startNodeId=str(start),
                    endNodeId=str(end),
                    properties=_properties_of(_cell(row, entity.properties)),
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


def result_to_query_data(result: Mapping[str, Any]) -> QueryData:
    """One worker result set, as a graph if its header describes one, a table otherwise."""
    rows = result.get("rows") or []
    columns = result.get("columns") or []

    if columns_hold_graph(columns):
        data, dropped = rows_to_graph(columns, rows)
        if dropped:
            data.summary = {
                **data.summary,
                "droppedRelationships": str(dropped),
                "droppedReason": (
                    "a relationship can only be placed when the statement also returns "
                    "its endpoints, which a LatticeDB edge does not carry"
                ),
            }
    else:
        data = rows_to_table(rows, columns)

    if result.get("truncated"):
        data.summary = {**data.summary, "truncated": "true"}
    return data
