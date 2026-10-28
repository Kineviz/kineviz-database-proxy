"""
The pattern IR: what a Cypher statement becomes before anything SQL-shaped exists.

Ported from ``ibase_bridge/backend.py`` (MIT). The reference's field names are kept
so the two files can be read side by side; the iBase-only machinery is dropped --
``id_prefix``, ``LinkEndSpec``, ``resolution``, ``src_type_value`` and ``src_prefix``
all describe link shapes an ordinary relational schema does not have -- and the
schema descriptors (``NodeSchema`` / ``RelSchema`` / ``Endpoint`` / ``Pair``) live
with the mapping rather than here, because a query plan is not the place to resolve
a table.

Nothing in this module imports SQL, a dialect or a database: ``MatchPlan`` is what
``translate.py`` produces and what ``plan.py`` consumes. ``ConcretePlan`` and
``BoundEdge`` are declared here because they are part of the same contract, but only
``plan.py`` builds them -- a ``MatchPlan`` still holds ambiguity (an untyped ``[r]``,
an alternation, a polymorphic label) and a ``ConcretePlan`` holds none.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from .predicate import Expression

if TYPE_CHECKING:  # typing only, never imported at runtime
    # Lane A's endpoint-pair record. The annotation is a string, so ir.py stays
    # importable on its own and a change to that shape cannot break a build here.
    from .schemas import Pair  # noqa: F401

#: The three arrow shapes a pattern can carry, as ``EdgePat.direction``.
#:
#: ``out`` is ``-[r]->``, ``in`` is ``<-[r]-`` and ``both`` is ``-[r]-``. A relational
#: driver resolves ``both`` by fanning the same edge out per orientation, which is why
#: the capability record lists ``["all", "from", "to"]`` and not ``"both"``.
Direction = str


@dataclass(frozen=True)
class VertexPat:
    """One ``( ... )`` group of a pattern, in the order it was written."""

    var: str
    #: The label after ``:``, or None for a bare ``(n)``. A name the pattern could not
    #: carry unquoted arrives already unquoted: ``n:`Person``` reads as ``Person``.
    label: Optional[str] = None
    #: ``ID(n) IN [internal_id(t, o), ...]`` references, as the reference bridge spelled
    #: them. Always None here: these backends have no node-identity function, so
    #: ``translate.py`` refuses such a predicate instead of filling this in, and a seed
    #: is re-selected by primary key.
    id_refs: Optional[List[Tuple[int, int]]] = None
    id_negated: bool = False


@dataclass(frozen=True)
class EdgePat:
    """One connector between two ``VertexPat`` groups."""

    var: str
    #: The types named in ``[r:A|B]``, de-duplicated in order; None for an untyped
    #: ``[r]``, which is every type the mapping has and is what makes a branch.
    types: Optional[List[str]]
    direction: Direction
    #: The variable on the left of the pattern and the one on the right. These are the
    #: *pattern's* ends, not the mapping's: ``direction`` is what turns one into the
    #: other, and ``BoundEdge.swapped`` is where that choice gets recorded.
    src_var: str
    dst_var: str


@dataclass(frozen=True)
class MatchPlan:
    """A whole ``MATCH ... WHERE ... RETURN ... SKIP ... LIMIT`` statement, read."""

    vertices: List[VertexPat] = field(default_factory=list)
    edges: List[EdgePat] = field(default_factory=list)
    #: The parsed ``WHERE`` body, or None when there was no ``WHERE``.
    where: Optional[Expression] = None
    #: ``ORDER BY`` terms as ``(expression text, "ASC" | "DESC")``, still text: the
    #: mapping decides which column an expression names.
    order: List[Tuple[str, str]] = field(default_factory=list)
    limit: Optional[int] = None
    #: ``SKIP n``, and ``OFFSET n`` lands here too.
    skip: int = 0
    #: The ``RETURN`` items exactly as written, or None when the statement had no
    #: ``RETURN``. ``query.py`` classifies them; they are kept raw so a refusal can
    #: quote the item it could not read.
    return_items: Optional[List[str]] = None
    distinct: bool = False

    def vertex(self, var: str) -> Optional[VertexPat]:
        """The pattern's ``var``-th node, or None when no group names it."""
        for vertex in self.vertices:
            if vertex.var == var:
                return vertex
        return None


@dataclass(frozen=True)
class BoundEdge:
    """One edge of a ``ConcretePlan``, with every choice already made."""

    var: str
    rel_type: str
    #: The mapping's endpoint pair this edge runs over, and whether the pattern runs
    #: against the mapping's direction (a reverse arrow, or an undirected fan-out).
    pair: "Pair"
    swapped: bool
    src_var: str
    dst_var: str


@dataclass(frozen=True)
class ConcretePlan:
    """A ``MatchPlan`` with no ambiguity left, which is what makes compiling total."""

    plan: MatchPlan
    #: Pattern variable -> node label. Every variable here has exactly one label, which
    #: is the whole point of the step that built this.
    labels: Dict[str, str]
    edges: List[BoundEdge] = field(default_factory=list)

    def label_of(self, var: str) -> Optional[str]:
        """The label bound to ``var``, or None when this plan does not name it."""
        return self.labels.get(var)
