"""
The mapping IR: what a table means as a node, and what it means as an edge.

Everything downstream of discovery — the translator, the planner, the compiler,
the row converter, ``/graphSchema`` — is written against these four shapes, so
they are the contract between the lanes and are kept deliberately small: no
engine object, no SQLAlchemy type, no DBAPI handle, nothing that needs a
database to build.

Construct these by keyword. Names are the contract; position is not, because the
spec's sketch lists ``schema`` third where this file — which carries ``show_as``
and ``notes`` too — keeps the required fields first.

Two conventions the rest of the fleet relies on:

* **Types are strings, and they are the engine's own spelling** (``bigint``,
  ``varchar(50)``, ``int8``, ``numeric(19,4)``). ``dialect.is_numeric_key_type``
  already understands those spellings, and ``drivers/relational/engines`` owns the
  normalised vocabulary the client sees. Nothing here tries to convert one to the
  other.
* **An endpoint's ``column`` is the column on the *edge* table** that holds that
  end's key, not the node's key column. The node side comes from
  ``NodeSchema.key``; the join is ``edge.column = node.key``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass(frozen=True)
class Endpoint:
    """One end of one link: a node label plus the edge-table column that holds its key."""

    label: str
    column: str
    column_type: str


@dataclass(frozen=True)
class Pair:
    """
    A concrete ``(src, dst)`` an edge type connects.

    A plain foreign key gives exactly one of these. Several on one type is a
    polymorphic link — the shape iBase and a hand-written override both produce —
    and it is why an untyped expand fans out per pair instead of once.
    """

    src: Endpoint
    dst: Endpoint
    #: Rows counted for this link's table. An int, because everything that consumes a
    #: ``Pair`` compares or prints it; ``SchemaTable.row_estimate`` in the catalog is
    #: where "nobody counted this" and "this is empty" are still told apart.
    row_estimate: int = 0

    def swapped(self) -> "Pair":
        """The same link read the other way round; the count goes with it."""
        return Pair(src=self.dst, dst=self.src, row_estimate=self.row_estimate)


#: What an edge with no pairs at all reports as its representative. Not a usable
#: endpoint — it names nothing — but it keeps ``representative`` total, as the
#: bridge's does, so a schema build cannot crash on a half-declared edge.
BLANK_ENDPOINT = Endpoint(label="", column="", column_type="")


@dataclass(frozen=True)
class NodeSchema:
    """
    One node label: a table, the one column that identifies a row, and its properties.

    ``key`` is always a single column. The graph contract carries one key pair per
    seed node (``dialect.NodeRef``) and ``_expand_by_key`` reads ``group[0].key_prop``,
    so a composite key is not merely awkward here, it is unrepresentable — which is
    why ``mapping`` refuses one rather than quietly using the first column.
    """

    label: str
    table: str
    key: str
    key_type: str
    props: Dict[str, str]
    schema: Optional[str] = None
    show_as: Optional[str] = None
    notes: str = ""

    def all_props(self) -> Dict[str, str]:
        """Every property a row of this label carries, key first."""
        out: Dict[str, str] = {self.key: self.key_type}
        for name, type_name in self.props.items():
            if name != self.key:
                out[name] = type_name
        return out


@dataclass(frozen=True)
class RelSchema:
    """
    One edge type: a table, the pairs it joins, and its properties.

    ``key`` is the edge's *own* key (its PK, else its widest single-column unique)
    so parallel links between the same two records stay distinct. ``None`` means
    there is none: ``ids.py`` mints the id from the two endpoint keys sorted, which
    is direction-stable but collapses duplicates, and ``mapping`` warns when it
    leaves it ``None``.
    """

    type: str
    table: str
    key: Optional[str]
    key_type: Optional[str]
    endpoints: List[Pair] = field(default_factory=list)
    props: Dict[str, str] = field(default_factory=dict)
    schema: Optional[str] = None
    show_as: Optional[str] = None
    notes: str = ""

    @property
    def representative(self) -> Pair:
        """
        The pair to report in a schema panel, which keys relationships by type and so
        can only show one. The most-populated pair is the one a person expects to see.
        """
        if not self.endpoints:
            return Pair(src=BLANK_ENDPOINT, dst=BLANK_ENDPOINT)
        return max(self.endpoints, key=lambda pair: pair.row_estimate)

    def pairs_touching(self, labels: Set[str]) -> List[Pair]:
        """
        The pairs with at least one end in ``labels``.

        This is the pruning that keeps an untyped expand from fanning out across
        every pair of every type in a 300-table schema.
        """
        want = set(labels)
        return [
            pair
            for pair in self.endpoints
            if pair.src.label in want or pair.dst.label in want
        ]
