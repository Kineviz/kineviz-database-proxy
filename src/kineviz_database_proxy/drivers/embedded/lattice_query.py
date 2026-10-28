# -*- coding: utf-8 -*-
"""
A statement the user wrote, made answerable.

``MATCH (n)-[r]->(m) RETURN * LIMIT 100`` is the first thing anyone types -- it is
the example in this proxy's own API docs -- and on LatticeDB it stops at the
parser. ``*`` is not in the grammar: the message is ``Expected expression``, which
names neither the token nor the reason. ``count(*)`` fails the same way.

Naming the variables instead parses, and is worse. There is no node value to name,
so ``RETURN n, r, m`` answers ``{"n": 1, "r": 1, "m": 2}`` -- three integers that
look like a result, carry no labels, no properties and no edge, and produce an
empty graph rather than an error.

Both have one fix, and the dialect already knows it: project the parts an entity is
made of, and name each edge's ends. This module finds the queries that fix is valid
for and applies it, so what the user typed reaches the mapper in the shape the
mapper reads.

It is deliberately timid, the way ``_rewrite_for_graph_intent`` is for RocketGraph.
One MATCH, no clause that could rebind anything, a RETURN that is ``*`` or bare
variables the pattern bound, and every projected relationship directed with a named
node at each end. Anything else goes to the engine untouched: a query this cannot
read is a query it must not rewrite, because a wrong rewrite returns wrong data
where the engine would have returned an error.

The other incompatibility is quoting. LatticeDB has no quoted-identifier syntax
at all: a backtick is an ``Invalid token`` wherever it appears -- label, type,
property key, variable -- and there is nothing to escape into instead, since double
quotes and brackets are rejected in those positions too. Every other Cypher backend
here accepts backticks, so a client that quotes by habit is producing a statement
this engine refuses before it reads anything else. GraphXR's search builder quotes
every label and every type, which is how
``MATCH (n0:`Document`)-[r0:`CITES`]->(n1:`Document`)`` arrives.

That splits in two. Where the quoted name is already a bare identifier the quoting
was decoration and the backticks simply come off. Where it is not -- a label with a
space -- unquoting produces a *worse* error, because a pattern here cannot carry
such a label at all; the label moves into a ``WHERE`` predicate instead, which is
the same answer the dialect gives for its own statements.

Direction is the reason undirected patterns are refused rather than handled. An
edge here cannot be asked for its endpoints, so the ends come from the pattern --
and ``(a)-[r]-(b)`` does not say which of ``a`` and ``b`` the edge actually leaves.
Projecting ``id(a) AS r_src`` would be right half the time and silently backwards
the rest, which is why the dialect emits undirected traversals as one statement per
direction and why this stops instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from ..dialect import LATTICEDB_DIALECT, cypher_string, project_variables

_MATCH_KW = re.compile(r"\bMATCH\b", re.IGNORECASE)
_RETURN_KW = re.compile(r"\bRETURN\b", re.IGNORECASE)
_WHERE_KW = re.compile(r"\bWHERE\b", re.IGNORECASE)

#: Where the RETURN body stops and the tail this module preserves begins.
_TAIL_KW = re.compile(r"\b(ORDER\s+BY|SKIP|LIMIT)\b", re.IGNORECASE)

#: Anything that could bind, rebind or branch. The projection is derived from one
#: pattern; a query with a second source of variables is not one this can read.
_FORBIDDEN = re.compile(
    r"\b(OPTIONAL\s+MATCH|MERGE|WITH|UNWIND|CREATE|DELETE|REMOVE|SET|CALL|"
    r"FOREACH|UNION)\b",
    re.IGNORECASE,
)

_BARE_VAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: The variable at the head of ``(n:Person {x: 1})`` or ``[r:KNOWS]``, if it has one.
_LEADING_VAR = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?=[:{\s]|$)")

NODE = "node"
RELATIONSHIP = "relationship"


@dataclass(frozen=True)
class _Group:
    """One ``(...)`` or ``[...]`` in a pattern, and where it sat."""

    kind: str
    variable: Optional[str]
    inner: str
    start: int
    end: int


def _scan(pattern: str) -> Optional[List[_Group]]:
    """
    The top-level ``(...)`` and ``[...]`` groups of a pattern, in order.

    String literals are skipped rather than scanned, so a property map holding a
    bracket in a value cannot unbalance the count. Anything unbalanced returns None:
    a pattern this cannot take apart is one nothing downstream should guess at.
    """
    groups: List[_Group] = []
    depth = 0
    opener = ""
    start = 0
    quote = ""
    index = 0

    while index < len(pattern):
        char = pattern[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "'\"`":
            quote = char
            index += 1
            continue
        if char in "([":
            if depth == 0:
                opener = char
                start = index
            depth += 1
        elif char in ")]":
            depth -= 1
            if depth < 0:
                return None
            if depth == 0:
                inner = pattern[start + 1 : index]
                found = _LEADING_VAR.match(inner)
                groups.append(
                    _Group(
                        kind=NODE if opener == "(" else RELATIONSHIP,
                        variable=found.group(1) if found else None,
                        inner=inner,
                        start=start,
                        end=index + 1,
                    )
                )
        index += 1

    if depth or quote:
        return None
    return groups


def _endpoints_of(
    pattern: str, groups: Sequence[_Group], position: int
) -> Optional[Tuple[str, str]]:
    """
    ``(start, end)`` variable names for the relationship at ``position``.

    Read off the arrows, not the order of the pattern: ``(a)<-[r]-(b)`` binds the
    same three variables as ``(a)-[r]->(b)`` and means the opposite edge. An
    undirected connector, an unnamed end or a comma between the edge and a node
    returns None, and the caller declines to rewrite.
    """
    if position == 0 or position + 1 >= len(groups):
        return None
    before, edge, after = groups[position - 1], groups[position], groups[position + 1]
    if before.kind != NODE or after.kind != NODE:
        return None
    if not before.variable or not after.variable:
        return None

    left = pattern[before.end : edge.start]
    right = pattern[edge.end : after.start]
    if "," in left or "," in right:
        return None

    forward = ">" in right and "<" not in left
    backward = "<" in left and ">" not in right
    if forward:
        return before.variable, after.variable
    if backward:
        return after.variable, before.variable
    return None


@dataclass(frozen=True)
class _Query:
    """A query taken apart far enough to put back together."""

    #: Everything up to the pattern -- in practice the MATCH keyword itself.
    prefix: str
    pattern: str
    #: The WHERE clause as written, keyword included, or "" when there is none.
    where: str
    return_body: str
    tail: str
    groups: List[_Group]

    @property
    def head(self) -> str:
        return f"{self.prefix}{self.pattern}{self.where}"


def _split(query: str) -> Optional[_Query]:
    """The one query shape this module will touch, or None."""
    cleaned = query.strip().rstrip(";").rstrip()

    returns = _RETURN_KW.search(cleaned)
    if returns is None:
        return None

    head = cleaned[: returns.start()]
    rest = cleaned[returns.end() :]
    tail_at = _TAIL_KW.search(rest)
    body = (rest[: tail_at.start()] if tail_at else rest).strip()
    tail = rest[tail_at.start() :] if tail_at else ""
    if not body:
        return None

    if _FORBIDDEN.search(head):
        return None
    matches = list(_MATCH_KW.finditer(head))
    if len(matches) != 1:
        return None

    pattern_from = matches[0].end()
    where = _WHERE_KW.search(head[pattern_from:])
    pattern_to = pattern_from + where.start() if where else len(head)
    pattern = head[pattern_from:pattern_to]
    if not pattern.strip():
        return None

    groups = _scan(pattern)
    if not groups:
        return None
    # A variable-length edge matches a path of unknown length, and a projection
    # names a fixed set of variables. Refusing the whole query is the point: the
    # edge carries no name a leading-identifier read would find, so it would
    # otherwise be dropped from ``*`` in silence and answer a path query with a
    # list of its endpoints.
    if any(group.kind == RELATIONSHIP and "*" in group.inner for group in groups):
        return None

    return _Query(
        prefix=head[:pattern_from],
        pattern=pattern,
        where=head[pattern_to:],
        return_body=body,
        tail=tail,
        groups=groups,
    )


def _wanted(parsed: _Query) -> Optional[List[_Group]]:
    """
    The pattern groups the RETURN clause asks for, in the order it asks for them.

    ``*`` means every variable the pattern bound. A list means those variables and
    only if every one of them was bound here -- a name from somewhere else is a
    query this cannot account for.
    """
    named = [group for group in parsed.groups if group.variable]
    if parsed.return_body == "*":
        return named or None

    wanted: List[_Group] = []
    for part in _split_commas(parsed.return_body):
        name = part.strip()
        if not _BARE_VAR.match(name):
            return None
        found = next((group for group in named if group.variable == name), None)
        if found is None:
            return None
        wanted.append(found)
    return wanted or None


def _split_commas(text: str) -> List[str]:
    """Split on commas at the top bracket depth."""
    parts: List[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


#: A node or relationship whose whole content is one quoted label: ``n0:`Document```.
#: Deliberately this narrow -- it is exactly what a search builder emits, and a
#: group holding anything else (a property map, a second label, an alternation) is
#: one this module should not be taking apart.
_QUOTED_LABEL_ONLY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)?\s*:\s*`([^`]*)`\s*$")

#: The WHERE keyword at the head of a clause, for lifting the condition out of it.
_LEADING_WHERE = re.compile(r"^\s*WHERE\s+", re.IGNORECASE)


def unquote_identifiers(query: str) -> str:
    """
    Backticked names, unquoted wherever that leaves the same name.

    Textual on purpose: quoting is not confined to the shapes this module can
    parse, and a statement it will not otherwise touch still deserves to lose
    backticks the engine would only reject. String literals are stepped over
    rather than scanned, so a backtick inside ``'a`b'`` stays where it is.

    A name that is *not* a bare identifier keeps its quotes. Unquoting
    ```Odd Label``` yields ``Odd Label``, which is not the same name and not even
    parseable -- ``move_unquotable_labels`` is what handles those, and leaving the
    backticks in place is what lets it recognise them.
    """
    out: List[str] = []
    index = 0
    quote = ""

    while index < len(query):
        char = query[index]
        if quote:
            if char == "\\":
                out.append(query[index : index + 2])
                index += 2
                continue
            if char == quote:
                quote = ""
            out.append(char)
            index += 1
            continue
        if char in "'\"":
            quote = char
            out.append(char)
            index += 1
            continue
        if char == "`":
            close = query.find("`", index + 1)
            if close == -1:
                # Unbalanced. Nothing good comes of guessing where it ended.
                out.append(query[index:])
                break
            name = query[index + 1 : close]
            out.append(name if _BARE_VAR.match(name) else query[index : close + 1])
            index = close + 1
            continue
        out.append(char)
        index += 1

    return "".join(out)


def move_unquotable_labels(query: str) -> str:
    """
    Labels and types a LatticeDB pattern cannot carry, moved into a predicate.

    ``(n0:`Order Item`)`` has no spelling in this grammar: quoted it is an invalid
    token, unquoted it ends the node pattern early. The name is only expressible as
    a value, so it becomes ``"Order Item" IN labels(n0)`` and the pattern keeps just
    the variable -- exactly what the dialect emits when it meets the same name, via
    the same ``label_predicate`` and the same ``pattern_identifier_ok``.

    An existing WHERE is parenthesised before the new predicates are joined to it:
    ``a OR b`` conjoined bare would bind the wrong way round.
    """
    parsed = _split(query)
    if parsed is None:
        return query

    edits: List[Tuple[int, int, str]] = []
    predicates: List[str] = []

    for group in parsed.groups:
        found = _QUOTED_LABEL_ONLY.match(group.inner)
        if found is None:
            continue
        variable, name = found.group(1), found.group(2)
        if LATTICEDB_DIALECT.pattern_identifier_ok(name):
            continue  # the quoting was decoration; unquote_identifiers has it
        if not variable:
            # Nothing to hang a predicate on, and inventing a variable would mean
            # editing what the caller asked to match.
            return query
        if group.kind == NODE:
            if LATTICEDB_DIALECT.label_predicate is None:  # pragma: no cover
                return query
            predicates.append(LATTICEDB_DIALECT.label_predicate(variable, name))
            edits.append((group.start, group.end, f"({variable})"))
        else:
            if LATTICEDB_DIALECT.rel_type_expr is None:  # pragma: no cover
                return query
            listed = f"[{cypher_string(name)}]"
            predicates.append(f"{LATTICEDB_DIALECT.rel_type_expr(variable)} IN {listed}")
            edits.append((group.start, group.end, f"[{variable}]"))

    if not predicates:
        return query

    pattern = parsed.pattern
    for start, end, text in reversed(edits):
        pattern = pattern[:start] + text + pattern[end:]

    condition = _LEADING_WHERE.sub("", parsed.where).strip()
    joined = " AND ".join(([f"({condition})"] if condition else []) + predicates)
    where = f"{LATTICEDB_DIALECT.where_keyword} {joined}"
    tail = f" {parsed.tail.strip()}" if parsed.tail.strip() else ""

    return f"{parsed.prefix}{pattern.rstrip()} {where} RETURN {parsed.return_body}{tail}"


def prepare_statement(query: str) -> str:
    """
    Everything that has to happen to a caller's statement before the engine sees it.

    Ordered, because each step depends on the last leaving its evidence behind.
    The label move runs first: it recognises an unquotable name *by* its backticks,
    which the unquoting step would otherwise have already removed or left in a state
    it no longer reads. Unquoting then clears what is left, and the projection
    rewrite works on a statement that finally parses.
    """
    return rewrite_for_projection(unquote_identifiers(move_unquotable_labels(query)))


def rewrite_for_projection(query: str) -> str:
    """
    ``query`` with its RETURN replaced by a projection, when that is safe.

    Returns the query unchanged whenever anything at all is unclear. The head --
    the MATCH pattern and any WHERE -- and the tail -- ORDER BY, SKIP, LIMIT -- are
    kept exactly as written; only the RETURN body is replaced.
    """
    parsed = _split(query)
    if parsed is None:
        return query

    wanted = _wanted(parsed)
    if wanted is None:
        return query

    entries: List[Tuple[str, str, Optional[Tuple[str, str, str]]]] = []
    for group in wanted:
        assert group.variable is not None  # _wanted only returns named groups
        if group.kind == NODE:
            entries.append((group.variable, NODE, None))
            continue
        ends = _endpoints_of(parsed.pattern, parsed.groups, parsed.groups.index(group))
        if ends is None:
            return query
        entries.append((group.variable, RELATIONSHIP, (group.variable, ends[0], ends[1])))

    projection = project_variables(LATTICEDB_DIALECT, entries)
    tail = f" {parsed.tail.strip()}" if parsed.tail.strip() else ""
    return f"{parsed.head.rstrip()} {projection}{tail}"


#: What LatticeDB says about a ``*`` it cannot parse. Matched loosely: the wording
#: is the engine's and may change, and being wrong here only costs a hint.
_PARSE_ERROR = re.compile(r"expected expression", re.IGNORECASE)

_STAR_RETURN = re.compile(r"\bRETURN\b[^;]*?\*", re.IGNORECASE | re.DOTALL)

_STAR_ADVICE = (
    "LatticeDB has no * in its grammar -- neither RETURN * nor count(*). There is "
    "no node or relationship value for a bare name to stand for, "
    "so a statement must name the parts it wants, for example: "
    "RETURN id(n), labels(n), properties(n), id(r), type(r), properties(r), "
    "id(n) AS r_src, id(m) AS r_dst, id(m), labels(m), properties(m). "
    "The proxy writes that for you when the query is a single MATCH over a "
    "directed pattern; this one it could not read well enough to rewrite."
)


#: What LatticeDB says about a backtick, anywhere.
_INVALID_TOKEN = re.compile(r"invalid token", re.IGNORECASE)

_QUOTING_ADVICE = (
    "LatticeDB has no quoted-identifier syntax: a backtick is an invalid token "
    "wherever it appears, and there is nothing to escape into instead -- double "
    "quotes and brackets are rejected in those positions too. The proxy takes the "
    "backticks off a name that is already a bare identifier, and moves a label or "
    "type that is not -- one with a space in it -- into a WHERE predicate such as "
    "\"Order Item\" IN labels(n0). One is left here that it could not place."
)


def explain(query: str, error: str) -> str:
    """
    The engine's error with the reason attached, when this module knows it.

    ``Expected expression`` and ``Invalid token`` each point at nothing on their
    own -- the statement looks fine and the character objected to is not named --
    and no one is going to guess that this engine has neither ``*`` nor a quoted
    identifier. Only a statement that actually holds the thing complained about is
    annotated, so an unrelated syntax error is passed through as it came.
    """
    if _PARSE_ERROR.search(error) and _STAR_RETURN.search(query):
        return f"{error}. {_STAR_ADVICE}"
    if _INVALID_TOKEN.search(error) and "`" in query:
        return f"{error}. {_QUOTING_ADVICE}"
    return error
