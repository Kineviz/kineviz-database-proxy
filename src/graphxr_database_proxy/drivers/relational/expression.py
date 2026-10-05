"""
The predicate AST as a SQLAlchemy expression, given the mapping and the engine.

The other half of what the reference bridge did inside its parser: ``predicate.py``
stopped at the tree precisely so that every SQL-shaped decision could be taken here,
with the mapping and the engine spelling both in the room. Nothing is concatenated:
identifiers come from the mapping through ``Table`` objects, and every value leaves
through a bind parameter typed as the column it is compared against.

Why the typing matters is engine truth rather than style. pyodbc sends every Python
string as ``nvarchar``, and an ``nvarchar`` compared against a ``varchar`` column makes
SQL Server convert *the column*: an index lookup becomes a full scan with nothing in the
output to say so. A date key reaching a ``date`` column as quoted text is worse — mssql
and postgresql answer it with a type error, not a match.

Three duties the front end deliberately leaves here:

* ``Lit`` values are bound, never pasted, and each is bound as its column's own type
  (``ids.coerce_value`` does the reading, so a float-rendered ``1.0`` lands on an
  integer column as ``1``);
* a list of keys is sized against the engine's own ceiling and goes out as ``IN``, as
  one bulk parameter, or as a fan-out the caller has to run (``fanout_slots``);
* case folding is not recoverable from the AST — ``toLower(n.x) = 'abc'`` *is*
  ``n.x = 'abc'`` here — so anything that knows about collations is read from
  ``EngineSpelling``, not guessed from the tree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import sqlalchemy as sa
from sqlalchemy import and_, bindparam, cast, false, literal_column, or_, select, text, true
from sqlalchemy.sql.elements import BindParameter, ColumnElement
from sqlalchemy.types import TypeEngine

from . import ids
from .engine import EngineSpelling, ServerInfo, cast_sqlalchemy_type
from .predicate import (
    And,
    BoolProp,
    Cmp,
    EntityFn,
    Expression,
    IdIn,
    InList,
    IsNull,
    Lit,
    Not,
    Operand,
    Or,
    Prop,
    StrOp,
)

#: The three spellings of "this variable is one of my pattern's entities".
VERTEX_KIND = "vertex"
EDGE_KIND = "edge"

#: ``OPENJSON``'s ``WITH`` clause and ``JSON_TABLE``'s ``COLUMNS`` clause both have to
#: declare the type they shred into, or the comparison converts the column instead of
#: the value and the index is gone; that declaration is the engine's own answer
#: (``EngineSpelling.shredded_column_type``), never a type name hardcoded here.
#: What each table function then exposes travels with the source that declares it:
#: ``OPENJSON``'s ``WITH`` names a bare column, ``JSON_TABLE`` needs the alias its
#: ``AS`` gives the row source in front of its name.
_BULK_PROJECTION_OPENJSON = "[value]"
_BULK_PROJECTION_JSON_TABLE = "gx_jt.gx_value"

class ExpressionError(Exception):
    """A predicate this compiler cannot turn into SQL. Names what it found."""


class UnknownVariable(ExpressionError):
    """The predicate names a variable the plan never bound."""


class UnknownProperty(ExpressionError):
    """
    The predicate names a property that variable's table does not have.

    Refused rather than guessed, and the guess that has to be refused is the tempting
    one: ``n.name`` against a table whose only name-ish column is ``full_name`` would
    otherwise quietly compare against something else, or against nothing at all.
    """


class UnsupportedPredicate(ExpressionError):
    """A construct with no answer here, named instead of approximated."""


class FanoutRequired(ExpressionError):
    """A value list this server cannot carry in one statement, and how to carry it."""


@dataclass(frozen=True)
class BoundVariable:
    """
    One pattern variable, resolved to the alias its branch uses.

    ``columns`` is the whole of what the predicate is allowed to name: the key column,
    the declared properties, and for an edge the two join columns, which are columns of
    this table even though the mapping does not expose them as properties.
    """

    var: str
    entity: str
    kind: str
    columns: Mapping[str, ColumnElement]
    types: Mapping[str, str]
    key_prop: str


class PredicateScope:
    """
    What a predicate may name, what it binds as, and what it has collected so far.

    One scope per statement: the bind names have to be unique inside a statement and the
    parameter dict is returned with it, so a scope is never shared across branches.
    """

    def __init__(
        self,
        variables: Sequence[BoundVariable],
        *,
        spelling: EngineSpelling,
        server: Optional[ServerInfo] = None,
    ) -> None:
        self.spelling = spelling
        # An unprobed server takes the conservative route: mssql fans a long list out
        # rather than assuming OPENJSON exists. Correct-but-cheaper beats wrong.
        self.server = server if server is not None else ServerInfo(engine=spelling.name)
        self.by_var: Dict[str, BoundVariable] = {variable.var: variable for variable in variables}
        self.parameters: Dict[str, object] = {}
        self._counter = 0

    def new_name(self, prefix: str = "gx_p") -> str:
        self._counter += 1
        return "%s%d" % (prefix, self._counter)

    def put(self, name: str, value: object) -> None:
        """Record one bound value, so the caller can pass the dict to ``execute``."""
        self.parameters[name] = value

    def bind(self, value: object, declared_type: Optional[str], prefix: str = "gx_p") -> BindParameter:
        """One value, read as its column's type and bound as a parameter of that type."""
        name = self.new_name(prefix)
        coerced = ids.coerce_value(value, declared_type)
        self.put(name, coerced)
        return bindparam(name, value=coerced, type_=self.spelling.column_type(declared_type))

    def resolve(self, var: str, prop: str) -> Tuple[ColumnElement, str]:
        """The column one ``var.prop`` names, with the declared type it was declared as."""
        bound = self.by_var.get(var)
        if bound is None:
            raise UnknownVariable(
                "the predicate names %r, which this plan does not bind (the variables are %s)"
                % (var, ", ".join(sorted(self.by_var)) or "none")
            )
        column = bound.columns.get(prop)
        if column is None:
            raise UnknownProperty(
                "%s.%s is not a property of %r: %s has %s"
                % (var, prop, bound.entity, bound.entity, _names(bound.columns))
            )
        return column, bound.types.get(prop, "")

    def entity_of(self, var: str) -> BoundVariable:
        bound = self.by_var.get(var)
        if bound is None:
            raise UnknownVariable("the predicate names %r, which this plan does not bind" % var)
        return bound


