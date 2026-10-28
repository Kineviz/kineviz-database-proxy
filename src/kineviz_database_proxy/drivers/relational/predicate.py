"""
The ``WHERE`` predicate grammar: Cypher boolean text in, an expression tree out.

Ported from ``ibase_bridge/predicate.py`` (MIT), and the AST is the whole product
here. The reference's second half — ``SqlCtx``, ``to_sql``, ``_in_list_sql`` and the
``from . import tsql`` that pulled a dialect in mid-compile — is deliberately gone,
together with the ``if ctx.dialect is None`` branches that quietly meant "assume
PostgreSQL and emit ``%s`` / ``ILIKE`` / ``::text``". Emitting SQL from the parser is
what made that file one-engine: it could say ``col IN (...)`` but not that SQL Server
caps a statement at 2 100 parameters, so the ceiling had to be smuggled in through a
dialect object the grammar did not otherwise need. Here the grammar stops at the tree
and ``expression.py`` / ``compile.py`` own every SQL-shaped decision, with the mapping
and the engine both in the room.

Grammar (low to high precedence), unchanged from the reference:

    or      := and   ( OR  and  )*
    and     := not   ( AND not  )*
    not     := NOT not | pred
    pred    := primary [ cmp | str-op | [NOT] IN list | IS [NOT] NULL ]   (else bare bool)
    primary := '(' or ')' | operand
    operand := case-fn '(' operand ')' | entity-fn '(' name ')' | literal | name

Leaf predicates: ``= <> != < > <= >=``, ``CONTAINS`` / ``STARTS WITH`` / ``ENDS WITH``,
``=~``, ``IN [...]`` (also ``IN UNNEST([...])``, also negated), ``IS [NOT] NULL``, a bare
boolean property (``WHERE n.isfraud``) and ``id(n) IN [internal_id(t, o), ...]``.
``toLower`` / ``toUpper`` / ``lower`` / ``upper`` are folded (see ``_fold``).

Identifier quoting is this repo's, not the bridge's: ``dialect.backtick`` wraps every
label, relationship type and property name in backticks and doubles a backtick inside
one, so a warehouse column called ``person id``, ``Order`` or ``update`` survives.
String literals arrive double-quoted through ``json.dumps``, so a ``"``-delimited run is
decoded as JSON and a ``'``-delimited one as Cypher escapes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple, Union

# ----- AST -----


@dataclass(frozen=True)
class Prop:
    """``n.name``, ``n.`person id``` — one property of one pattern variable.

    ``prop`` is None only for a bare variable, which no predicate accepts; the parser
    refuses that rather than letting a name with no property reach the compiler.
    """

    var: str
    prop: Optional[str] = None


@dataclass(frozen=True)
class Lit:
    """A literal, already unquoted: str, int, float, bool or None."""

    value: object


@dataclass(frozen=True)
class EntityFn:
    """
    A per-entity function the dialect emits where a value can go: ``type(r)``,
    ``label(n)``, ``id(n)``.

    The reference had a node for one case only (``id(n)``, transient, folded into
    ``IdIn``) because Kineviz never put a function anywhere else. This repo's
    ``StatementDialect`` does: ``rel_type_expr`` writes ``type(r)``, so a hidden
    relationship type is filtered by a *function* on an edge variable
    (``AND NOT(type(r) IN ["PERFORMS"])``). A leaf therefore has to say which function
    of which variable it means, and ``func`` is the lower-cased spelling as written.

    ``id`` and ``element_id`` name an identity these backends do not have, and
    ``translate.py`` refuses those; ``type`` / ``label`` / ``labels`` are answered by
    the plan, which knows the edge's type once it has branched.
    """

    var: str
    func: str


@dataclass(frozen=True)
class And:
    parts: List[Expression] = field(default_factory=list)


@dataclass(frozen=True)
class Or:
    parts: List[Expression] = field(default_factory=list)


@dataclass(frozen=True)
class Not:
    expr: Expression


@dataclass(frozen=True)
class Cmp:
    """A six-way comparison. ``<>`` and ``!=`` are the same operator, spelled ``<>``."""

    lhs: Operand
    op: str
    rhs: Operand


@dataclass(frozen=True)
class StrOp:
    lhs: Operand
    #: "contains" | "startswith" | "endswith" | "regex"
    op: str
    value: object


@dataclass(frozen=True)
class InList:
    lhs: Operand
    values: List[object] = field(default_factory=list)
    negated: bool = False


@dataclass(frozen=True)
class IsNull:
    lhs: Operand
    negated: bool = False


@dataclass(frozen=True)
class BoolProp:
    """``WHERE n.isfraud`` — a boolean property compared against nothing."""

    lhs: Operand


@dataclass(frozen=True)
class IdIn:
    """``id(n) IN [internal_id(t, o), ...]``: seeds named by engine identity."""

    var: str
    refs: List[Tuple[int, int]] = field(default_factory=list)
    negated: bool = False


#: Anything a leaf predicate can put where a value goes.
Operand = Union[Prop, Lit, EntityFn]
#: Anything the boolean grammar can produce.
Expression = Union[And, Or, Not, Cmp, StrOp, InList, IsNull, BoolProp, IdIn, EntityFn]
#: Every node ``walk`` can hand back, operands included.
Node = Union[Expression, Prop, Lit]

#: Functions the grammar folds into their argument, so case-insensitivity is a property
#: of the comparison and not of the tree.
CASE_FUNCS = frozenset({"tolower", "lower", "toupper", "upper"})
#: Functions that name a value an entity carries, and that ``translate`` will accept.
ENTITY_FUNCS = frozenset({"id", "element_id", "type", "label", "labels"})
#: The subset of ``ENTITY_FUNCS`` these backends cannot answer at all: a relational
#: engine has no node-identity function, so a seed is re-selected by primary key.
IDENTITY_FUNCS = frozenset({"id", "element_id"})


class ParseError(Exception):
    """The text is not a predicate this grammar reads. Always says what it found."""


# ----- tokenizer -----


#: One identifier: bare, or backtick-quoted with a doubled backtick as the escape —
#: which is exactly what ``dialect.backtick`` writes, so a column called ``person id``,
#: ``Order`` or ``Or`der`` is one token here and not a split.
_IDENT = r"(?:`(?:``|[^`])*`|[A-Za-z_]\w*)"
#: A dotted name, as far as a property access goes: ``n.name``, ``n.`person id```.
_NAME = r"{0}(?:\.{0})*".format(_IDENT)

_TOKEN = re.compile(
    r"""(?P<WS>\s+)
      | (?P<STRING>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
      | (?P<NUMBER>-?\d+\.\d+|-?\d+)
      | (?P<OP><=|>=|<>|!=|=~|=|<|>)
      | (?P<LP>\() | (?P<RP>\)) | (?P<LB>\[) | (?P<RB>\]) | (?P<COMMA>,)
      | (?P<NAME>"""
    + _NAME
    + r""")
    """,
    re.VERBOSE,
)


def tokenize(text: str) -> List[Tuple[str, str]]:
    """Split a WHERE body into ``(kind, text)`` pairs. Raises ``ParseError`` on a stray."""
    toks: List[Tuple[str, str]] = []
    i = 0
    while i < len(text):
        match = _TOKEN.match(text, i)
        if not match:
            raise ParseError("unexpected character at %d: %r" % (i, text[i : i + 12]))
        i = match.end()
        kind = match.lastgroup
        if kind == "WS":
            continue
        toks.append((kind, match.group()))
    return toks


# ----- quoting -----

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "\\": "\\", "'": "'", '"': '"', "`": "`", "/": "/"}


def unquote(text: str) -> str:
    """
    Strip one layer of Cypher quoting, of any of the three styles.

    The reference's ``_unquote``, widened: it handled ``'`` and ``"`` and nothing else,
    and this repo quotes *identifiers* with backticks (``dialect.backtick``), so
    ```Or``der` `` has to come back as ``Or`der`` — a doubled backtick is one literal
    backtick, which is exactly how ``backtick()`` writes one. A double-quoted run is
    decoded as JSON, because ``cypher_string`` is ``json.dumps`` and a key containing a
    quote or a non-ASCII character therefore arrives escaped; a single-quoted one uses
    Cypher's own backslash escapes. Anything not wrapped in a matched pair is returned
    unchanged.
    """
    if len(text) < 2 or text[0] != text[-1] or text[0] not in "\"'`":
        return text
    quote, body = text[0], text[1:-1]
    if quote == "`":
        return body.replace("``", "`")
    if quote == '"':
        try:
            decoded = json.loads(text)
        except ValueError:
            pass
        else:
            return decoded if isinstance(decoded, str) else text
    out: List[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            out.append(_ESCAPES.get(body[i + 1], body[i + 1]))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_name(text: str) -> List[str]:
    """
    Split ``n.`first name``` into its pieces, one per dot, quotes intact.

    One identifier scanner rather than two: ``translate.py`` reads property names out of
    ``ORDER BY`` and ``RETURN`` terms with this, so a dot inside a quoted name cannot be
    read as a separator here any more than it can be there.
    """
    parts: List[str] = []
    buf: List[str] = []
    quoted = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "`":
            if quoted and i + 1 < len(text) and text[i + 1] == "`":
                buf.append("``")
                i += 2
                continue
            quoted = not quoted
            buf.append(ch)
            i += 1
            continue
        if ch == "." and not quoted:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _property_operand(text: str) -> Operand:
    """A token the grammar read as a name: a variable, a property, or a quoted one."""
    parts = split_name(text)
    var = unquote(parts[0])
    if len(parts) == 1:
        return Prop(var)
    if len(parts) > 2:
        raise ParseError("a nested property path is not a predicate: %r" % text)
    prop = unquote(parts[1])
    if not prop:
        raise ParseError("a property name after the dot is empty: %r" % text)
    return Prop(var, prop)


def _lit_value(operand: Operand) -> object:
    return operand.value if isinstance(operand, Lit) else operand


def _fold(fn: str, inner: Operand) -> Operand:
    """
    Apply ``toLower`` / ``toUpper``: fold a literal, unwrap a property.

    The reference's rule, kept. The consequence is worth stating plainly, because
    ``compile.py`` reads it: a folded operand is *indistinguishable* from one written
    without the function, so ``toLower(n.name) = 'abc'`` arrives as ``n.name = 'abc'``
    with the literal already lowered. On a case-insensitive collation that is the right
    answer and on a case-sensitive one it narrows the match. A compiler that wants the
    case-insensitivity to survive should wrap the column itself, not read this fold as
    a case-sensitive comparison.
    """
    if isinstance(inner, Lit) and isinstance(inner.value, str):
        lowered = fn in ("tolower", "lower")
        return Lit(inner.value.lower() if lowered else inner.value.upper())
    return inner


# ----- transient operands -----


@dataclass(frozen=True)
class _InternalId:
    """``internal_id(t, o)``, the one operand that is a value only inside an ``IN`` list."""

    t: int
    o: int


def _is_identity(operand: Operand) -> bool:
    """Whether an operand names an identity, which is what makes an ``IN`` an ``IdIn``."""
    return isinstance(operand, EntityFn) and operand.func in IDENTITY_FUNCS


# ----- parser -----


class _Parser:
    def __init__(self, toks: List[Tuple[str, str]]) -> None:
        self.toks = toks
        self.i = 0

    def _peek(self, k: int = 0) -> Tuple[str, str]:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else ("EOF", "")

    def _next(self) -> Tuple[str, str]:
        tok = self._peek()
        self.i += 1
        return tok

    def _kw(self, word: str) -> bool:
        """Consume a bare keyword. A quoted run never matches: ```in``` is a name."""
        kind, value = self._peek()
        if kind == "NAME" and value.lower() == word:
            self.i += 1
            return True
        return False

    def _expect(self, kind: str) -> str:
        got, value = self._next()
        if got != kind:
            raise ParseError("expected %s, got %s %r" % (kind, got, value))
        return value

    # -- grammar --

    def parse(self) -> Expression:
        expr = self._or()
        if self._peek()[0] != "EOF":
            raise ParseError("trailing tokens: %r" % (self.toks[self.i :],))
        return expr

    def _or(self) -> Expression:
        parts = [self._and()]
        while self._kw("or"):
            parts.append(self._and())
        return parts[0] if len(parts) == 1 else Or(parts)

    def _and(self) -> Expression:
        parts = [self._not()]
        while self._kw("and"):
            parts.append(self._not())
        return parts[0] if len(parts) == 1 else And(parts)

    def _not(self) -> Expression:
        # A leading NOT that is not the "NOT" of "NOT IN", which belongs to the predicate.
        kind, value = self._peek()
        if kind == "NAME" and value.lower() == "not" and self._peek(1)[1].lower() != "in":
            self.i += 1
            return Not(self._not())
        return self._predicate()

    def _predicate(self) -> Expression:
        if self._peek()[0] == "LP":
            self.i += 1
            expr = self._or()
            self._expect("RP")
            return expr
        operand = self._operand()

        if self._kw("is"):
            negated = self._kw("not")
            if not self._kw("null"):
                raise ParseError("expected NULL after IS")
            return IsNull(_leaf(operand), negated)

        if self._kw("contains"):
            return StrOp(_leaf(operand), "contains", _lit_value(self._operand()))
        if self._kw("starts"):
            self._kw("with")
            return StrOp(_leaf(operand), "startswith", _lit_value(self._operand()))
        if self._kw("ends"):
            self._kw("with")
            return StrOp(_leaf(operand), "endswith", _lit_value(self._operand()))

        negated = False
        if self._peek()[0] == "NAME" and self._peek()[1].lower() == "not" \
                and self._peek(1)[0] == "NAME" and self._peek(1)[1].lower() == "in":
            self.i += 1
            negated = True
        if self._kw("in"):
            return self._in_list(operand, negated)

        kind, value = self._peek()
        if kind == "OP":
            self.i += 1
            lhs = _leaf(operand)
            rhs = _right(self._operand())
            if value == "=~":
                return StrOp(lhs, "regex", _lit_value(rhs))
            return Cmp(lhs, "<>" if value == "!=" else value, rhs)

        if isinstance(operand, Prop):
            return BoolProp(_leaf(operand))
        raise ParseError("an entity function must be compared, not used as a condition: %r" % (operand,))

    def _in_list(self, operand: Operand, negated: bool) -> Expression:
        items = self._bracket_list()
        internal = [item for item in items if isinstance(item, _InternalId)]
        if internal and len(internal) != len(items):
            raise ParseError("internal_id(...) appears only on the right of an id(n) IN predicate")
        if internal and _is_identity(operand):
            return IdIn(operand.var, [(item.t, item.o) for item in internal], negated)  # type: ignore[union-attr]
        return InList(_leaf(operand), [_lit_value(item) for item in items], negated)

    def _bracket_list(self) -> List[object]:
        # ``IN UNNEST([ ... ])`` — the Spanner spelling; equivalent to ``IN [ ... ]``.
        if self._peek()[0] == "NAME" and self._peek()[1].lower() == "unnest":
            self.i += 1
            self._expect("LP")
            self._expect("LB")
            items = self._items_until("RB")
            self._expect("RB")
            self._expect("RP")
            return items
        # An optional wrapping ``( [ ... ] )``.
        if self._peek()[0] == "LP":
            self.i += 1
            self._expect("LB")
            items = self._items_until("RB")
            self._expect("RB")
            self._expect("RP")
            return items
        self._expect("LB")
        items = self._items_until("RB")
        self._expect("RB")
        return items

    def _items_until(self, end: str) -> List[object]:
        items: List[object] = []
        if self._peek()[0] == end:
            return items
        items.append(self._operand())
        while self._peek()[0] == "COMMA":
            self.i += 1
            items.append(self._operand())
        return items

    def _operand(self) -> Operand:
        if self._peek()[0] == "LP":
            self.i += 1
            inner = self._operand()
            self._expect("RP")
            return inner
        kind, value = self._next()
        if kind == "STRING":
            return Lit(unquote(value))
        if kind == "NUMBER":
            return Lit(int(value) if re.match(r"^-?\d+$", value) else float(value))
        if kind != "NAME":
            raise ParseError("expected an operand, got %s %r" % (kind, value))
        low = value.lower()
        if low in CASE_FUNCS and self._peek()[0] == "LP":
            self.i += 1
            inner = self._operand()
            self._expect("RP")
            return _fold(low, inner)
        if low in ENTITY_FUNCS and self._peek()[0] == "LP":
            self.i += 1
            name = self._expect("NAME")
            self._expect("RP")
            var = unquote(split_name(name)[0])
            if not var:
                raise ParseError("%s() needs one variable" % low)
            return EntityFn(var, low)
        if low == "internal_id" and self._peek()[0] == "LP":
            self.i += 1
            table_id = int(self._expect("NUMBER"))
            self._expect("COMMA")
            offset = int(self._expect("NUMBER"))
            self._expect("RP")
            return _InternalId(table_id, offset)
        if low == "true":
            return Lit(True)
        if low == "false":
            return Lit(False)
        if low == "null":
            return Lit(None)
        return _property_operand(value)


def _leaf(operand: Operand) -> Operand:
    """
    A leaf's left side: a property of a variable, or an entity function of one.

    Neither a literal nor a bare variable qualifies. The reference let a bare ``n``
    through as ``Prop("n", None)`` and compiled it as ``n.id = true`` against whatever
    column came back for a name with no property; here the missing property is the
    error, because silently guessing which column a variable means is how a filter
    returns rows nobody asked for.
    """
    if isinstance(operand, Lit):
        raise ParseError("a literal is not a predicate left side: %r" % (operand.value,))
    if isinstance(operand, Prop) and not operand.prop:
        raise ParseError("a predicate needs a property of %r, as in %s.<name>" % (operand.var, operand.var))
    return operand


def _right(operand: Operand) -> Operand:
    """The right side of a comparison: a literal, or another variable's property."""
    if isinstance(operand, Prop) and not operand.prop:
        raise ParseError("a comparison needs a property of %r, not the variable alone" % operand.var)
    return operand


# ----- public entry points -----


def parse(text: str) -> Optional[Expression]:
    """
    Parse a WHERE body, or None when it is empty or unreadable.

    The reference's lenient contract, kept because a caller that only wants to draw
    conclusions from a readable predicate is the common case. ``parse_expression`` is
    the one that says why, and ``translate.py`` uses it, because this proxy's rule is
    that a statement it cannot read is refused rather than answered with nothing.
    """
    try:
        return parse_expression(text)
    except ParseError:
        return None


def parse_expression(text: str) -> Optional[Expression]:
    """``parse``, raising ``ParseError`` instead of swallowing it. Empty text is None."""
    body = (text or "").strip()
    if not body:
        return None
    return _Parser(tokenize(body)).parse()


def walk(expr: Optional[Expression]) -> Iterator[Node]:
    """
    Every node in ``expr``, parents before children. None yields nothing.

    Operands and literals come back too, so a compiler can find every property a
    predicate names — and every ``EntityFn``, which is how an identity predicate gets
    refused before anything runs.
    """
    if expr is None:
        return
    yield expr
    if isinstance(expr, (And, Or)):
        for part in expr.parts:
            yield from walk(part)
    elif isinstance(expr, Not):
        yield from walk(expr.expr)
    elif isinstance(expr, Cmp):
        yield expr.lhs
        yield expr.rhs
    elif isinstance(expr, (StrOp, InList, IsNull, BoolProp)):
        yield expr.lhs
