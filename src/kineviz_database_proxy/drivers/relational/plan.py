"""
Resolve the ambiguity in a ``MatchPlan``: one pattern in, one branch per meaning.

Ported from ``ibase_bridge/mssql_backend.concrete_plans`` (MIT), keeping its reasoning
and dropping its iBase-only inputs. A pattern is allowed to leave things open — an
unlabelled ``(n)``, an untyped ``[r]``, an alternation ``[r:A|B]``, an undirected
``-[r]-``, a link type that joins several kinds of record — and a compiler cannot be
total over that. Enumerating every reading up front is what makes ``compile.py`` a
lookup rather than a decision, and it replaces three separate passes of the
PostgreSQL bridge the reference took over.

Three differences from the reference, all of them this repo's contract:

* ``VertexPat.id_refs`` is always None here — these backends have no node-identity
  function and ``translate.py`` refuses ``ID(n)`` outright — so the reference's
  "labels implied by the ids in the predicate" pruning has nothing to read and is
  gone. The seeds arrive as ``n.key IN [...]`` and need no label hint.
* A pattern that names a label or a type the mapping does not have is **refused by
  name** (``PatternNotInMapping``) instead of being skipped. Silently dropping a
  misspelled label is how a query returns an empty canvas and reads as "no data".
* The branch cap is ``DEFAULT_MAX_BRANCHES``, and blowing it says which pattern blew
  it up. A truncated branch set is a truncated answer, which is the one outcome this
  proxy promises never to present as a result.

``[]`` is reserved for a pattern that is well-formed but cannot exist in *this*
mapping — a type whose every endpoint pair contradicts the pattern's labels. That is
not the same statement as "nothing matched", and the caller has to be able to tell
the two apart.
"""

from __future__ import annotations

import itertools
from typing import Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from .ir import BoundEdge, ConcretePlan, EdgePat, MatchPlan
from .schemas import NodeSchema, Pair, RelSchema

#: How many readings of one pattern will still be run. Each one costs a statement, so
#: this is a cost ceiling, not a tuning knob: 200 labels is a 200-statement fan-out.
DEFAULT_MAX_BRANCHES = 10

#: Enumerating a combinatorial explosion in order to discover it exceeds the cap is
#: itself the failure, so a pattern whose raw product is this far past the cap is
#: refused before the cross product is walked.
_PRODUCT_SLACK = 64

_OUT: Tuple[bool] = (False,)
_IN: Tuple[bool] = (True,)
_BOTH: Tuple[bool, bool] = (False, True)
#: The orientations one connector admits. ``both`` fans out to two branches rather
#: than one statement with an ``OR``: a key predicate pins one end, so a single
#: ``OR``ed join cannot use the index on either side.
_ORIENTATIONS: Dict[str, Tuple[bool, ...]] = {"out": _OUT, "in": _IN, "both": _BOTH}


class PlanningError(Exception):
    """The pattern cannot be planned. The message names what was wrong."""


class BranchLimitExceeded(PlanningError):
    """Too many readings of this pattern to run them all."""


class PatternNotInMapping(PlanningError):
    """The pattern names a label or a type this mapping does not declare."""


class MappingView(Protocol):
    """The slice of a mapping the planner reads — ``EffectiveMapping`` satisfies it."""

    nodes: Mapping[str, NodeSchema]
    edges: Mapping[str, RelSchema]


def _resolve(name: str, table: Mapping[str, object]) -> Optional[str]:
    """
    The key ``table`` holds for ``name``, exact first, then case-folded.

    A hand-typed ``:person`` should find ``Person`` — SQL Server's default collation
    makes them the same table, and our dictionaries are not doing that for free. An
    exact hit always wins, because on an engine where ``Order`` and ``ORDER`` are two
    tables a case-folded guess would pick the wrong one.
    """
    if name in table:
        return name
    folded = name.casefold()
    hits = [key for key in table if key.casefold() == folded]
    return hits[0] if len(hits) == 1 else None


