"""
One ``ConcretePlan`` in, one ``SELECT`` and the manifest that reads its rows back.

The compile step is a lookup, not a decision: ``plan.py`` already chose a label for
every variable and a pair and orientation for every connector, so nothing here asks the
mapping a question it cannot answer. That is the property that makes the output
defensible — an ambiguous plan compiled optimistically is how a wrong join becomes a
confident empty canvas.

Three of the bridge's "not cosmetic" decisions, and where they went:

* **one ``MetaData`` per statement, ``Table`` objects built from the mapping.** Nothing
  is reflected at query time: reflection is a round trip per query, and a table that
  changed underneath us should fail as a wrong column name rather than silently select
  a different one. An ``alias()`` per hop is what lets one table appear twice in a
  pattern, which is ordinary for a self-link.
* **every projected column carries a ``__gx_v{i}_{k|p}{j}`` label** so ``convert.py``
  reads rows by name. Position-dependence is what makes a projection change corrupt a
  result silently.
* **``start_var`` / ``end_var`` follow the mapping's src and dst, never the pattern's
  left and right.** A reverse arrow still has to name the endpoints its row actually
  joins, and ``test_reverse_edge_endpoint_ids_still_match_their_nodes`` is the test that
  pins it.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

import sqlalchemy as sa
from sqlalchemy import MetaData, Table, and_, cast, func, select
from sqlalchemy.sql.expression import Select

from . import expression
from .engine import EngineSpelling, ServerInfo, UnbindableType
from .expression import BoundVariable, PredicateScope
from .ir import BoundEdge, ConcretePlan, MatchPlan
from .predicate import split_name, unquote
from .schemas import NodeSchema, Pair, RelSchema
from .translate import AggItem, ReturnSpec

#: Prefix on every projected column. The bridge's convention, kept because it is what
#: makes the row reader position-independent.
COLUMN_PREFIX = "__gx"

_GRAPH = "graph"
_SCALAR = "scalar"
_AGGREGATE = "aggregate"


class CompileError(Exception):
    """A branch cannot be compiled. The message says which part."""


class UnbindableKey(CompileError, UnbindableType):
    """
    A key column whose type this engine cannot name, so a seed cannot be re-selected.

    The returning half of ``<Label>:<key>`` binds a seed against the key column *as that
    column's own type*. ``spelling.column_type`` answers None for a declared type the
    engine has no name for — a ``geometry``, or an un-analysed blank — and None against a
    key means a text parameter, no match, and an empty graph that looks exactly like a row
    that was never there. This is that refusal at its cheapest point, where the label, the
    column and its declared type are all still in hand; the bind seam
    (``expression.PredicateScope.bind_type``) raises the same fact for a value no key check
    was ever going to run against.
    """


class RepeatedRelationshipVariable(CompileError):
    """
    The pattern uses one relationship variable twice.

    Cypher forbids a relationship appearing twice in a path, so the honest answers are
    "no rows" or a refusal. A refusal is the one a person can act on: two joins of the
    same link table would hand back the ``a -> b -> a`` bounce-back Cypher never returns,
    which is a wrong answer rather than an empty one.
    """


@dataclass(frozen=True)
class VertexEntry:
    """One node of the projection: which result columns hold it, and what they mean."""

    var: str
    label: str
    alias: str
    #: the result column that carries the key, e.g. ``__gx_v0_k0``
    key_column: str
    #: the key's own column name, which is also the property name it is shown under
    key_name: str
    key_type: str
    #: result column label -> property name
    properties: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EdgeEntry:
    """
    One relationship of the projection.

    ``key_column`` is None when the edge type has no key of its own, and then the id is
    minted from its two endpoint keys — direction-stable, but two identical links
    collapse into one, which is the warning the mapping already carries.
    """

    var: str
    type: str
    alias: str
    key_column: Optional[str]
    key_name: Optional[str]
    key_type: Optional[str]
    start_var: str
    end_var: str
    start_label: str
    end_label: str
    properties: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Manifest:
    """Everything ``convert.py`` needs to turn rows back into a graph."""

    vertices: Tuple[VertexEntry, ...] = ()
    edges: Tuple[EdgeEntry, ...] = ()


@dataclass(frozen=True)
class CompiledStatement:
    """
    One statement, its bound values, and how to read what comes back.

    ``chunk_index`` / ``chunk_count`` say which piece of a fan-out this is, because a
    ``LIMIT`` applied per chunk is not a ``LIMIT`` applied to the answer: the caller
    trims on output and says paging across branches was best-effort.
    """

    select: Select
    parameters: Dict[str, object]
    manifest: Manifest
    kind: str
    columns: Tuple[str, ...] = ()
    limit: Optional[int] = None
    skip: int = 0
    distinct: bool = False
    chunk_index: int = 0
    chunk_count: int = 1

    @property
    def fanned_out(self) -> bool:
        return self.chunk_count > 1


def compile_statement(
    plan: ConcretePlan,
    nodes: Mapping[str, NodeSchema],
    edges: Mapping[str, RelSchema],
    *,
    spelling: EngineSpelling,
    server: Optional[ServerInfo] = None,
    spec: Optional[ReturnSpec] = None,
) -> CompiledStatement:
    """
    Compile one branch into one statement.

    Raises :class:`expression.FanoutRequired` for a value list this server cannot carry;
    call :func:`compile_statements` when the answer might need more than one statement.
    """
    return _Builder(plan, nodes, edges, spelling, server, spec).build()


def compile_statements(
    plan: ConcretePlan,
    nodes: Mapping[str, NodeSchema],
    edges: Mapping[str, RelSchema],
    *,
    spelling: EngineSpelling,
    server: Optional[ServerInfo] = None,
    spec: Optional[ReturnSpec] = None,
) -> List[CompiledStatement]:
    """
    One statement per chunk of a fan-out, or exactly one statement when nothing fans out.

    The list is never capped and never shortened: dropping a chunk drops the rows in it,
    and a missing row on a canvas is indistinguishable from a row that was never there.
    """
    slots = _fanout_slots(plan, nodes, edges, spelling, server)
    if not slots:
        return [compile_statement(plan, nodes, edges, spelling=spelling, server=server, spec=spec)]
    combinations = list(itertools.product(*[slot.chunks() for slot in slots]))
    out: List[CompiledStatement] = []
    for index, values in enumerate(combinations):
        substitution = {slot.index: chunk for slot, chunk in zip(slots, values)}
        out.append(
            _Builder(plan, nodes, edges, spelling, server, spec, substitution=substitution).build(
                chunk_index=index, chunk_count=len(combinations)
            )
        )
    return out


def _fanout_slots(
    plan: ConcretePlan,
    nodes: Mapping[str, NodeSchema],
    edges: Mapping[str, RelSchema],
    spelling: EngineSpelling,
    server: Optional[ServerInfo],
) -> List[expression.FanoutSlot]:
    """
    The key lists too big for one statement, found on a throwaway scope.

    A scope only answers "which column is this, and what did it declare", so the probe
    costs no more than the real build does and leaves every bind with the statement that
    actually uses it.
    """
    return expression.fanout_slots(plan.plan.where, _Builder(plan, nodes, edges, spelling, server, None).scope())


class _Builder:
    """
    One branch, one ``MetaData``, one statement.

    An object rather than a chain of functions because the state is genuinely shared:
    a variable's alias has to be *the same SQLAlchemy object* in the join, in the
    predicate and in the projection, or the compiler puts one table in FROM twice.
    """

    def __init__(
        self,
        plan: ConcretePlan,
        nodes: Mapping[str, NodeSchema],
        edges: Mapping[str, RelSchema],
        spelling: EngineSpelling,
        server: Optional[ServerInfo],
        spec: Optional[ReturnSpec],
        substitution: Optional[Mapping[int, Sequence[object]]] = None,
    ) -> None:
        self.branch = plan
        self.plan: MatchPlan = plan.plan
        self.nodes = nodes
        self.edges = edges
        self.spelling = spelling
        self.server = server
        self.spec = spec
        self.substitution: Dict[int, Sequence[object]] = dict(substitution or {})
        self.metadata = MetaData()
        self._tables: Dict[str, Table] = {}
        self._vertex_alias: Dict[str, object] = {}
        self._vertex_entry: Dict[str, int] = {}
        self._edge_alias: Dict[str, object] = {}
        self._extra: List[object] = []

    # -- mapping lookups ---------------------------------------------------

    def node(self, label: str) -> NodeSchema:
        schema = self.nodes.get(label)
        if schema is None:
            raise CompileError("the plan binds label %r, which this mapping does not declare" % label)
        return schema

    def rel(self, rel_type: str) -> RelSchema:
        schema = self.edges.get(rel_type)
        if schema is None:
            raise CompileError(
                "the plan binds relationship type %r, which this mapping does not declare" % rel_type
            )
        return schema

    def label_of(self, var: str) -> str:
        label = self.branch.labels.get(var)
        if label is None:
            raise CompileError("variable %r is not bound to a label by this branch" % var)
        return label

    # -- tables and aliases ------------------------------------------------

    def _table(self, name: str, schema: Optional[str], columns: Mapping[str, str]) -> Table:
        key = "%s.%s" % (schema or "", name)
        existing = self._tables.get(key)
        if existing is not None:
            return existing
        # Declaring a column is not binding one: nothing leaves for these types, so the
        # unnamed ones stay text here and refuse where a value is actually sent.
        args = [sa.Column(column, self.spelling.declared_column_type(type_name)) for column, type_name in columns.items()]
        table = Table(name, self.metadata, *args, schema=schema)
        self._tables[key] = table
        return table

    def vertex_alias(self, var: str) -> object:
        """This branch's alias for one node variable, made once and reused everywhere."""
        existing = self._vertex_alias.get(var)
        if existing is not None:
            return existing
        index = self._vertex_index(var)
        schema = self.node(self.label_of(var))
        columns = schema.all_props()
        alias = self._table(schema.table, schema.schema, columns).alias("v%d" % index)
        self._vertex_alias[var] = alias
        return alias

    def edge_alias(self, bound: BoundEdge) -> object:
        """The alias for one connector, keyed by the variable it was written with."""
        existing = self._edge_alias.get(bound.var)
        if existing is not None:
            return existing
        index = self._edge_index(bound)
        schema = self.rel(bound.rel_type)
        alias = self._table(schema.table, schema.schema, edge_columns(schema, bound.pair)).alias("e%d" % index)
        self._edge_alias[bound.var] = alias
        return alias

    def _vertex_index(self, var: str) -> int:
        known = self._vertex_entry.get(var)
        if known is not None:
            return known
        for index, vertex in enumerate(self.plan.vertices):
            if vertex.var == var:
                self._vertex_entry[var] = index
                return index
        raise CompileError("the plan uses %r, which no vertex of the pattern binds" % var)

    def _edge_index(self, bound: BoundEdge) -> int:
        for index, edge in enumerate(self.plan.edges):
            if edge.var == bound.var:
                return index
        raise CompileError("the plan binds %r, which no connector of the pattern declares" % bound.var)

    # -- scope -------------------------------------------------------------

    def scope(self) -> PredicateScope:
        """The predicate scope for this branch, with the join columns reachable."""
        variables: List[BoundVariable] = []
        for vertex in self.plan.vertices:
            if vertex.var not in self.branch.labels:
                continue
            schema = self.node(self.label_of(vertex.var))
            _require_bindable_key(self.spelling, "node label", schema.label, schema.key, schema.key_type)
            columns = schema.all_props()
            alias = self.vertex_alias(vertex.var)
            variables.append(
                BoundVariable(
                    var=vertex.var,
                    entity=self.label_of(vertex.var),
                    kind=expression.VERTEX_KIND,
                    columns={name: alias.c[name] for name in columns},  # type: ignore[attr-defined]
                    types=dict(columns),
                    key_prop=schema.key,
                )
            )
        for bound in self.branch.edges:
            schema = self.rel(bound.rel_type)
            _require_bindable_key(self.spelling, "relationship type", schema.type, schema.key, schema.key_type)
            columns = edge_columns(schema, bound.pair)
            alias = self.edge_alias(bound)
            variables.append(
                BoundVariable(
                    var=bound.var,
                    entity=bound.rel_type,
                    kind=expression.EDGE_KIND,
                    columns={name: alias.c[name] for name in columns},  # type: ignore[attr-defined]
                    types=dict(columns),
                    key_prop=schema.key or "",
                )
            )
        return PredicateScope(variables, spelling=self.spelling, server=self.server)

    # -- the statement -----------------------------------------------------

    def build(self, chunk_index: int = 0, chunk_count: int = 1) -> CompiledStatement:
        self._check_repeated_variables()
        scope = self.scope()
        plan = self.plan
        kind = self.spec.kind if self.spec is not None else _GRAPH
        columns, from_clause, manifest, names, group_by, order_keys = self._project(scope, kind)
        statement = select(*columns).select_from(from_clause)
        conditions = [clause for clause in (self._predicate(plan, scope),) + tuple(self._extra) if clause is not None]
        if conditions:
            statement = statement.where(and_(*conditions))
        if group_by:
            statement = statement.group_by(*group_by)
        if plan.limit is not None:
            statement = statement.limit(int(plan.limit))
        if plan.skip:
            statement = statement.offset(int(plan.skip))
        # A page has to be a *stable* page: see ``_order_terms``.
        if plan.limit is not None or plan.skip:
            ordering = self._order_terms(plan, scope, order_keys)
            if ordering:
                statement = statement.order_by(*ordering)
        distinct = bool(self.spec.distinct) if self.spec is not None else False
        if distinct:
            statement = statement.distinct()
        return CompiledStatement(
            select=statement,
            parameters=dict(scope.parameters),
            manifest=manifest,
            kind=kind,
            columns=tuple(names) if kind != _GRAPH else _column_names(columns),
            limit=plan.limit,
            skip=plan.skip,
            distinct=distinct,
            chunk_index=chunk_index,
            chunk_count=chunk_count,
        )

    def _check_repeated_variables(self) -> None:
        seen: Set[str] = set()
        for edge in self.plan.edges:
            if edge.var and edge.var in seen:
                raise RepeatedRelationshipVariable(
                    "relationship variable %r is used twice in this pattern. One relationship cannot "
                    "appear twice in a path, and two joins over it would answer rows Cypher does not "
                    "return." % edge.var
                )
            seen.add(edge.var)

    def _predicate(self, plan: MatchPlan, scope: PredicateScope):
        where = plan.where
        for index, values in sorted(self.substitution.items()):
            where = expression.replace_slot(where, index, values)
        return expression.compile_predicate(where, scope)

    def _project(self, scope: PredicateScope, kind: str):
        if kind == _GRAPH:
            columns, manifest, order_keys = self._graph_projection()
            return columns, self._from_clause(), manifest, (), [], order_keys
        columns, names, group_by = self._value_projection(scope, kind)
        return columns, self._from_clause(), Manifest(), names, group_by, []

    def _from_clause(self) -> object:
        """The root alias with one join per hop; a cycle becomes a predicate instead."""
        vertices = self.plan.vertices
        if not vertices:
            raise CompileError("a pattern with no vertices has no statement to build")
        joined: Set[str] = {vertices[0].var}
        current = self.vertex_alias(vertices[0].var)
        for bound in self.branch.edges:
            edge = self.edge_alias(bound)
            src = self.vertex_alias(bound.src_var)
            src_key = self.node(self.label_of(bound.src_var)).key
            current = current.join(edge, edge.c[_near_column(bound)] == src.c[src_key])  # type: ignore[attr-defined]
            joined.add(bound.src_var)
            dst = self.vertex_alias(bound.dst_var)
            dst_key = self.node(self.label_of(bound.dst_var)).key
            if bound.dst_var in joined:
                self._extra.append(dst.c[dst_key] == edge.c[_far_column(bound)])  # type: ignore[attr-defined]
                continue
            current = current.join(dst, dst.c[dst_key] == edge.c[_far_column(bound)])  # type: ignore[attr-defined]
            joined.add(bound.dst_var)
        # Cypher forbids one relationship appearing twice in a path, and a plain JOIN
        # does not: without this, a two-hop pattern over one link table answers every
        # a -> b -> a bounce-back the graph engine would have refused.
        for index, previous in enumerate(self.branch.edges):
            key = self.rel(previous.rel_type).key
            if not key:
                continue
            for earlier in self.branch.edges[:index]:
                if self.rel(earlier.rel_type).table != self.rel(previous.rel_type).table:
                    continue
                self._extra.append(
                    self.edge_alias(previous).c[key] != self.edge_alias(earlier).c[key]  # type: ignore[attr-defined]
                )
        return current

    def _graph_projection(self):
        """The entity columns and the manifest that says what each one is."""
        columns: List[object] = []
        vertices: List[VertexEntry] = []
        edges: List[EdgeEntry] = []
        order_keys: List[object] = []
        seen: Set[str] = set()
        for vertex in self.plan.vertices:
            if vertex.var in seen or vertex.var not in self.branch.labels:
                continue
            seen.add(vertex.var)
            index = self._vertex_index(vertex.var)
            schema = self.node(self.label_of(vertex.var))
            alias = self.vertex_alias(vertex.var)
            key_label = "%s_v%d_k0" % (COLUMN_PREFIX, index)
            columns.append(alias.c[schema.key].label(key_label))  # type: ignore[attr-defined]
            order_keys.append(alias.c[schema.key])  # type: ignore[attr-defined]
            properties: Dict[str, str] = {}
            position = 0
            for name in schema.all_props():
                if name == schema.key:
                    continue
                label = "%s_v%d_p%d" % (COLUMN_PREFIX, index, position)
                position += 1
                columns.append(alias.c[name].label(label))  # type: ignore[attr-defined]
                properties[label] = name
            vertices.append(
                VertexEntry(
                    var=vertex.var,
                    label=self.label_of(vertex.var),
                    alias=str(alias.name),  # type: ignore[attr-defined]
                    key_column=key_label,
                    key_name=schema.key,
                    key_type=schema.key_type,
                    properties=properties,
                )
            )
        for index, bound in enumerate(self.branch.edges):
            schema = self.rel(bound.rel_type)
            alias = self.edge_alias(bound)
            # The mapping's own direction, never the pattern's: an edge read backwards
            # still has to name the endpoints its row points at.
            start_var = bound.dst_var if bound.swapped else bound.src_var
            end_var = bound.src_var if bound.swapped else bound.dst_var
            key_label = None
            if schema.key:
                key_label = "%s_e%d_k0" % (COLUMN_PREFIX, index)
                columns.append(alias.c[schema.key].label(key_label))  # type: ignore[attr-defined]
                order_keys.append(alias.c[schema.key])  # type: ignore[attr-defined]
            properties = {}
            position = 0
            for name in schema.props:
                if name == schema.key:
                    continue
                label = "%s_e%d_p%d" % (COLUMN_PREFIX, index, position)
                position += 1
                columns.append(alias.c[name].label(label))  # type: ignore[attr-defined]
                properties[label] = name
            edges.append(
                EdgeEntry(
                    var=bound.var,
                    type=bound.rel_type,
                    alias=str(alias.name),  # type: ignore[attr-defined]
                    key_column=key_label,
                    key_name=schema.key,
                    key_type=schema.key_type,
                    start_var=start_var,
                    end_var=end_var,
                    start_label=bound.pair.src.label,
                    end_label=bound.pair.dst.label,
                    properties=properties,
                )
            )
        return columns, Manifest(tuple(vertices), tuple(edges)), order_keys

    def _value_projection(self, scope: PredicateScope, kind: str):
        """
        A ``RETURN`` of columns and aggregates.

        A grouping key is projected as the *same* expression it is grouped by: on SQL
        Server an unbounded column cannot be grouped at all, so it is narrowed to the
        index key size, and grouping the cast while projecting the column would leave
        the select list naming something the ``GROUP BY`` never mentioned.
        """
        spec = self.spec
        if spec is None:
            raise CompileError("a value projection needs a RETURN to project")
        columns: List[object] = []
        names: List[str] = []
        group_by: List[object] = []
        for item in spec.columns:
            column, declared = scope.resolve(item.var, item.prop)
            expression_ = column
            if kind == _AGGREGATE:
                cast_type = expression.cast_from_name(self.spelling.group_key_cast(declared))
                if cast_type is not None:
                    expression_ = cast(column, cast_type)
                group_by.append(expression_)
            columns.append(expression_.label(item.alias))
            names.append(item.alias)
        for item in spec.aggregates:
            columns.append(self._aggregate(item, scope).label(item.alias))
            names.append(item.alias)
        if not columns:
            raise CompileError("the RETURN projects nothing")
        return columns, names, group_by

    def _aggregate(self, item: AggItem, scope: PredicateScope):
        if item.star:
            return func.count()
        column = None
        declared = ""
        if item.prop and item.var:
            column, declared = scope.resolve(item.var, item.prop)
        elif item.var:
            schema = self.node(self.label_of(item.var))
            alias = self.vertex_alias(item.var)
            column = alias.c[schema.key]  # type: ignore[attr-defined]
            declared = schema.key_type
        if column is None:
            raise CompileError("%s() needs a variable or a variable's property" % item.func)
        if item.distinct:
            column = column.distinct()
        cast_type = expression.cast_from_name(self.spelling.agg_cast(item.func, declared))
        # ``count`` is never cast: casting the column changes what the count means. The
        # cast for the others is the whole reason the hook exists -- SQLAlchemy emits
        # ``avg(int)`` bare and SQL Server then answers with integer division.
        return getattr(func, item.func)(cast(column, cast_type) if cast_type is not None else column)

    def _order_terms(self, plan: MatchPlan, scope: PredicateScope, tiebreakers: Sequence[object]):
        """
        ``ORDER BY``, ending in the key columns when the statement pages.

        The tiebreakers are not tidiness: an unordered page is a page the server may hand
        out twice, and the dummy order SQL Server accepts to satisfy its ``OFFSET/FETCH``
        grammar -- ``ORDER BY (SELECT 1)`` -- is precisely the thing that makes paging
        meaningless. SQLAlchemy goes further and refuses to compile ``LIMIT``/``OFFSET``
        on mssql at all without an ``ORDER BY``, which is the same fact as a traceback.
        """
        out: List[object] = []
        for term, direction in plan.order or []:
            column = self._order_column(term, scope)
            out.append(column.desc() if direction == "DESC" else column.asc())
        for column in tiebreakers:
            if not _known(out, column):
                out.append(column.asc())
        return out

    def _order_column(self, term: str, scope: PredicateScope):
        """One ``ORDER BY`` term, which arrives as text because the mapping reads it."""
        parts = split_name(str(term))
        var = unquote(parts[0]) if parts else ""
        bound = scope.by_var.get(var)
        if bound is None:
            raise CompileError("ORDER BY names %r, which this pattern does not bind" % var)
        if len(parts) == 1:
            return bound.columns.get(bound.key_prop)
        return scope.resolve(var, unquote(parts[1]))[0]