def _names(columns: Mapping[str, object]) -> str:
    keys = sorted(str(name) for name in columns)
    if not keys:
        return "no columns at all"
    return ", ".join(keys[:12]) + (", ..." if len(keys) > 12 else "")


def compile_predicate(expr: Optional[Expression], scope: PredicateScope) -> Optional[ColumnElement]:
    """
    The WHERE tree as one SQLAlchemy condition, or None when there was no predicate.

    Raises :class:`ExpressionError` for a name the plan does not bind, a property the
    table does not have, a regex on an engine without one, or a value list that has to
    be fanned out first.
    """
    if expr is None:
        return None
    if isinstance(expr, IdIn):
        raise UnsupportedPredicate(
            "ID(n) IN [...] cannot be compiled: these backends have no node-identity function, "
            "so a seed is re-selected by primary key (n.`<key>` IN [...])"
        )
    if isinstance(expr, And):
        parts = [compile_predicate(part, scope) for part in expr.parts]
        kept = [part for part in parts if part is not None]
        return and_(*kept) if kept else true()
    if isinstance(expr, Or):
        parts = [compile_predicate(part, scope) for part in expr.parts if part is not None]
        return or_(*parts) if parts else false()
    if isinstance(expr, Not):
        inner = compile_predicate(expr.expr, scope)
        return sa.not_(inner) if inner is not None else false()
    if isinstance(expr, Cmp):
        return _comparison(expr, scope)
    if isinstance(expr, StrOp):
        return _string_op(expr, scope)
    if isinstance(expr, InList):
        return _in_list(expr, scope)
    if isinstance(expr, IsNull):
        column, _ = _column(expr.lhs, scope)
        return column.isnot(None) if expr.negated else column.is_(None)
    if isinstance(expr, BoolProp):
        return _bool_prop(expr, scope)
    if isinstance(expr, EntityFn):
        raise UnsupportedPredicate(
            "%s(%s) on its own is not a condition: compare it with something" % (expr.func, expr.var)
        )
    raise UnsupportedPredicate("unsupported predicate: %r" % (expr,))


