"""
The ``RETURN`` list: what a statement asks back, which decides the shape of the answer.

``PropItem`` / ``AggItem`` / ``ReturnSpec`` and the parse that fills them, plus the
two tables only this layer reads — ``AGGREGATES`` and the ``DISTINCT`` / identifier
regexes. A bare variable or ``*`` stands for whole entities, ``var.prop`` items are
columns, and an aggregate is one row per group; anything else is refused by name,
because a shape read as the wrong one costs a person an empty canvas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from . import predicate
from .translate_text import (
    TranslateError,
    _bracket_body,
    _index_outside,
    _split_on_word,
    _split_outside,
)

#: The functions a ``RETURN`` may aggregate with. The list ``query.classify`` reads the
#: kind of, and the one ``plan.py`` / ``compile.py`` have to answer.
AGGREGATES = ("count", "sum", "avg", "min", "max")

_DISTINCT = re.compile(r"^DISTINCT\b\s*(.*)$", re.IGNORECASE | re.DOTALL)
_NAME = re.compile(r"^[A-Za-z_][\w ]*$")


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
