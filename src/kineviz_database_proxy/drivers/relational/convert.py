"""
Rows and a manifest back into a graph, one cell at a time.

The reading half of ``compile.py``'s bargain: every projected column was given a
``__gx_v{i}_{k|p}{j}`` label precisely so this module never has to know what order the
server returned them in, and every endpoint id is minted from the same label and key the
node beside it was minted from, which is the only way a relationship can be *placed*.

Why an edge needs both ends in the same row is already documented in this repo, twice:
Kuzu and LatticeDB build ids out of the key rather than read them off the edge, so a
relationship whose endpoints are not both present cannot be placed (``CLAUDE.md``,
"Node ids have to round-trip"). The relational drivers inherit that limit and inherit the
answer — the edge is dropped, counted, and said out loud in the summary, because a canvas
missing one line is indistinguishable from a database that never had it.

Cell values follow the drivers already in this tree (``bolt_mapping.to_json_value``,
``embedded.worker._to_json_value``, ``bigquery.json_safe``): base64 for ``bytes``,
ISO-8601 for a date or a timestamp, and a number only where a number is exact.
"""

from __future__ import annotations

import base64
import datetime as _datetime
import decimal
import math
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ...models.project import GraphData, Node, RelationshipData
from . import ids
from .compile import EdgeEntry, Manifest, VertexEntry

#: An exotic value is reported as text, and text this long is a blob, not a value.
MAX_CELL_CHARS = 4096

#: What joins the two endpoint keys of a keyless edge into one direction-stable id. The
#: separator has to be something a key may contain a colon inside of without confusing
#: the reader, and ``split_label_key_id`` only ever splits on the *first* colon.
ENDPOINT_SEPARATOR = "|"


@dataclass(frozen=True)
class GraphConversion:
    """
    The graph, and what it cost.

    ``unplaced_relationships`` and ``dropped_nodes`` exist because a caller has to be able
    to say "3 relationships could not be placed" instead of handing back a smaller graph
    and letting the person notice the difference themselves.
    """

    graph: GraphData
    unplaced_relationships: int = 0
    dropped_nodes: int = 0

    @property
    def summary(self) -> Dict[str, str]:
        """The counts a ``QueryData.summary`` carries, only when they are worth saying."""
        out: Dict[str, str] = {}
        if self.unplaced_relationships:
            out["unplacedRelationships"] = str(self.unplaced_relationships)
        if self.dropped_nodes:
            out["droppedNodes"] = str(self.dropped_nodes)
        return out


def convert_rows(rows: Sequence[Mapping[str, object]], manifest: Manifest) -> GraphConversion:
    """
    Read every row of one compiled statement and merge them into one graph.

    De-duplicated by id as the rows come in, which is what makes a fan-out correct: the
    same seed node reached from two chunks is one node on the canvas, and the caller's
    merge of branch results keeps the first-seen order the same way.
    """
    nodes: Dict[str, Node] = {}
    relationships: Dict[str, RelationshipData] = {}
    unplaced = 0
    dropped = 0

    for row in rows:
        placed: Dict[str, Tuple[str, str]] = {}
        for entry in manifest.vertices:
            placed[entry.var] = _place_node(row, entry, nodes)
            if placed[entry.var][0] == "":
                dropped += 1
                del placed[entry.var]
        for entry in manifest.edges:
            relationship = _place_relationship(row, entry, placed)
            if relationship is None:
                unplaced += 1
                continue
            relationships.setdefault(relationship.id, relationship)

    return GraphConversion(
        graph=GraphData(nodes=list(nodes.values()), relationships=list(relationships.values())),
        unplaced_relationships=unplaced,
        dropped_nodes=dropped,
    )


def rows_to_graph(rows: Sequence[Mapping[str, object]], manifest: Manifest) -> GraphData:
    """The graph alone, for a caller that has no use for the counts."""
    return convert_rows(rows, manifest).graph