def _column(operand: Operand, scope: PredicateScope) -> Tuple[ColumnElement, str]:
    if isinstance(operand, Prop):
        if not operand.prop:
            raise UnsupportedPredicate(
                "%s alone is not a column: name one of its properties" % operand.var
            )
        return scope.resolve(operand.var, operand.prop)
    if isinstance(operand, EntityFn):
        raise UnsupportedPredicate("%s(%s) is a value this branch already knows, not a column" % (operand.func, operand.var))
    raise UnsupportedPredicate("a literal is not a column: %r" % (getattr(operand, "value", operand),))


def _comparison(expr: Cmp, scope: PredicateScope) -> ColumnElement:
    lhs, rhs = expr.lhs, expr.rhs
    if isinstance(lhs, EntityFn) or isinstance(rhs, EntityFn):
        return _entity_comparison(lhs, expr.op, rhs, scope)
    left, left_type = _side(lhs, rhs, scope)
    right, _ = _side(rhs, lhs, scope)
    return left.op(expr.op)(right)


def _side(operand: Operand, other: Operand, scope: PredicateScope) -> Tuple[ColumnElement, str]:
    """
    One side of a comparison, typed by the other side when the other side is a column.

    ``n.a = 1`` and ``1 = n.a`` have to compile the same way, and the type that decides
    the bind is whichever half names a column.
    """
    if isinstance(operand, Prop):
        column, declared = scope.resolve(operand.var, operand.prop or "")
        return column, declared
    if isinstance(operand, Lit):
        declared = ""
        if isinstance(other, Prop) and other.prop:
            declared = scope.resolve(other.var, other.prop)[1]
        return scope.bind(operand.value, declared or None), declared
    column, declared = _column(operand, scope)
    return column, declared


def _entity_value(entity: EntityFn, scope: PredicateScope) -> str:
    """
    What ``type(r)`` / ``labels(n)`` means for this branch.

    A relational table *is* the label, and one branch binds exactly one label per
    variable and exactly one type per edge, so the function is a constant here. The
    branch's own name is compared exactly as the mapping declares it: the client
    generates these predicates from the schema it read from this same mapping.
    """
    bound = scope.entity_of(entity.var)
    func_name = entity.func
    if func_name == "type":
        if bound.kind != EDGE_KIND:
            raise UnsupportedPredicate(
                "type(%s) names a relationship type, but %s is a node in this pattern" % (entity.var, entity.var)
            )
        return bound.entity
    if func_name in ("label", "labels"):
        if bound.kind != VERTEX_KIND:
            raise UnsupportedPredicate(
                "%s(%s) names a node label, but %s is a relationship in this pattern"
                % (func_name, entity.var, entity.var)
            )
        return bound.entity
    raise UnsupportedPredicate(
        "%s(%s) has no answer here: these backends have no node-identity function" % (func_name, entity.var)
    )


def _entity_comparison(lhs: Operand, op: str, rhs: Operand, scope: PredicateScope) -> ColumnElement:
    """
    ``type(r) = 'X'`` reduced to ``true()`` or ``false()``.

    There is no type column to compare against, and inventing one would be a second
    answer to a question the branch already settled. The consequence is worth stating
    because it is the useful one: the client's hidden-type filter
    ``NOT(type(r) IN [...])`` collapses the branch it excludes, which is exactly the
    ``relationshipTypeFilter`` the capability record promises.
    """
    entity = lhs if isinstance(lhs, EntityFn) else rhs
    other = rhs if isinstance(lhs, EntityFn) else lhs
    value = _entity_value(entity, scope)
    if isinstance(other, Lit):
        answer = _compare_values(value, op if other is rhs else _mirror(op), other.value)
        return true() if answer else false()
    column, declared = _column(other, scope)
    return column.op(op)(scope.bind(value, declared or None))


def _mirror(op: str) -> str:
    """The same operator with its halves swapped."""
    return {"<": ">", ">": "<", "<=": ">=", ">=": "<="}.get(op, op)


