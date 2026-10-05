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

The grammar is split by job, and this file is the front-end that reads one whole
statement: ``translate_text`` scans the text, ``translate_pattern`` reads the
``MATCH`` pattern, ``translate_return`` reads the ``RETURN``, and every name below
is re-exported here so ``from .translate import ...`` keeps resolving.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from . import predicate
from .ir import EdgePat, MatchPlan, VertexPat
from .translate_pattern import (  # noqa: F401  (re-exported: this module's names, unchanged)
    _edge_direction,
    _parse_edge,
    _parse_node,
    _parse_pattern,
    _scan_groups,
)
from .translate_return import (  # noqa: F401
    AGGREGATES,
    AggItem,
    PropItem,
    ReturnSpec,
    _DISTINCT,
    _NAME,
    _agg_alias,
    _is_name,
    _parse_agg,
    _property_pair,
    _return_spec,
    parse_return,
)
from .translate_text import (  # noqa: F401
    _ENDS_WORD,
    _OPERATOR_WITH,
    _QUOTES,
    _REFUSED_HEADS,
    _SUPPORTED_HEADS,
    TranslateError,
    _bracket_body,
    _index_outside,
    _skip_quoted,
    _split_on_word,
    _split_once,
    _split_outside,
    _stands_alone,
    clause_heads,
    refusal_phrase,
    split_clauses,
    strip_comments,
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