def describe(plan: MatchPlan) -> str:
    """
    The pattern in words, for a refusal that has to say *which* pattern it means.

    Rebuilt from the IR rather than carried from the statement text, because by the
    time a branch limit trips the caller may be holding a plan it derived from an
    intent, not one a person typed. Connectors are emitted in the order the pattern
    was written: ``translate._parse_pattern`` builds one edge per gap between two
    adjacent groups, so ``edges[i]`` always joins ``vertices[i]`` to ``vertices[i + 1]``.
    """
    pieces: List[str] = []
    for index, vertex in enumerate(plan.vertices):
        if index:
            edge = plan.edges[index - 1]
            middle = edge.var or ""
            if edge.types:
                middle = "%s:%s" % (middle, "|".join(edge.types))
            arrow = {"out": "-[%s]->", "in": "<-[%s]-", "both": "-[%s]-"}.get(
                edge.direction, "-[%s]-"
            )
            pieces.append(arrow % middle)
            pieces.append("(%s)" % vertex.var if vertex.label is None else "(%s:%s)" % (vertex.var, vertex.label))
            continue
        pieces.append("(%s)" % vertex.var if vertex.label is None else "(%s:%s)" % (vertex.var, vertex.label))
    return "MATCH " + "".join(pieces) if pieces else "MATCH (nothing)"


def _stated_types(edge: EdgePat, edges: Mapping[str, RelSchema], where: str) -> List[str]:
    """The types one connector names, resolved — or refused, naming the one that missed."""
    if edge.types is None:
        return list(edges)
    out: List[str] = []
    for name in edge.types:
        found = _resolve(name, edges)
        if found is None:
            raise PatternNotInMapping(
                "%s: relationship type %r named by %s is not in this mapping. "
                "A type this mapping does not declare cannot match any row, and an empty answer "
                "would look like data that is missing rather than a name that is wrong."
                % (where, name, edge.var)
            )
        if found not in out:
            out.append(found)
    return out


def _candidates(
    edge: EdgePat, types: Sequence[str], edges: Mapping[str, RelSchema], fixed: Mapping[str, str]
) -> List[BoundEdge]:
    """Every (type, endpoint pair, orientation) this connector could mean."""
    out: List[BoundEdge] = []
    orientations = _ORIENTATIONS.get(edge.direction, _BOTH)
    want_src = fixed.get(edge.src_var)
    want_dst = fixed.get(edge.dst_var)
    for rel_type in types:
        rel = edges[rel_type]
        for pair in rel.endpoints:
            for swapped in orientations:
                src_label, dst_label = _ends(pair, swapped)
                if want_src is not None and want_src != src_label:
                    continue
                if want_dst is not None and want_dst != dst_label:
                    continue
                out.append(
                    BoundEdge(
                        var=edge.var,
                        rel_type=rel_type,
                        pair=pair,
                        swapped=swapped,
                        src_var=edge.src_var,
                        dst_var=edge.dst_var,
                    )
                )
    return out


def _ends(pair: Pair, swapped: bool) -> Tuple[str, str]:
    """The pattern's ``(src, dst)`` labels for one mapping pair read one way."""
    if swapped:
        return pair.dst.label, pair.src.label
    return pair.src.label, pair.dst.label


def _fixed_labels(plan: MatchPlan, nodes: Mapping[str, NodeSchema], where: str) -> Dict[str, str]:
    """
    What the pattern already states, with a refusal for anything it states wrongly.

    A composite-key table never reaches ``nodes`` — ``mapping`` excludes it, because
    ``NodeRef`` carries one key pair and ``_expand_by_key`` reads ``group[0].key_prop``
    — so a pattern naming one lands here as an unknown label, and the message says what
    was excluded and why rather than treating it as absent data.
    """
    fixed: Dict[str, str] = {}
    for vertex in plan.vertices:
        if vertex.label is None:
            continue
        found = _resolve(vertex.label, nodes)
        if found is None:
            raise PatternNotInMapping(
                "%s: label %r named by %s is not in this mapping. It may be a table the mapping "
                "excluded — one with no single-column key cannot be a node label at all — but it "
                "cannot be a label that simply has no rows." % (where, vertex.label, vertex.var)
            )
        known = fixed.get(vertex.var)
        if known is not None and known != found:
            raise PatternNotInMapping(
                "%s: variable %r is asked to be both %r and %r" % (where, vertex.var, known, found)
            )
        fixed[vertex.var] = found
    return fixed