def _compare_values(left: str, op: str, right: object) -> bool:
    """One comparison of a branch's own name against a literal, in Python."""
    if right is None:
        return False
    text = str(right)
    if op in ("=", "=="):
        return left == text
    if op in ("<>", "!="):
        return left != text
    if op == "<":
        return left < text
    if op == ">":
        return left > text
    if op == "<=":
        return left <= text
    if op == ">=":
        return left >= text
    raise UnsupportedPredicate("a label or type cannot be compared with %r" % op)


def _string_op(expr: StrOp, scope: PredicateScope) -> ColumnElement:
    column, declared = _column(expr.lhs, scope)
    value = expr.value
    # A non-character column has to become text before it can be matched against text,
    # and only the engine knows which columns those are: wrapping an indexed ``varchar``
    # in a CAST is the same index loss a wrongly typed bind costs.
    if declared:
        cast_type = cast_from_name(scope.spelling.like_cast(declared))
        if cast_type is not None:
            column = cast(column, cast_type)
    if expr.op == "regex":
        return _regex(column, value, scope)
    if not isinstance(value, str):
        raise UnsupportedPredicate(
            "%s needs a string to look for, got %r" % (expr.op, type(value).__name__)
        )
    # ``autoescape`` is the whole reason this is SQLAlchemy's problem and not ours: a
    # value containing a wildcard must not become a wildcard. The bridge annotated its
    # own ``like_pattern`` "not cosmetic" because CONTAINS '50%' turned into
    # LIKE '%50%%', which matches anything holding "50".
    if expr.op == "contains":
        return column.contains(value, autoescape=True)
    if expr.op == "startswith":
        return column.startswith(value, autoescape=True)
    if expr.op == "endswith":
        return column.endswith(value, autoescape=True)
    raise UnsupportedPredicate("unsupported text comparison: %r" % expr.op)


def _regex(column: ColumnElement, value: object, scope: PredicateScope) -> ColumnElement:
    """
    ``=~`` on an engine that has an operator for it.

    ``regexp_match`` is SQLAlchemy's own: it renders ``~`` on PostgreSQL and ``REGEXP``
    on MySQL, and on SQL Server it raises at compile time — so the two guards line up
    instead of one of them having to know the other's operator token. The refusal for
    mssql is ``query.REGEX_REFUSAL``'s job, so a person normally hears about it before
    anything runs; reaching here on that engine means the caller compiled against the
    wrong spelling, and it still refuses rather than matching nothing.
    """
    if not scope.spelling.supports_regex:
        raise UnsupportedPredicate(
            "regular-expression matches (=~) is refused: %s has no regular-expression operator. "
            "Use CONTAINS, STARTS WITH or ENDS WITH instead." % scope.spelling.name
        )
    if not isinstance(value, str):
        raise UnsupportedPredicate("a regular expression must be a string, got %r" % (value,))
    return column.regexp_match(scope.bind(value, None))


def _bool_prop(expr: BoolProp, scope: PredicateScope) -> ColumnElement:
    """
    ``WHERE n.isfraud``: a boolean property, compared against true.

    Bound as the column's own type rather than emitted as ``= 1``, so ``bit`` on SQL
    Server, ``bool`` on PostgreSQL and ``tinyint(1)`` on MySQL each get a value they
    actually hold. A non-boolean column answered this way comes back as the server's
    own type error, which names the column; guessing "truthy means not null" would
    answer a question nobody asked.
    """
    column, declared = _column(expr.lhs, scope)
    return column == scope.bind(True, declared or None)


def _in_list(expr: InList, scope: PredicateScope) -> ColumnElement:
    if isinstance(expr.lhs, EntityFn):
        value = _entity_value(expr.lhs, scope)
        hit = any(value == str(item) for item in expr.values if item is not None)
        return (false() if expr.negated else true()) if hit else (true() if expr.negated else false())
    column, declared = _column(expr.lhs, scope)
    values = list(expr.values)
    if not values:
        # Cypher's ``x IN []`` is false and ``NOT (x IN [])`` is true, and both are a
        # constant: no SQL ``IN ()``, which is a syntax error on all three engines.
        return false() if not expr.negated else true()
    condition = _membership(column, values, declared, scope)
    return sa.not_(condition) if expr.negated else condition


