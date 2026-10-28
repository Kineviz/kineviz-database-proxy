# -*- coding: utf-8 -*-
"""
Bolt records -> the proxy's ``QueryData``.

The Python twin of ``modules/graphdb/shared/boltMapping.js`` in graphxr-dev, and
shared by every bolt-compatible backend (Neo4j, Memgraph).

Two decisions are carried over from there deliberately:

  - **A result is a graph when it actually contains graph entities**, not when the
    statement looked like one. Cypher hands back typed objects, so there is no need
    to guess from syntax the way the GQL backends must.
  - **A node's id is its bolt internal id**, stringified — ``item.identity.toString()``
    on the JavaScript side. Not ``elementId``: that only exists from Neo4j 5, and
    Memgraph has no equivalent, so ``ID()`` is the one predicate both answer. See
    ``_bolt_family_dialect`` in ``dialect.py``.

Pure: no driver construction and no I/O, so the mapping can be asserted directly.
"""

from __future__ import annotations

import base64
import warnings
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from ..models.project import GraphData, Node, QueryData, RelationshipData

#: The type vocabulary is GraphXR's, not the driver's — ``INT64`` and ``DOUBLE``
#: rather than ``INTEGER`` and ``FLOAT``, because that is what every consumer of
#: ``propsTypes`` understands (the Save-to-Kuzu dropdown offers exactly
#: ``STRING`` / ``INT64`` / ``DOUBLE``).
TYPE_ALIASES: Dict[str, str] = {
    "INTEGER": "INT64",
    "INT": "INT64",
    # `db.schema.nodeTypeProperties()` reports Neo4j's own storage names, where a
    # 64-bit integer is a "Long" and the temporal types are spelled without the
    # separators `valueType()` uses.
    "LONG": "INT64",
    "FLOAT": "DOUBLE",
    "LOCAL_TIME": "TIME",
    "LOCALTIME": "TIME",
    "LOCAL_DATE_TIME": "DATETIME",
    "LOCALDATETIME": "DATETIME",
    "ZONED_DATE_TIME": "DATETIME",
    "ZONEDDATETIME": "DATETIME",
    "ZONED_TIME": "TIME",
    "ZONEDTIME": "TIME",
}


def normalize_type_name(type_name: Any) -> str:
    """
    A backend's spelling of a type -> GraphXR's.

    ``SHOW SCHEMA INFO`` says ``"Integer"`` and ``valueType()`` says ``"INTEGER"``;
    both mean ``INT64`` here. ``"NULL"`` is the absence of a type, not a type.
    """
    text = str(type_name).strip().upper() if isinstance(type_name, str) else ""
    if not text or text == "NULL":
        return ""
    return TYPE_ALIASES.get(text, text)


try:  # pragma: no cover - exercised by whichever install is present
    from neo4j.graph import Node as _BoltNode, Path as _BoltPath, Relationship as _BoltRelationship
    from neo4j.spatial import Point as _BoltPoint
except ImportError:  # the bolt drivers are optional; the rest of the module still imports
    _BoltNode = _BoltPath = _BoltRelationship = _BoltPoint = ()


def _is_node(value: Any) -> bool:
    return isinstance(value, _BoltNode)


def _is_relationship(value: Any) -> bool:
    # The driver names each relationship class after its *type* — a `KNOWS` edge is
    # an instance of a class called `KNOWS` — so this must be an isinstance check
    # against the base class, never a comparison of the class name.
    return isinstance(value, _BoltRelationship)


def _is_path(value: Any) -> bool:
    return isinstance(value, _BoltPath)


def _is_point(value: Any) -> bool:
    return isinstance(value, _BoltPoint) or (isinstance(value, tuple) and hasattr(value, "srid"))


def entity_id(entity: Any) -> str:
    """
    A node's or relationship's bolt internal id, as a string.

    ``.id`` is deprecated in the Python driver in favour of ``.element_id``, but it
    is the value ``ID()`` matches on, and ``ID()`` is the predicate the dialect
    emits. The warning is suppressed rather than the deprecation followed; a server
    new enough to withhold the legacy id falls back to the element id, which is
    what ``ID()`` would have to match against there anyway.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        legacy = getattr(entity, "id", None)
    if legacy is not None:
        return str(legacy)
    return str(getattr(entity, "element_id", ""))


def to_json_value(value: Any) -> Any:
    """One cell, as something the JSON response can carry."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if _is_node(value) or _is_relationship(value):
        return {key: to_json_value(item) for key, item in dict(value).items()}
    if _is_path(value):
        return [to_json_value(entity) for entity in _path_entities(value)]
    if _is_point(value):
        # The same spelling the browser has always shown for a spatial value.
        coordinates = list(value)
        parts = [f"srid:{value.srid}", f"x:{coordinates[0]}", f"y:{coordinates[1]}"]
        if len(coordinates) > 2:
            parts.append(f"z:{coordinates[2]}")
        return "point({" + ", ".join(parts) + "})"
    if hasattr(value, "iso_format"):
        # Every neo4j.time type — Date, Time, DateTime, Duration.
        return value.iso_format()
    if isinstance(value, Mapping):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json_value(item) for item in value]
    return str(value)


