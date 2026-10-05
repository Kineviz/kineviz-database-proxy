"""
Cypher text -> ``MatchPlan``.

Ported from ``ibase_bridge/cypher_translator.py`` (MIT), keeping the reference's
shape — carve the clauses, split the pattern into alternating node groups and
connectors, hand ``WHERE`` to ``predicate.py`` — and replacing its two regex
shortcuts with one quote-aware scanner, because this repo's statement text carries
backtick-quoted identifiers on both sides of every dot:

  * the reference found clauses with ``\\bMATCH\\b`` / ``\\bRETURN\\b`` regexes, which
    splits ``WHERE n.note = 'RETURN me'`` on a word inside a literal and reads
    ``n.limit`` as a ``LIMIT`` clause. A clause head is recognised only where it is a
    word of its own, outside any quoted run and outside brackets;
  * the reference split node groups with ``\\(([^()]*)\\)``, which cannot read the
    label ```sales (2024)``` that ``dialect.backtick`` produces from a table of that
    name — the parentheses inside the quotes end the group early.

Quoting is read here and never emitted: ``predicate.unquote`` and
``predicate.split_name`` are the one place a quoted identifier or literal is decoded,
so a name that came from ``backtick()`` comes back with its doubled backtick folded to
one. Nothing in this module produces SQL, and nothing in it knows an engine exists.

What the grammar cannot answer is refused, naming the construct. That is not polish:
an empty canvas reads as "nothing matched", and a statement we could not read has not
established that anything failed to match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import predicate
from .ir import EdgePat, MatchPlan, VertexPat

#: The functions a ``RETURN`` may aggregate with. The list ``query.classify`` reads the
#: kind of, and the one ``plan.py`` / ``compile.py`` have to answer.
AGGREGATES = ("count", "sum", "avg", "min", "max")

#: Clause heads this front-end reads. ``ORDER`` is ``ORDER BY``; ``OFFSET`` is the
#: ``LIMIT m OFFSET n`` spelling a hand-typed query may use.
_SUPPORTED_HEADS = frozenset({"MATCH", "WHERE", "RETURN", "ORDER", "SKIP", "LIMIT", "OFFSET"})

#: Heads recognised well enough to know they are a clause, and refused. The value is the
#: phrase the refusal names, so the message says what arrived rather than "unsupported".
_REFUSED_HEADS: Dict[str, str] = {
    "OPTIONAL": "OPTIONAL MATCH",
    "WITH": "WITH",
    "UNWIND": "UNWIND",
    "HAVING": "HAVING",
    "CALL": "CALL { } subqueries",
    "UNION": "UNION",
    "FOREACH": "FOREACH",
    "LOAD": "LOAD CSV",
    "USE": "USE",
    "CREATE": "CREATE",
    "MERGE": "MERGE",
    "DELETE": "DELETE",
    "DETACH": "DETACH DELETE",
    "SET": "SET",
    "REMOVE": "REMOVE",
    "DROP": "DROP",
    "ALTER": "ALTER",
    "INSERT": "INSERT",
    "UPDATE": "UPDATE",
}

_QUOTES = "\"'`"
#: A word only starts a clause where the previous character ended a word. Searched, not
#: matched: ``re.match`` anchors at the front, and the text to test is everything before
#: the word, so an anchored "last character" test has to be a search.
_ENDS_WORD = re.compile(r"[.\w]$")
#: ``STARTS WITH`` / ``ENDS WITH``: the ``WITH`` belongs to the operator, not to a clause.
_OPERATOR_WITH = re.compile(r"(?:^|[^\w.])(?:starts|ends)[ \t\r\n]+$", re.IGNORECASE)
_DISTINCT = re.compile(r"^DISTINCT\b\s*(.*)$", re.IGNORECASE | re.DOTALL)
_NAME = re.compile(r"^[A-Za-z_][\w ]*$")


class TranslateError(Exception):
    """The statement is not something this front-end can plan. Names what it found."""


# ----- text scanning -----


def _skip_quoted(text: str, i: int) -> int:
    """Index just past the run that opens at ``text[i]``, in any of the three quote styles."""
    quote = text[i]
    j = i + 1
    n = len(text)
    while j < n:
        ch = text[j]
        if quote == "`":
            if ch == "`":
                if j + 1 < n and text[j + 1] == "`":
                    j += 2
                    continue
                return j + 1
        elif ch == "\\":
            j += 2
            continue
        elif ch == quote:
            return j + 1
        j += 1
    what = "backtick" if quote == "`" else "string literal"
    raise TranslateError("the statement ends inside an unterminated %s run" % what)


def _index_outside(text: str, ch: str, start: int = 0) -> int:
    """The index of the first ``ch`` that is not inside a quoted run, or -1."""
    i = start
    while i < len(text):
        cur = text[i]
        if cur in _QUOTES:
            i = _skip_quoted(text, i)
            continue
        if cur == ch:
            return i
        i += 1
    return -1


def _split_outside(text: str, sep: str) -> List[str]:
    """Split on every ``sep`` that is not inside a quoted run."""
    parts: List[str] = []
    start = 0
    while True:
        i = _index_outside(text, sep, start)
        if i < 0:
            parts.append(text[start:])
            return parts
        parts.append(text[start:i])
        start = i + 1


def _split_once(text: str, sep: str) -> Tuple[str, Optional[str]]:
    """``(before, after)`` on the first unquoted ``sep``, or ``(text, None)``."""
    i = _index_outside(text, sep)
    if i < 0:
        return text, None
    return text[:i], text[i + 1 :]


def _split_on_word(text: str, word: str) -> Tuple[str, Optional[str]]:
    """
    ``(before, after)`` on the first ``word`` standing alone outside quotes.

    For ``AS``: a property called ``cost_as_total`` and a literal ``'AS new'`` both have
    to keep meaning what they say, so the split is by word and not by substring.
    """
    pattern = re.compile(r"(?<![.\w])%s(?![.\w])" % re.escape(word), re.IGNORECASE)
    i = 0
    while i < len(text):
        if text[i] in _QUOTES:
            i = _skip_quoted(text, i)
            continue
        match = pattern.match(text, i)
        if match:
            return text[: match.start()].strip(), text[match.end() :].strip()
        i += 1
    return text.strip(), None


def _bracket_body(text: str, opener: str, closer: str) -> Optional[str]:
    """
    The content of the first balanced ``opener ... closer`` pair, quotes left intact.

    ``[r:T1|T2]`` and ``count(n)`` both come from this. Depth counts unquoted openers,
    so a ``]`` inside a quoted relationship type cannot close the bracket early.
    """
    start = _index_outside(text, opener)
    if start < 0:
        return None
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch in _QUOTES:
            i = _skip_quoted(text, i)
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
            if depth < 0:
                raise TranslateError("unbalanced %r in %r" % (closer, text))
        i += 1
    raise TranslateError("unbalanced %r in %r" % (opener, text))


def strip_comments(text: str) -> str:
    """Drop ``// ...`` and ``/* ... */``, leaving quoted runs alone."""
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _QUOTES:
            j = _skip_quoted(text, i)
            out.append(text[i:j])
            i = j
            continue
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise TranslateError("the statement ends inside an unterminated block comment")
            i = end + 2
            out.append(" ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_clauses(text: str) -> List[Tuple[str, str]]:
    """
    Break a statement into ``(head, body)`` pairs, in order, heads upper-cased.

    A word is a head only where it stands alone (so ``n.limit`` is not ``LIMIT`` and
    ``reorder`` is not ``ORDER``), outside every quoted run (so ``= 'RETURN me'`` is not
    ``RETURN``) and inside no bracket (so a list member that spells a clause is not
    one). ``WITH`` right after ``STARTS`` or ``ENDS`` is the operator's own word. Text
    ahead of the first head is dropped; both refused and supported heads come back,
    because refusing is the caller's decision, not the scanner's.
    """
    clauses: List[Tuple[str, str]] = []
    head: Optional[str] = None
    buf: List[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _QUOTES:
            j = _skip_quoted(text, i)
            buf.append(text[i:j])
            i = j
            continue
        if ch in "([{":
            depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            buf.append(ch)
            i += 1
            continue
        if depth == 0 and ch.isalpha():
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            upper = text[i:j].upper()
            if (
                upper in _SUPPORTED_HEADS
                or upper in _REFUSED_HEADS
            ) and _stands_alone(text, i) and not (upper == "WITH" and _OPERATOR_WITH.search("".join(buf))):
                if head is not None:
                    clauses.append((head, "".join(buf)))
                head = upper
                buf = []
                i = j
                continue
            buf.append(text[i:j])
            i = j
            continue
        buf.append(ch)
        i += 1
    if head is not None:
        clauses.append((head, "".join(buf)))
    return clauses


def _stands_alone(text: str, i: int) -> bool:
    """Whether the word starting at ``i`` begins where a word ended, rather than inside one."""
    return i == 0 or not _ENDS_WORD.search(text[:i])


def clause_heads(text: str) -> List[str]:
    """
    The heads of a statement's clauses, upper-cased, in order.

    Empty for text that has no clause in it, and for text that cannot be scanned at all
    — an unterminated quote is a syntax error for ``translate`` to name, not for a
    caller that is only asking whether this looks like a read query.
    """
    try:
        return [head for head, _ in split_clauses(strip_comments(text))]
    except TranslateError:
        return []


def refusal_phrase(head: str) -> Optional[str]:
    """
    The words a refusal should use for a clause head, or None if this one is not refused.

    ``OPTIONAL`` on its own tells a person less than ``OPTIONAL MATCH`` does, and the
    phrase is already in the table ``translate`` refuses with, so the guard that runs
    before it reads the same one rather than keeping a second spelling.
    """
    return _REFUSED_HEADS.get((head or "").upper())


# ----- pattern -----


def _scan_groups(pattern: str) -> Tuple[List[str], List[str]]:
    """
    The ``( ... )`` groups of a pattern, and the text between, before and after them.

    ``gaps[k + 1]`` joins ``groups[k]`` to ``groups[k + 1]``. Nested parentheses inside
    a quoted label belong to the group they sit in, which is the whole reason this is a
    scanner and not the reference's ``\\(([^()]*)\\)``.
    """
    groups: List[str] = []
    gaps: List[str] = []
    buf: List[str] = []
    depth = 0
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch in _QUOTES:
            j = _skip_quoted(pattern, i)
            buf.append(pattern[i:j])
            i = j
            continue
        if ch == "(":
            depth += 1
            if depth == 1:
                gaps.append("".join(buf))
                buf = []
            buf.append(ch)
            i += 1
            continue
        if ch == ")":
            if depth == 0:
                raise TranslateError("a pattern has a ) with no ( to match: %r" % pattern)
            depth -= 1
            buf.append(ch)
            if depth == 0:
                groups.append("".join(buf))
                buf = []
            i += 1
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth = max(0, depth - 1)
        buf.append(ch)
        i += 1
    if depth:
        raise TranslateError("a pattern has an unmatched ( : %r" % pattern)
    gaps.append("".join(buf))
    return groups, gaps


def _parse_node(group: str, index: int) -> VertexPat:
    """One ``( ... )`` group: an optional variable and an optional label."""
    inner = group[1:-1].strip()
    if not inner:
        return VertexPat(var="__n%d" % index, label=None)
    if _index_outside(inner, "{") >= 0:
        raise TranslateError(
            "an inline property constraint in a pattern (%r) is not supported; write it as a WHERE predicate"
            % inner
        )
    var_text, label_text = _split_once(inner, ":")
    var = predicate.unquote(var_text.strip())
    if not var:
        raise TranslateError("a pattern variable cannot be empty: (%s)" % inner)
    if len(_split_outside(var, ".")) > 1:
        raise TranslateError("a pattern variable cannot be a property path: (%s)" % inner)
    if label_text is None:
        return VertexPat(var=var, label=None)
    labels = _split_outside(label_text.strip(), ":")
    if len(labels) > 1 and any(one.strip() for one in labels[1:]):
        raise TranslateError("more than one label on %r is not supported; filter in WHERE instead" % var)
    label = predicate.unquote(labels[0].strip())
    if not label:
        raise TranslateError("a label after the colon cannot be empty: (%s)" % inner)
    return VertexPat(var=var, label=label)


def _edge_direction(connector: str) -> str:
    """``out`` for ``-[r]->``, ``in`` for ``<-[r]-``, ``both`` for ``-[r]-``."""
    inbound = connector.startswith("<")
    outbound = connector.endswith(">")
    if inbound and not outbound:
        return "in"
    if outbound and not inbound:
        return "out"
    return "both"


def _parse_edge(connector: str, index: int, left: str, right: str) -> EdgePat:
    body = _bracket_body(connector, "[", "]")
    direction = _edge_direction(connector)
    if body is None:
        return EdgePat(var="__e%d" % index, types=None, direction=direction, src_var=left, dst_var=right)
    if _index_outside(body, "{") >= 0:
        raise TranslateError(
            "an inline property constraint on a relationship ([%s]) is not supported; write it as a WHERE predicate"
            % body.strip()
        )
    if ".." in body or "*" in body or "+" in body:
        raise TranslateError(
            "a variable-length path ([%s]) is not supported; write the hops out, or expand one step at a time"
            % body.strip()
        )
    var_text, types_text = _split_once(body, ":")
    var = predicate.unquote(var_text.strip()) or "__e%d" % index
    if len(_split_outside(var, ".")) > 1:
        raise TranslateError("a relationship variable cannot be a property path: [%s]" % body.strip())
    types: Optional[List[str]] = None
    if types_text is not None and types_text.strip():
        named = [predicate.unquote(one.strip()) for one in _split_outside(types_text, "|") if one.strip()]
        # De-duplicated in order: Kuzu's and this dialect's alternation can list one type
        # twice, and a branch per listing would return the same rows twice.
        types = list(dict.fromkeys(named)) or None
    return EdgePat(var=var, types=types, direction=direction, src_var=left, dst_var=right)


def _parse_pattern(pattern: str) -> Tuple[List[VertexPat], List[EdgePat]]:
    groups, gaps = _scan_groups(pattern)
    if not groups:
        raise TranslateError("MATCH needs a pattern of nodes and relationships, got %r" % pattern)
    if gaps[0].strip() or gaps[-1].strip():
        raise TranslateError("a pattern must be node groups joined by arrows, got %r" % pattern)
    if len(_split_outside(pattern, ",")) > 1:
        raise TranslateError("comma-separated MATCH patterns are not supported; one pattern per statement")
    vertices = [_parse_node(group, k) for k, group in enumerate(groups)]
    edges: List[EdgePat] = []
    for position in range(1, len(gaps) - 1):
        connector = gaps[position].strip()
        if "-" not in connector:
            raise TranslateError("pattern groups must be joined by an arrow, not by %r" % connector)
        edges.append(_parse_edge(connector, position - 1, vertices[position - 1].var, vertices[position].var))
    return vertices, edges


# ----- RETURN -----


@dataclass(frozen=True)
class PropItem:
    """``RETURN n.name``, or ``RETURN n.`full name` AS label``."""

    var: str
    prop: str
    alias: str


@dataclass(frozen=True)
class AggItem:
    """``RETURN count(*)``, or ``RETURN sum(n.amount) AS total``."""

    func: str
    #: The variable the aggregate runs over; None only for ``count(*)``.
    var: Optional[str]
    #: Its property; None for ``count(n)`` and ``count(*)``.
    prop: Optional[str] = None
    star: bool = False
    distinct: bool = False
    alias: str = ""


@dataclass(frozen=True)
class ReturnSpec:
    """
    What a ``RETURN`` asks for, which is what decides the shape of the answer.

    ``kind`` is ``"graph"`` when a bare variable or ``*`` stands for whole entities,
    ``"scalar"`` when every item is a property (a table of columns), and ``"aggregate"``
    when any item aggregates — and then ``columns`` are the grouping keys.
    """

    kind: str
    distinct: bool = False
    variables: List[str] = field(default_factory=list)
    columns: List[PropItem] = field(default_factory=list)
    aggregates: List[AggItem] = field(default_factory=list)
    star: bool = False


def _is_name(text: str) -> bool:
    """A readable identifier, spaces included: they arrive from a quoted name."""
    return bool(text) and bool(_NAME.match(text))


def _property_pair(text: str, context: str) -> Tuple[str, str]:
    parts = _split_outside(text.strip(), ".")
    if len(parts) == 2:
        var, prop = predicate.unquote(parts[0]), predicate.unquote(parts[1])
        if _is_name(var) and _is_name(prop):
            return var, prop
    raise TranslateError("%s must be <variable>.<property>, got %r" % (context, text.strip()))


def _agg_alias(func: str, arg: str) -> str:
    """The reference's default alias: ``count(n)`` becomes ``count_n``, ``count(*)`` ``count_all``."""
    return "%s_%s" % (func, arg.replace(".", "_").replace("*", "all"))


def _parse_agg(item: str, alias: Optional[str]) -> AggItem:
    open_index = _index_outside(item, "(")
    func = item[:open_index].strip().lower() if open_index >= 0 else ""
    if open_index < 0 or func not in AGGREGATES or not item.rstrip().endswith(")"):
        raise TranslateError("unsupported RETURN item: %r" % item)
    inner = (_bracket_body(item, "(", ")") or "").strip()
    folded = _DISTINCT.match(inner)
    if folded:
        raise TranslateError(
            "DISTINCT inside an aggregate (%s(DISTINCT %s)) is not supported; write RETURN DISTINCT %s(%s)"
            % (func, folded.group(1).strip(), func, folded.group(1).strip())
        )
    if inner == "*":
        return AggItem(func=func, var=None, prop=None, star=True, alias=alias or _agg_alias(func, inner))
    parts = _split_outside(inner, ".")
    if len(parts) > 1:
        var, prop = _property_pair(inner, "%s()" % func)
        return AggItem(func=func, var=var, prop=prop, alias=alias or _agg_alias(func, inner))
    var = predicate.unquote(inner)
    if not _is_name(var):
        raise TranslateError("%s() needs *, a variable or a variable's property, got %r" % (func, inner))
    return AggItem(func=func, var=var, prop=None, alias=alias or _agg_alias(func, inner))


def parse_return(items: Optional[Sequence[str]], distinct: bool = False) -> ReturnSpec:
    """
    Classify a ``RETURN``, refusing what this contract cannot answer.

    Mirrors the reference's ``_parse_return``: a bare variable or ``*`` means whole
    entities, ``var.prop`` items mean columns, and ``count`` / ``sum`` / ``avg`` /
    ``min`` / ``max`` mean grouping over those columns. Two differences, both because a
    wrong shape costs a person a canvas: an item that is none of those is refused by
    name instead of quietly being read as a graph, and a graph variable beside an
    aggregate is refused instead of dropped.
    """
    variables: List[str] = []
    columns: List[PropItem] = []
    aggregates: List[AggItem] = []
    star = False
    for raw in items or []:
        item = raw.strip()
        if not item:
            continue
        if item == "*":
            star = True
            continue
        expr, alias = _split_on_word(item, "AS")
        if _index_outside(expr, "(") >= 0:
            aggregates.append(_parse_agg(expr, alias))
            continue
        parts = _split_outside(expr, ".")
        if len(parts) == 1:
            var = predicate.unquote(parts[0])
            if not _is_name(var):
                raise TranslateError("unsupported RETURN item: %r" % raw)
            variables.append(var)
            continue
        var, prop = _property_pair(expr, "a RETURN projection")
        columns.append(PropItem(var=var, prop=prop, alias=alias or "%s.%s" % (var, prop)))
    return _return_spec(distinct, variables, columns, aggregates, star)


def _return_spec(
    distinct: bool,
    variables: List[str],
    columns: List[PropItem],
    aggregates: List[AggItem],
    star: bool,
) -> ReturnSpec:
    if aggregates and (variables or star):
        named = variables[0] if variables else "*"
        raise TranslateError(
            "returning %r beside an aggregate function is not supported: a graph variable is a whole entity "
            "and an aggregate is one row per group" % named
        )
    if aggregates:
        kind = "aggregate"
    elif variables or star or not columns:
        kind = "graph"
    else:
        kind = "scalar"
    return ReturnSpec(
        kind=kind, distinct=distinct, variables=variables, columns=columns, aggregates=aggregates, star=star
    )


# ----- the front-end -----


def translate(statement: str) -> MatchPlan:
    """
    Read one ``MATCH ... [WHERE ...] [RETURN ...] [ORDER BY ...] [SKIP n] [LIMIT m]``.

    Raises ``TranslateError``, which always names the construct it could not plan.
    """
    text = strip_comments(statement or "").strip().rstrip(";").strip()
    if not text:
        raise TranslateError("the statement is empty")
    clauses = split_clauses(text)
    if not clauses:
        raise TranslateError("no clause found in %r" % text[:80])
    for head, _body in clauses:
        if head in _REFUSED_HEADS:
            raise TranslateError(
                "%s is not supported; only read-only MATCH ... RETURN statements are planned" % _REFUSED_HEADS[head]
            )
    heads = [head for head, _ in clauses]
    if heads[0] != "MATCH":
        raise TranslateError("a statement this front-end plans must open with MATCH, got %s" % heads[0])
    _check_clause_order(heads)
    bodies = _bodies_by_head(clauses)

    vertices, edges = _parse_pattern(bodies["MATCH"].strip())
    where: Optional[predicate.Expression] = None
    if "WHERE" in bodies:
        where = _parse_where(bodies["WHERE"])
        _refuse_identity(where)

    return_items: Optional[List[str]] = None
    distinct = False
    if "RETURN" in bodies:
        distinct, return_body = _take_distinct(bodies["RETURN"].strip())
        return_items = [item.strip() for item in _split_outside(return_body, ",") if item.strip()]
        # Classified here as well as in ``query.classify``, because a RETURN naming a
        # variable the pattern never bound is a translation failure, and the only way to
        # know it is to have read both halves.
        _check_bound_variables(parse_return(return_items, distinct), vertices, edges)

    limit = _count(bodies.get("LIMIT"), "LIMIT")
    skip = _count(bodies.get("SKIP"), "SKIP")
    offset = _count(bodies.get("OFFSET"), "OFFSET")
    if skip and offset:
        raise TranslateError("SKIP and OFFSET both name an offset; use one")
    return MatchPlan(
        vertices=vertices,
        edges=edges,
        where=where,
        order=_parse_order(bodies.get("ORDER", "")),
        limit=limit,
        skip=offset or skip or 0,
        return_items=return_items,
        distinct=distinct,
    )


def _bodies_by_head(clauses: List[Tuple[str, str]]) -> Dict[str, str]:
    bodies: Dict[str, str] = {}
    for head, body in clauses:
        if head in bodies:
            raise TranslateError("two %s clauses in one statement are not supported" % head)
        bodies[head] = body
    return bodies


#: The one clause order this grammar reads. ``OFFSET`` belongs to the ``LIMIT m OFFSET n``
#: spelling and has to follow ``LIMIT`` for that to mean anything.
_CLAUSE_ORDER = ("MATCH", "WHERE", "RETURN", "ORDER", "SKIP", "LIMIT", "OFFSET")
_CLAUSE_RANK = {head: rank for rank, head in enumerate(_CLAUSE_ORDER)}


def _check_clause_order(heads: List[str]) -> None:
    """
    Refuse a clause that comes before one it has to follow.

    ``MATCH (n) SKIP 1 RETURN n`` parses into the right pieces and means nothing: the
    skip would be read as an offset over rows the RETURN has not produced yet. Saying so
    costs one comparison and keeps a reordered statement from being planned as if it were
    the one the caller meant.
    """
    for position in range(1, len(heads)):
        if _CLAUSE_RANK[heads[position]] < _CLAUSE_RANK[heads[position - 1]]:
            raise TranslateError(
                "%s cannot follow %s in one statement; the order is MATCH, WHERE, RETURN, ORDER BY, then "
                "SKIP and LIMIT" % (heads[position], heads[position - 1])
            )


def _check_bound_variables(spec: ReturnSpec, vertices: List[VertexPat], edges: List[EdgePat]) -> None:
    """
    Refuse a ``RETURN`` that names a variable no pattern binds.

    An unbound name is how a statement asks for rows it never matched: ``MATCH (n)
    RETURN m`` has an answer, and it is empty, and an empty answer on a canvas reads as
    "the data is not there" rather than "you asked for ``m``".
    """
    bound = {vertex.var for vertex in vertices} | {edge.var for edge in edges}
    names: List[str] = list(spec.variables)
    names.extend(column.var for column in spec.columns)
    names.extend(aggregate.var for aggregate in spec.aggregates if aggregate.var)
    for name in names:
        if name not in bound:
            raise TranslateError(
                "RETURN names %r, which no pattern in this statement binds (the variables are %s)"
                % (name, ", ".join(sorted(bound)))
            )


def _parse_where(body: str) -> Optional[predicate.Expression]:
    try:
        return predicate.parse_expression(body)
    except predicate.ParseError as error:
        raise TranslateError("the WHERE clause cannot be read: %s (%s)" % (body.strip()[:80], error)) from error


def _refuse_identity(expr: Optional[predicate.Expression]) -> None:
    """
    Refuse a predicate that names a node by identity, which these backends do not have.

    The reason matters more than the refusal. mssql, PostgreSQL and MySQL have no
    ``ID(n)`` to compare against, so a seed cannot be re-selected by identity: the intent
    layer pins it by primary key instead (``n.`<key>` IN [...]``) and the id the client
    carries is the minted ``<Label>:<key>``. A bolt-shaped ``id(n) IN [internal_id(t, o),
    ...]`` therefore has nothing to answer it, and letting it through is how a Kuzu
    project once received ``ID(n) IN ["<Label>:<key>", ...]`` — a string list against an
    internal id, with every selected id printed back at the user.
    """
    for node in predicate.walk(expr):
        if isinstance(node, predicate.IdIn) or (
            isinstance(node, predicate.EntityFn) and node.func in predicate.IDENTITY_FUNCS
        ):
            raise TranslateError(
                "ID(n) is not supported: these backends have no node-identity function, so a seed node is "
                "re-selected by primary key (n.`<key>` IN [...]) rather than by identity"
            )


def _take_distinct(body: str) -> Tuple[bool, str]:
    """``DISTINCT`` in the ``RETURN`` head position, which is the one this contract keeps."""
    folded = _DISTINCT.match(body)
    if not folded:
        return False, body
    return True, folded.group(1).strip()


def _parse_order(body: str) -> List[Tuple[str, str]]:
    """``ORDER BY`` terms as ``(term text, "ASC" | "DESC")``; a term stays text."""
    text = (body or "").strip()
    if not text:
        return []
    if not re.match(r"^BY\b", text, re.IGNORECASE):
        raise TranslateError("ORDER must be followed by BY")
    order: List[Tuple[str, str]] = []
    for term in _split_outside(text[2:], ","):
        item = term.strip()
        if not item:
            continue
        direction = "DESC" if re.search(r"\bDESC\b", item, re.IGNORECASE) else "ASC"
        expr = re.sub(r"\s+(ASC|DESC)\s*$", "", item, flags=re.IGNORECASE).strip()
        if not expr:
            raise TranslateError("an ORDER BY term has no expression: %r" % item)
        order.append((expr, direction))
    return order


def _count(body: Optional[str], keyword: str) -> Optional[int]:
    """One whole number after ``LIMIT`` / ``SKIP`` / ``OFFSET``, or a refusal naming it."""
    text = (body or "").strip()
    if not text:
        return None
    parts = text.split()
    if len(parts) != 1 or not parts[0].isdigit():
        raise TranslateError("%s needs one whole number, got %r" % (keyword, text[:40]))
    return int(parts[0])