def _membership(column: ColumnElement, values: Sequence[object], declared_type: str, scope: PredicateScope) -> ColumnElement:
    """
    ``column IN (...)`` for a list of any size, the way this server wants it.

    The choice is the engine's (``EngineSpelling.id_list_strategy``) and never a
    guess here: SQL Server caps a statement at 2 100 parameters, PostgreSQL carries a
    whole selection in one array parameter, and MySQL shreds one JSON document through
    ``JSON_TABLE`` only from 8.0.4.
    """
    strategy = scope.spelling.id_list_strategy(scope.server, len(values))
    if strategy == "bulk":
        return _bulk_membership(column, values, declared_type, scope)
    if strategy == "fanout":
        raise FanoutRequired(
            "a list of %d values cannot be carried by %s in one statement; run one statement per "
            "chunk of %d (plan the chunks with fanout_slots and substitute them with replace_slot)."
            % (len(values), scope.spelling.name, max(1, scope.spelling.fanout_chunk_size(scope.server)))
        )
    binds = [scope.bind(value, declared_type or None) for value in values]
    return column.in_(binds)


def _bulk_membership(column: ColumnElement, values: Sequence[object], declared_type: str, scope: PredicateScope) -> ColumnElement:
    form = scope.spelling.bulk_id_form(scope.server)
    if form == "any":
        from sqlalchemy.dialects.postgresql import ARRAY

        element = scope.spelling.column_type(declared_type or None)
        name = scope.new_name("gx_any")
        coerced = [ids.coerce_value(value, declared_type or None) for value in values]
        scope.put(name, coerced)
        param = bindparam(name, value=coerced, type_=ARRAY(element))
        return column == sa.any_(param)
    if form == "openjson":
        return _shred(
            column, values, declared_type, scope, _openjson_source,
            _BULK_PROJECTION_OPENJSON, "gx_json",
        )
    if form == "json_table":
        return _shred(
            column, values, declared_type, scope, _json_table_source,
            _BULK_PROJECTION_JSON_TABLE, "gx_json",
        )
    # ``placeholders`` is what an engine says when its bulk form is a plain IN; the
    # size that reached here is the engine's own call, so honour it.
    binds = [scope.bind(value, declared_type or None) for value in values]
    return column.in_(binds)


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _openjson_source(name: str, value_type: str) -> str:
    """
    One JSON document, shredded with the engine's declared column type.

    The ``WITH`` clause is not decoration: without it ``OPENJSON`` answers ``nvarchar``
    and the comparison converts the column, which is the same index loss a wrongly
    typed bind costs.
    """
    return "OPENJSON(:%s) WITH ([value] %s '$')" % (name, value_type)


def _json_table_source(name: str, value_type: str) -> str:
    """``JSON_TABLE`` for the same one-parameter list on MySQL 8.0.4 and later."""
    return (
        "JSON_TABLE(:%s, '$[*]' COLUMNS (gx_value %s PATH '$')) AS gx_jt"
        % (name, value_type)
    )


def _shred(
    column: ColumnElement,
    values: Sequence[object],
    declared_type: str,
    scope: PredicateScope,
    source: object,
    projection: str,
    prefix: str,
) -> ColumnElement:
    """
    The list as one document, shredded by the engine's own table function.

    The parameter inside the ``text()`` is the same named bind the collector made, so
    one value crosses the wire once and with a type attached: an untyped document would
    arrive as whatever the DBAPI guessed, which is the conversion the ``WITH`` /
    ``COLUMNS`` clause exists to prevent. Both names that clause needs — the document's
    type and the shredded column's — are the engine's own answers
    (``json_document_type``, ``shredded_column_type``), never names read off it here.
    """
    name = scope.new_name(prefix)
    document = json.dumps([_jsonable(value) for value in values], separators=(",", ":"))
    document_type = scope.spelling.json_document_type()
    value_type = scope.spelling.shredded_column_type(declared_type)
    scope.put(name, document)
    param = bindparam(name, value=document, type_=scope.spelling.column_type(document_type))
    inner = select(literal_column(projection)).select_from(
        text(str(source(name, value_type))).bindparams(param)
    )
    return column.in_(inner)