def infer_value_type(value: Any) -> str:
    """
    A property's type, read off the value a sample carried.

    Neither Neo4j nor Memgraph reports a property's declared type on the sampling
    path — there is no declaration to report — so the value is the only evidence.
    Unlike the browser, which sees JSON and has to tell an integer from a float by
    its ``{low, high}`` shape, the driver hands over real Python types here.

    Returns ``""`` for a value that carries no type: the property is still recorded,
    it just has no type yet.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INT64"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, str):
        return "STRING"
    if isinstance(value, (bytes, bytearray)):
        return "BYTEARRAY"
    if _is_point(value):
        return "POINT"
    if hasattr(value, "iso_format") or hasattr(value, "isoformat"):
        # Checked before the sequence test: neo4j.time.Duration is a tuple subclass,
        # and would otherwise be reported as a LIST.
        return normalize_type_name(type(value).__name__) or "STRING"
    if isinstance(value, (list, tuple, set)):
        return "LIST"
    if isinstance(value, Mapping):
        return "MAP"
    return normalize_type_name(type(value).__name__)


def _path_entities(path: Any) -> List[Any]:
    """A path's nodes and relationships, start to end."""
    entities: List[Any] = []
    nodes = list(getattr(path, "nodes", ()) or ())
    relationships = list(getattr(path, "relationships", ()) or ())
    for index, node in enumerate(nodes):
        entities.append(node)
        if index < len(relationships):
            entities.append(relationships[index])
    return entities


def graph_entities(value: Any) -> Iterable[Any]:
    """Every node and relationship reachable from one cell, at any nesting depth."""
    if _is_node(value) or _is_relationship(value):
        yield value
        return
    if _is_path(value):
        yield from _path_entities(value)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from graph_entities(item)
        return
    if isinstance(value, (list, tuple, set)) and not _is_point(value):
        for item in value:
            yield from graph_entities(item)


def records_hold_graph(records: Sequence[Any]) -> bool:
    """Whether any cell in the result carries a node, relationship or path."""
    for record in records:
        for value in record.values():
            for _ in graph_entities(value):
                return True
    return False


def records_to_graph(records: Sequence[Any]) -> QueryData:
    """Every entity in the result, de-duplicated by internal id, first-seen order."""
    nodes: Dict[str, Node] = {}
    relationships: Dict[str, RelationshipData] = {}

    for record in records:
        for value in record.values():
            for entity in graph_entities(value):
                identity = entity_id(entity)
                if not identity:
                    continue
                properties = {key: to_json_value(item) for key, item in dict(entity).items()}
                if _is_node(entity):
                    nodes.setdefault(
                        identity,
                        Node(
                            id=identity,
                            # The driver hands labels over as a frozenset, so the
                            # server's ordering is already gone; sorted() at least
                            # makes the answer the same on every call.
                            labels=sorted(str(label) for label in entity.labels),
                            properties=properties,
                        ),
                    )
                else:
                    start = entity.start_node
                    end = entity.end_node
                    relationships.setdefault(
                        identity,
                        RelationshipData(
                            id=identity,
                            type=str(entity.type),
                            startNodeId=entity_id(start) if start is not None else "",
                            endNodeId=entity_id(end) if end is not None else "",
                            properties=properties,
                        ),
                    )

    return QueryData(
        type="GRAPH",
        data=GraphData(nodes=list(nodes.values()), relationships=list(relationships.values())),
    )


def records_to_table(records: Sequence[Any], keys: Sequence[str] = ()) -> QueryData:
    """A 2D array whose first row is the column headers, as the contract expects."""
    header = [str(key) for key in (keys or (records[0].keys() if records else ()))]
    table: List[List[Any]] = [header]
    for record in records:
        table.append([to_json_value(value) for value in record.values()])
    return QueryData(type="TABLE", data=table)


def table_rows(result: QueryData) -> List[Dict[str, Any]]:
    """
    A TABLE result as dicts keyed by column name.

    Read by name, never by position: Memgraph does not preserve the ``RETURN``
    order across a ``UNION``, so the shared label-props statement comes back with
    the header ``["props", "label"]`` once it has two or more branches.
    """
    if result is None or result.type != "TABLE" or not isinstance(result.data, list):
        return []
    if len(result.data) < 2:
        return []
    columns = [str(column) for column in result.data[0]]
    rows: List[Dict[str, Any]] = []
    for raw in result.data[1:]:
        cells = list(raw) if isinstance(raw, (list, tuple)) else []
        rows.append({column: (cells[index] if index < len(cells) else None) for index, column in enumerate(columns)})
    return rows
