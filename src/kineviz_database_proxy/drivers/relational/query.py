"""
Query behaviour: what is refused, what limit applies, what shape the answer has.

The one component that knows any of that, ported from
``ibase_bridge/query_processor.py`` (MIT) and stripped of the parts that belong to
another lane: no dispatch, no branch fan-out, no row conversion, no probe intercept.
The reference returned a ``TABLE`` of ``[["error"], ["unsupported query: ..."]]`` when
it could not plan a statement; here a refusal raises, because a graph response that
carries no graph is indistinguishable on the canvas from a query that legitimately
matched nothing, and "nothing matched" is a sentence nobody should have to guess at.

Three decisions live here:

* **writes are refused before the translator runs**, one message per verb, so the
  answer names what arrived. ``SET`` on a column called ``is_set`` does not: a verb
  counts only where it opens a clause, which is ``translate.clause_heads``;
* **a missing ``LIMIT`` becomes one** (``DEFAULT_LIMIT``, capped by the project's
  ``max_limit``), because an unbounded expand against a warehouse table is the one
  query that brings a proxy down rather than a person's afternoon;
* **the answer's shape is decided from the ``RETURN``** — a graph, a table of
  columns, or a grouped aggregate — and the constructs this contract will not answer
  raise here rather than reaching ``compile.py`` and coming back as nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, List, Literal, Optional, Sequence, Tuple

from . import predicate, translate
from .ir import MatchPlan
from .translate import ReturnSpec, TranslateError

#: The rows an absent ``LIMIT`` gets. The spec's number, and what Kuzu and RocketGraph
#: default to today.
DEFAULT_LIMIT = 1000

#: A statement longer than this is not a query anyone typed. The reference allowed two
#: million characters because Kineviz inlines every selected node id into an expand, and
#: captured traffic reached 42 214 selected nodes; this cap is the same size for the same
#: reason, and it exists so a runaway client cannot make the proxy hold a statement whole.
MAX_QUERY_LENGTH = 2_000_000

#: The three shapes a ``RETURN`` can ask for.
ResultKind = Literal["graph", "scalar", "aggregate"]

#: Clause heads a read query may open with. ``WITH`` is a shape this front-end reads
#: well enough to allow past the write guard and then refuse precisely.
_SHAPE_HEADS = frozenset({"MATCH", "RETURN", "WITH"})

#: Write verbs, each with the reason the refusal gives. Every one of them counts only
#: where it opens a clause, so ``MATCH (n) WHERE n.is_set RETURN n`` is a read.
_WRITE_REFUSALS: List[Tuple[str, str]] = [
    ("CREATE", "it creates rows, and this proxy serves a customer database read-only"),
    ("MERGE", "it writes when no match exists, which is a write even when it matches"),
    ("DELETE", "it removes rows"),
    ("DETACH", "it is the write half of DETACH DELETE"),
    ("SET", "it rewrites columns"),
    ("REMOVE", "it drops properties or labels"),
    ("DROP", "it destroys a schema object the customer owns"),
    ("ALTER", "it changes a schema object the customer owns"),
    ("INSERT", "it writes rows"),
    ("UPDATE", "it writes rows"),
    ("CALL", "it runs an engine procedure, and only read-only MATCH ... RETURN statements are served"),
]

_WRITE_REASONS = dict(_WRITE_REFUSALS)


class RefusedQuery(TranslateError):
    """A statement this contract will not run. The message names the construct."""


# ----- write and shape guard -----


def validate(statement: str) -> Tuple[bool, str]:
    """
    Whether ``statement`` is a read query at all, and if not, why in one sentence.

    Runs before the translator, so a write never reaches a planner and an oversized or
    empty statement never reaches a tokenizer. Each refused verb carries its own
    reason; the shape rule is that a served statement opens with ``MATCH``, ``RETURN``
    or ``WITH``.
    """
    text = (statement or "").strip()
    if not text:
        return False, "the query is empty"
    if len(text) > MAX_QUERY_LENGTH:
        return False, "the query is too long (max %d characters)" % MAX_QUERY_LENGTH
    heads = translate.clause_heads(text)
    if not heads:
        return False, "no clause found; only read-only MATCH ... RETURN queries are served"
    for head in heads:
        reason = _WRITE_REASONS.get(head)
        if reason is not None:
            return False, "%s is refused: %s" % (head, reason)
    if heads[0] not in _SHAPE_HEADS:
        phrase = translate.refusal_phrase(heads[0])
        if phrase is not None:
            return False, "%s is refused: only read-only MATCH ... RETURN statements are served" % phrase
        return False, "a served statement opens with MATCH, RETURN or WITH, not %s" % heads[0]
    return True, "ok"


# ----- engine-specific refusals -----


def _has_regex(plan: MatchPlan) -> bool:
    return any(
        isinstance(node, predicate.StrOp) and node.op == "regex" for node in predicate.walk(plan.where)
    )


@dataclass(frozen=True)
class Refusal:
    """
    One thing a particular engine cannot serve, and the reason it cannot.

    ``detect`` reads a whole ``MatchPlan`` rather than one predicate node because the
    things engines differ about are not all in the ``WHERE`` clause: a fan-out ceiling,
    an absent operator, a collation that cannot do case-insensitive comparison.
    """

    name: str
    reason: str
    detect: Callable[[MatchPlan], bool]


#: The refusal SQL Server needs and PostgreSQL and MySQL do not: no regex operator.
REGEX_REFUSAL = Refusal(
    name="regular-expression matches (=~)",
    reason="this engine has no regular-expression operator",
    detect=_has_regex,
)


@dataclass(frozen=True)
class Refusals:
    """
    The caller's refusal list, so an engine can add to the shared one without forking ``query``.

    Immutable and additive: ``add`` returns a new one, which is what makes it safe to
    build once per engine spelling and hand to every request.
    """

    rules: Sequence[Refusal] = ()

    @classmethod
    def of(cls, *rules: Refusal) -> "Refusals":
        return cls(rules)

    def add(self, *rules: Refusal) -> "Refusals":
        return Refusals(tuple(self.rules) + tuple(rules))

    def check(self, plan: MatchPlan) -> Optional[str]:
        """The message of the first rule this plan trips, or None when none of them apply."""
        for rule in self.rules:
            if rule.detect(plan):
                return "%s is refused: %s" % (rule.name, rule.reason)
        return None


NO_REFUSALS = Refusals()


# ----- prepared statement -----


@dataclass(frozen=True)
class PreparedStatement:
    """A statement that has passed the guard and been planned, with its limit settled."""

    statement: str
    plan: MatchPlan
    spec: ReturnSpec
    #: The limit that will actually be applied, which is the statement's own unless the
    #: project's ``max_limit`` cut it down.
    limit: Optional[int] = None
    skip: int = 0
    #: Whether this limit is ours rather than the caller's, which is what a summary
    #: says "the answer was trimmed" from.
    auto_limited: bool = False
    capped: bool = False

    @property
    def kind(self) -> str:
        """``"graph"``, ``"scalar"`` or ``"aggregate"`` — the shape of the answer."""
        return self.spec.kind


def _effective_limit(
    explicit: Optional[int], default_limit: int, max_limit: Optional[int]
) -> Tuple[Optional[int], bool, bool]:
    """``(limit, auto_limited, capped)`` for one statement."""
    ceiling = max_limit if max_limit and max_limit > 0 else None
    if explicit is None:
        if default_limit <= 0:
            return None, False, False
        limit = min(default_limit, ceiling) if ceiling else default_limit
        return limit, True, ceiling is not None and default_limit > ceiling
    if ceiling is not None and explicit > ceiling:
        return ceiling, False, True
    return explicit, False, False


def _refuse(error: TranslateError) -> RefusedQuery:
    """Turn a grammar refusal into a policy refusal, keeping its message."""
    return RefusedQuery(str(error))


def _spec_of(plan: MatchPlan) -> ReturnSpec:
    """``translate.parse_return`` for one plan, refusing through ``RefusedQuery``."""
    try:
        return translate.parse_return(plan.return_items, plan.distinct)
    except TranslateError as error:
        raise _refuse(error) from error


def prepare(
    statement: str,
    default_limit: int = DEFAULT_LIMIT,
    max_limit: Optional[int] = None,
    refusals: Refusals = NO_REFUSALS,
) -> PreparedStatement:
    """
    Guard, plan, refuse and bound one statement.

    Raises ``RefusedQuery`` — a ``TranslateError``, so a caller that catches the
    translator catches this too — with a message that names whatever it could not
    serve. ``default_limit`` of 0 or less means the caller really does want an
    unbounded read, and ``max_limit`` cuts an explicit ``LIMIT`` down as well as
    supplying an absent one: a ceiling a hand-typed ``LIMIT 10000000`` walks past is
    not a ceiling.
    """
    ok, why = validate(statement)
    if not ok:
        raise RefusedQuery(why)
    try:
        plan = translate.translate(statement)
    except TranslateError as error:
        raise _refuse(error) from error
    spec = _spec_of(plan)
    message = refusals.check(plan)
    if message is not None:
        raise RefusedQuery(message)
    limit, auto_limited, capped = _effective_limit(plan.limit, default_limit, max_limit)
    if limit != plan.limit:
        plan = replace(plan, limit=limit)
    return PreparedStatement(
        statement=statement.strip().rstrip(";").strip(),
        plan=plan,
        spec=spec,
        limit=limit,
        skip=plan.skip,
        auto_limited=auto_limited,
        capped=capped,
    )


def classify(plan: MatchPlan) -> ResultKind:
    """
    ``"graph"`` for whole entities, ``"scalar"`` for columns, ``"aggregate"`` for a grouped count.

    The same read ``prepare`` records on its ``PreparedStatement``, so a caller holding
    a plan can ask the question without preparing anything. A ``RETURN`` this contract
    cannot answer raises, naming the item.
    """
    return _spec_of(plan).kind  # type: ignore[return-value]