# ----- fan-out ---------------------------------------------------------------


@dataclass(frozen=True)
class FanoutSlot:
    """
    One value list too big for this server, and the chunks it has to be cut into.

    ``index`` is the list's position in a depth-first walk of the predicate, which is
    how :func:`replace_slot` finds the same list again: the tree is frozen dataclasses,
    so identity survives one walk and nothing else does.
    """

    index: int
    values: Tuple[object, ...]
    chunk_size: int
    column: str

    def chunks(self) -> List[List[object]]:
        size = max(1, int(self.chunk_size))
        return [list(self.values[start : start + size]) for start in range(0, len(self.values), size)]


def _in_lists(expr: Optional[Expression]) -> List[InList]:
    """Every key-list ``IN`` in the tree, in walk order."""
    out: List[InList] = []
    _collect(expr, out)
    return out


def _collect(expr: Optional[Expression], out: List[InList]) -> None:
    if expr is None:
        return
    if isinstance(expr, InList) and isinstance(expr.lhs, Prop):
        out.append(expr)
        return
    if isinstance(expr, (And, Or)):
        for part in expr.parts:
            _collect(part, out)
    elif isinstance(expr, Not):
        _collect(expr.expr, out)


def fanout_slots(expr: Optional[Expression], scope: PredicateScope) -> List[FanoutSlot]:
    """
    The lists in ``expr`` this server cannot carry in one statement, as chunks.

    Empty means one statement is enough, which is the ordinary case: the ceilings are
    in the low thousands and a hand-typed predicate is nowhere near them. A pull or an
    expand over a large selection is where this trips, and the caller then runs one
    statement per chunk and merges — which is why paging *across* branches is only
    best-effort and each branch trims on output.
    """
    slots: List[FanoutSlot] = []
    for index, node in enumerate(_in_lists(expr)):
        if node.lhs is None or not isinstance(node.lhs, Prop) or not node.lhs.prop:
            continue
        column, declared = scope.resolve(node.lhs.var, node.lhs.prop)
        if scope.spelling.id_list_strategy(scope.server, len(node.values)) != "fanout":
            continue
        size = max(1, int(scope.spelling.fanout_chunk_size(scope.server)))
        slots.append(FanoutSlot(index=index, values=tuple(node.values), chunk_size=size, column="%s.%s" % (node.lhs.var, node.lhs.prop)))
    return slots


def replace_slot(expr: Optional[Expression], index: int, values: Sequence[object]) -> Optional[Expression]:
    """
    The predicate with the ``index``-th key list narrowed to ``values``.

    A frozen rewrite rather than a mutation, because the same plan is compiled once per
    chunk and a shared tree would let one chunk's values appear in another's SQL.
    """
    counter = [0]

    def rebuild(node: Optional[Expression]) -> Optional[Expression]:
        if node is None:
            return None
        if isinstance(node, InList) and isinstance(node.lhs, Prop):
            here = counter[0]
            counter[0] += 1
            if here == index:
                return InList(lhs=node.lhs, values=list(values), negated=node.negated)
            return node
        if isinstance(node, And):
            return And([rebuild(part) for part in node.parts])  # type: ignore[list-item]
        if isinstance(node, Or):
            return Or([rebuild(part) for part in node.parts])  # type: ignore[list-item]
        if isinstance(node, Not):
            return Not(rebuild(node.expr))  # type: ignore[arg-type]
        return node

    return rebuild(expr)


def cast_from_name(name: Optional[str]) -> Optional[TypeEngine]:
    """
    A SQL type name the engine asked for, as a SQLAlchemy type.

    The grammar — the exact numerics *and* the character narrowing, since an unbounded
    LOB cannot be a ``GROUP BY`` key at all and the size the index can hold is the size
    the cast gets — is one function's, :func:`engine.cast_sqlalchemy_type`, because the
    names it reads are that same seam's answer. This is the compiler's import and owns
    nothing: a second table of type names is what drifts when the next engine is added.
    """
    if not name:
        return None
    return cast_sqlalchemy_type(str(name).strip())