def concrete_plans(
    plan: MatchPlan,
    nodes: Mapping[str, NodeSchema],
    edges: Mapping[str, RelSchema],
    *,
    max_branches: int = DEFAULT_MAX_BRANCHES,
) -> List[ConcretePlan]:
    """
    Every reading of ``plan`` under this mapping, with nothing left to decide.

    Raises :class:`PatternNotInMapping` for a name the mapping does not have and
    :class:`BranchLimitExceeded` for a pattern with more readings than
    ``max_branches``; returns ``[]`` only for a pattern that is consistent with the
    mapping in every name and still cannot be joined (an untyped connector whose every
    pair contradicts the pattern's labels, say).
    """
    where = describe(plan)
    fixed = _fixed_labels(plan, nodes, where)

    if not plan.edges:
        return _vertex_only_plans(plan, fixed, nodes, where, max_branches)

    per_edge: List[List[BoundEdge]] = []
    for edge in plan.edges:
        types = _stated_types(edge, edges, where)
        candidates = _candidates(edge, types, edges, fixed)
        if not candidates:
            return []
        per_edge.append(candidates)

    product = 1
    for candidates in per_edge:
        product *= len(candidates)
    if product > max_branches * _PRODUCT_SLACK:
        raise BranchLimitExceeded(
            "%s could mean %d different combinations of record type, which is more than the %d "
            "branches this query is allowed to fan out to. Name a label on a node, name a "
            "relationship type, or narrow the selection." % (where, product, max_branches)
        )

    out: List[ConcretePlan] = []
    for combo in itertools.product(*per_edge):
        labels = dict(fixed)
        if not _consistent(combo, labels):
            continue
        out.append(ConcretePlan(plan=plan, labels=labels, edges=list(combo)))
        if len(out) > max_branches:
            raise BranchLimitExceeded(
                "%s matches more than %d combinations of record type. Name a label on a node, "
                "name a relationship type, or narrow the selection; the branches are not trimmed, "
                "because a dropped branch is an answer that is quietly wrong."
                % (where, max_branches)
            )
    return out


def concrete_plans_for(
    plan: MatchPlan, mapping: MappingView, *, max_branches: int = DEFAULT_MAX_BRANCHES
) -> List[ConcretePlan]:
    """``concrete_plans`` over anything that carries ``.nodes`` and ``.edges``."""
    return concrete_plans(plan, mapping.nodes, mapping.edges, max_branches=max_branches)


def _consistent(combo: Sequence[BoundEdge], labels: Dict[str, str]) -> bool:
    """
    Whether one reading of every connector can be one reading of every variable.

    Two rules, both from Cypher rather than from SQL: a variable is one thing, so a
    node shared between hops must land on the same label both times (this is what stops
    a two-hop pattern multiplying out), and a relationship variable keeps its type — a
    second use of ``r`` cannot mean a different link type.
    """
    types: Dict[str, str] = {}
    for bound in combo:
        src_label, dst_label = _ends(bound.pair, bound.swapped)
        for var, label in ((bound.src_var, src_label), (bound.dst_var, dst_label)):
            if labels.setdefault(var, label) != label:
                return False
        seen = types.setdefault(bound.var, bound.rel_type)
        if seen != bound.rel_type:
            return False
    return True


def _vertex_only_plans(
    plan: MatchPlan,
    fixed: Mapping[str, str],
    nodes: Mapping[str, NodeSchema],
    where: str,
    max_branches: int,
) -> List[ConcretePlan]:
    """
    ``MATCH (n)`` — no connector to bind a label through, so the label is the branch.

    An unlabelled vertex is every label in the mapping. That is capped like any other
    fan-out: an unbounded ``MATCH (n)`` across 200 labels is 200 statements, and the
    answer to that is a label in the pattern, not a silently truncated scan of the
    first ten tables.
    """
    options: List[List[str]] = []
    for vertex in plan.vertices:
        stated = fixed.get(vertex.var)
        options.append([stated] if stated is not None else list(nodes))
    product = 1
    for choice in options:
        product *= len(choice)
    if product > max_branches:
        raise BranchLimitExceeded(
            "%s has an unlabelled node, which means every label in this mapping: %d combinations, "
            "over the %d branches allowed. Add a label to the pattern (for example `(n:Person)`)."
            % (where, product, max_branches)
        )
    out: List[ConcretePlan] = []
    for combo in itertools.product(*options):
        labels: Dict[str, str] = {}
        for vertex, label in zip(plan.vertices, combo):
            labels[vertex.var] = label
        out.append(ConcretePlan(plan=plan, labels=labels, edges=[]))
    return out
