"""
The ``MATCH`` pattern: node groups and the arrows between them, into ``VertexPat``.

Scanning the text is ``translate_text``'s job; this is what a group *means* — an
optional variable and label, the direction a connector carries, the types in a
relationship alternation — and what it refuses: inline property constraints,
variable-length paths, comma-separated patterns, a group that is not joined to the
next one by an arrow.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from . import predicate
from .ir import EdgePat, VertexPat
from .translate_text import (
    TranslateError,
    _QUOTES,
    _bracket_body,
    _index_outside,
    _skip_quoted,
    _split_once,
    _split_outside,
)


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