def _known(items: Sequence[object], column: object) -> bool:
    return any(item is column for item in items)


def _column_names(columns: Sequence[object]) -> Tuple[str, ...]:
    out: List[str] = []
    for column in columns:
        name = getattr(column, "name", None)
        out.append(str(name))
    return tuple(out)


def _require_bindable_key(
    spelling: EngineSpelling, what: str, name: str, key: Optional[str], key_type: Optional[str]
) -> None:
    """
    The key column's own type must be nameable, or a seed cannot be re-selected.

    ``column_type`` answers None for a declared type the engine has no name for, and a key
    left with no name sends a text parameter, matches nothing, and fills a canvas with
    nothing under a name that looks like a real answer. Asking here rather than at the bind
    is what buys the message its cost: the label and its key column are known while the
    scope is being built, and no statement has to be half-compiled to find out. A label or
    link with no key of its own is not checked: ``convert.py`` mints those from their
    endpoints, not a bind.

    Two cases reach the same refusal, and the message names which: a reader that wrote down
    no type at all (blank, ``STRING``, ``NULL``) means *the schema did not name one*, which
    the user can only fix by keying on a column the schema can read; a reader that wrote
    down a real name the engine has no binding for (``geometry`` and the like) means *the
    type is real, the engine cannot carry it*, which the user can only fix by keying on a
    column this engine can name, or by taking the record out of the graph. Declaring a type
    for a key is not an option: there is no ``keyType`` field on the document, and the key
    column never becomes a property.
    """
    if not key:
        return
    declared = key_type or ""
    if spelling.column_type(declared) is not None:
        return
    if _is_no_type_marker(declared):
        raise UnbindableKey(
            "%s %r is keyed by column %r, whose type the schema did not name, so this engine "
            "has nothing to bind as the column's own type; a seed could not be re-selected, so "
            "the lookup is refused rather than answered with an empty result. Key the label on "
            "a column whose type the schema can name, or remove the record from the graph "
            "(exclude it, or clear it from include)." % (what, name, key)
        )
    raise UnbindableKey(
        "%s %r is keyed by column %r, whose declared type %r this engine cannot bind as the "
        "column's own type; a seed could not be re-selected, so the lookup is refused rather "
        "than answered with an empty result. Key the label on a column this engine can name, or "
        "remove the record from the graph (exclude it, or clear it from include)."
        % (what, name, key, declared)
    )