def _place_node(row: Mapping[str, object], entry: VertexEntry, nodes: Dict[str, Node]) -> Tuple[str, str]:
    """
    One vertex of one row: its id, its key text, and the node if this row was first.

    A NULL key mints nothing, so the element is dropped rather than named by the empty
    string: ``Person:`` would be one id shared by every keyless row in the table.
    """
    key = row.get(entry.key_column)
    if key is None:
        return ("", "")
    identity = ids.mint(entry.label, key, entry.key_type)
    if identity is None:
        return ("", "")
    if identity not in nodes:
        properties = {name: json_safe(row.get(column)) for column, name in entry.properties.items()}
        # The key belongs among the properties, not only inside the id: a projection of
        # ``n.person_id`` expects to see it, and a schema panel that lists a property the
        # rows never carry is a second, quieter bug.
        properties.setdefault(entry.key_name, json_safe(key))
        nodes[identity] = Node(id=identity, labels=[entry.label], properties=properties)
    return (identity, ids.key_text(key, entry.key_type))


def _place_relationship(
    row: Mapping[str, object],
    entry: EdgeEntry,
    placed: Mapping[str, Tuple[str, str]],
) -> Optional[RelationshipData]:
    start = placed.get(entry.start_var)
    end = placed.get(entry.end_var)
    if start is None or end is None:
        return None
    key = row.get(entry.key_column) if entry.key_column else None
    identity = _edge_identity(entry, key, start[1], end[1])
    if identity is None:
        return None
    properties = {name: json_safe(row.get(column)) for column, name in entry.properties.items()}
    if entry.key_name and key is not None:
        properties.setdefault(entry.key_name, json_safe(key))
    return RelationshipData(
        id=identity,
        type=entry.type,
        startNodeId=start[0],
        endNodeId=end[0],
        properties=properties,
    )


def _edge_identity(entry: EdgeEntry, key: object, start_key: str, end_key: str) -> Optional[str]:
    """
    ``<TYPE>:<key>``, or ``<TYPE>:<a>|<b>`` when the link table has no key of its own.

    The endpoint pair is sorted before it is joined so the id does not depend on which
    way round the pattern was matched: an undirected expand fans out per orientation, and
    an id that flipped with the orientation would draw the same line twice. What that
    costs is stated in the mapping's warning — two genuinely identical links collapse
    into one, because there is nothing left to tell them apart.
    """
    if key is not None:
        return ids.mint(entry.type, key, entry.key_type)
    pair = sorted([start_key, end_key])
    if not pair[0] and not pair[1]:
        return None
    return ids.mint(entry.type, ENDPOINT_SEPARATOR.join(pair))


def json_safe(value: object) -> Any:
    """
    One cell, as something a JSON response can carry.

    A ``Decimal`` becomes a float only when reading it back out gives the same number. A
    trailing zero is not a lost digit (``Decimal("1000.50")`` goes out as ``1000.5``), but
    ``Decimal("1.0000000000000000001")`` has more digits than a double has and stays text:
    a precision loss that happens silently in the middle of a ledger total is exactly the
    class of bug this project keeps documenting in capital letters. Bytes go out as base64,
    which is what the bolt drivers already send for a ``BYTEA`` — and the ``BYTES`` property
    type that tells a client to decode it comes from the mapping, not from here.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():
            return None
        as_float = float(value)
        if math.isfinite(as_float) and decimal.Decimal(repr(as_float)) == value.normalize():
            return as_float
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, _datetime.timedelta):
        return value.total_seconds()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):  # date, time, datetime
        try:
            return str(isoformat())
        except Exception:  # a custom temporal type that lies about isoformat
            return _text(value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    return _text(value)


def _text(value: object) -> str:
    """Anything else, as text, and truncated: a 4 MB XML cell is not a property."""
    text = str(value)
    if len(text) > MAX_CELL_CHARS:
        return text[:MAX_CELL_CHARS] + "..."
    return text


def rows_to_table(rows: Sequence[Mapping[str, object]], columns: Sequence[str]) -> List[List[Any]]:
    """
    A scalar or aggregate answer as a 2D array whose first row is the headers.

    The shape every other driver returns for ``QueryData(type="TABLE")``, so the client's
    table panel sees one format rather than one per backend.
    """
    header = [str(name) for name in columns]
    table: List[List[Any]] = [header]
    for row in rows:
        table.append([json_safe(row.get(name)) for name in columns])
    return table