#: What a reader writes down when it has **no** type at all, as distinct from a type the engine
#: has no binding for. Mirrors :data:`mapping_index.NO_TYPE`: blank, ``STRING`` (this project's
#: own placeholder ``UNKNOWN_TYPE``), and ``NULL`` (what ``reflect.type_name`` renders for the
#: ``NullType`` row every dialect hands back after "Did not recognize type"). The two cases
#: reach the same refusal but cost the user different fixes, so the message names which.
_NO_TYPE_MARKERS = frozenset({"", "STRING", "NULL"})


def _is_no_type_marker(declared: str) -> bool:
    """Whether the declared type is a reader admitting it has none, not a real name."""
    return str(declared or "").strip().upper() in _NO_TYPE_MARKERS


def edge_columns(schema: RelSchema, pair: Pair) -> Dict[str, str]:
    """
    The columns of an edge table: its key, its properties, and both join columns.

    The endpoint columns are not properties -- the mapping deliberately does not expose
    them, because a GraphXR edge is placed by its endpoints and not by their values --
    but they are still columns of the table, and a predicate naming one is a legitimate
    narrowing of a polymorphic link.
    """
    columns: Dict[str, str] = {}
    if schema.key:
        columns[schema.key] = schema.key_type or ""
    for name, type_name in schema.props.items():
        if name not in columns:
            columns[name] = type_name
    for endpoint in (pair.src, pair.dst):
        if endpoint.column not in columns:
            columns[endpoint.column] = endpoint.column_type
    return columns


def _near_column(bound: BoundEdge) -> str:
    """The edge column that points back at the vertex this hop starts from."""
    return bound.pair.dst.column if bound.swapped else bound.pair.src.column


def _far_column(bound: BoundEdge) -> str:
    """...and the one that points at the vertex it brings in."""
    return bound.pair.src.column if bound.swapped else bound.pair.dst.column
