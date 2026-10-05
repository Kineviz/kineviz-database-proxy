"""
Statement generation, once, parameterised per backend.

The Python twin of ``web/react_views/configure/graphdb/dialect/CypherDialect.ts``
in graphxr-dev: one builder for the whole ``MATCH ... RETURN`` family, with the
per-database differences expressed as a ``StatementDialect`` token table.

A new proxy driver should be a capability record plus one of these — not another
copy of the traversal logic.

Pure: no I/O, no driver imports, so ``tests/test_dialect.py`` can assert the
statements directly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..contract.capabilities import ExpandDirection, ExpandPredicate

#: Arrow pairs per direction, matching the client's DIRECTION_ARROWS.
_ARROWS: Dict[str, tuple] = {
    "all": ("-", "-"),
    "from": ("-", "->"),
    "to": ("<-", "-"),
    "both": ("<-", "->"),
}

_NUMERIC_KEY_TYPES = {
    "int", "integer", "uint", "bigint", "smallint", "float", "double", "decimal", "number",
    "numeric", "int2", "int4", "int8", "real", "double precision", "float4", "float8", "oid",
    "money", "int64", "float64", "bignumeric", "int128", "int32", "int16", "uint64", "uint32",
    "uint16", "uint8", "serial",
}

_NUMERIC_LITERAL = re.compile(r"^-?\d+(\.\d+)?$")

#: A bolt internal id, kept as a string so a 64-bit value keeps its precision.
_NUMERIC_INTERNAL_ID = re.compile(r"^\d+$")


def backtick(name: str) -> str:
    """Backtick-quoted identifier, with embedded backticks doubled."""
    return "`" + str(name).replace("`", "``") + "`"


def cypher_string(value: str) -> str:
    """Double-quoted Cypher string literal."""
    return json.dumps(str(value))


def is_numeric_key_type(key_type: Optional[str]) -> bool:
    """Whether a schema type means "emit the key unquoted"."""
    if not key_type:
        return False
    base = re.sub(r"\s*\(.*$", "", str(key_type).lower()).strip()
    return base in _NUMERIC_KEY_TYPES


def numeric_id_literal(value: object) -> str:
    """
    A bolt internal id, emitted bare.

    Neo4j and Memgraph ids are 64-bit integers and are carried as strings on
    purpose: parsing ``1152921504606846979`` through a float loses precision, so
    the digits are pasted into the statement as they arrived. Anything that is not
    all digits is quoted rather than pasted raw.
    """
    text = str(value)
    return text if _NUMERIC_INTERNAL_ID.match(text) else cypher_string(text)


def render_key_literal(value: object, key_type: Optional[str], quote: Callable[[str], str]) -> str:
    """
    A key is emitted bare only when the column is numeric **and** the value looks
    numeric — a numeric column holding ``"abc"`` must still be quoted so the
    backend reports a type error rather than a syntax error.
    """
    text = str(value)
    if is_numeric_key_type(key_type) and _NUMERIC_LITERAL.match(text):
        return text
    return quote(text)


@dataclass(frozen=True)
class NodeRef:
    """A seed node, resolved to whatever its backend's predicate needs."""

    category: str = ""
    internal_id: Optional[str] = None
    key_prop: Optional[str] = None
    key_value: Optional[str] = None


@dataclass(frozen=True)
class StatementDialect:
    """One backend's tokens. Everything else is shared."""

    name: str
    predicate: ExpandPredicate = "internal-id"
    #: ``ID(n)`` / ``id(n)`` / ``ELEMENT_ID(n)``.
    node_id_expr: Callable[[str], str] = lambda var: f"ID({var})"
    #: ``ID(r)``; None when the backend cannot filter on edge identity.
    rel_id_expr: Optional[Callable[[str], str]] = None
    #: ``TYPE(r)``; None when relationship filters must go into the pattern.
    rel_type_expr: Optional[Callable[[str], str]] = None
    id_literal: Callable[[str], str] = cypher_string
    #: Wrap rendered literals as the right side of ``IN``.
    id_list: Callable[[Sequence[str]], str] = lambda items: "[" + ",".join(items) + "]"
    where_keyword: str = "WHERE"
    #: "pattern" puts relationship types in ``[r:`A`|`B`]``; "where" puts them in a predicate.
    relationship_filter: str = "where"
    return_vars: Sequence[str] = ("r", "m")
    return_separator: str = ","
    #: How one pattern variable is written in a ``RETURN``. None returns the
    #: variable itself, which is what a backend whose results carry whole entities
    #: wants. A backend that answers a bare variable with nothing but an id has to
    #: project the parts it needs instead. ``kind`` is "node" or "relationship".
    projection: Optional[Callable[[str, str], str]] = None
    #: Whether a name can be written into a pattern as an identifier. A backend with
    #: no quoting syntax cannot express every label or relationship type there, and
    #: the builders fall back to a predicate for the ones it cannot.
    pattern_identifier_ok: Callable[[str], bool] = lambda name: True
    #: The predicate restricting a variable to a label, used only for a label
    #: ``pattern_identifier_ok`` refused. None when the pattern always suffices.
    label_predicate: Optional[Callable[[str, str], str]] = None
    #: Extra RETURN items naming a relationship's endpoints, for a backend whose
    #: edges do not carry them. ``(rel_var, source_var, target_var) -> str``.
    rel_endpoints_projection: Optional[Callable[[str, str, str], str]] = None
    #: Whether an undirected pattern can be read back. A backend whose edges carry
    #: their own endpoints can; one that infers them from the pattern cannot, and is
    #: given one statement per direction instead of one ambiguous statement.
    directed_patterns_only: bool = False
    quote_identifier: Callable[[str], str] = backtick
    pagination: Callable[[int, int], str] = lambda skip, limit: f"SKIP {skip} LIMIT {limit}"
    supports_multi_hop: bool = True
    supports_only_between_selected: bool = True
    #: Required when ``predicate`` is "primary-key".
    key_access: Callable[[str, str], str] = lambda var, prop: f"{var}.{backtick(prop)}"
    key_literal: Callable[[object, Optional[str]], str] = (
        lambda value, key_type: render_key_literal(value, key_type, cypher_string)
    )
    key_list: Callable[[Sequence[str]], str] = lambda items: "[" + ",".join(items) + "]"
    label_pattern: Callable[[str, str], str] = lambda var, label: f"{var}:{backtick(label)}"
    extra: Dict[str, object] = field(default_factory=dict)


def _project(dialect: StatementDialect, var: str, kind: str) -> str:
    """One RETURN item: the variable itself, or the parts a backend needs instead."""
    return dialect.projection(var, kind) if dialect.projection else var


def _pattern_types(dialect: StatementDialect, types: Sequence[str]) -> List[str]:
    """The relationship types this backend can put in a pattern."""
    return [t for t in types if dialect.pattern_identifier_ok(str(t))]


def _type_predicates(
    dialect: StatementDialect, rel_vars: Sequence[str], types: Sequence[str]
) -> List[str]:
    """
    The types a pattern could not carry, as predicates.

    Empty for every backend with identifier quoting, which is all of them but
    LatticeDB -- and empty there too until a store holds a relationship type that
    is not a bare identifier.
    """
    unexpressible = [t for t in types if not dialect.pattern_identifier_ok(str(t))]
    if not unexpressible or dialect.rel_type_expr is None:
        return []
    listed = "[" + ",".join(cypher_string(t) for t in unexpressible) + "]"
    return [f"{dialect.rel_type_expr(var)} IN {listed}" for var in rel_vars]


def _label_predicate(
    dialect: StatementDialect, var: str, category: Optional[str]
) -> Optional[str]:
    """The label predicate a backend needs when its pattern could not carry it."""
    if not category or dialect.label_predicate is None:
        return None
    if dialect.pattern_identifier_ok(str(category)):
        return None
    return dialect.label_predicate(var, category)


def _where_clause(dialect: StatementDialect, predicates: Sequence[str]) -> List[str]:
    """``WHERE a AND b``, or nothing at all when there is nothing to say."""
    kept = [p for p in predicates if p]
    if not kept:
        return []
    return [f"{dialect.where_keyword} " + " AND ".join(kept)]


def _relationship_vars(dialect: StatementDialect, hops: int) -> List[str]:
    count = max(1, int(hops or 1)) if dialect.supports_multi_hop else 1
    return ["r" if index == 0 else f"r{index}" for index in range(count)]


def _relationship_pattern(dialect: StatementDialect, var: str, types: Sequence[str]) -> str:
    expressible = _pattern_types(dialect, types)
    if not expressible:
        return var
    return var + ":" + "|".join(dialect.quote_identifier(t) for t in expressible)


def _match_clause(
    dialect: StatementDialect,
    direction: ExpandDirection,
    rel_vars: Sequence[str],
    pattern_types: Sequence[str],
    category: Optional[str],
) -> str:
    left, right = _ARROWS.get(direction, _ARROWS["all"])
    head = (
        dialect.label_pattern("n", category)
        if category and dialect.pattern_identifier_ok(str(category))
        else "n"
    )
    segments = [f"({head})"]
    for index, rel_var in enumerate(rel_vars):
        tail = "(m)" if index == len(rel_vars) - 1 else f"(n{index + 1})"
        segments.append(f"{left}[{_relationship_pattern(dialect, rel_var, pattern_types)}]{right}{tail}")
    return "MATCH " + "".join(segments)


def _node_chain(rel_vars: Sequence[str]) -> List[str]:
    """
    The node variables a pattern walks, in order.

    ``_match_clause`` writes ``(n)-[r]->(m)`` for one hop and
    ``(n)-[r]->(n1)-[r1]->(m)`` for two, so ``rel_vars[i]`` always joins
    ``chain[i]`` to ``chain[i + 1]``.
    """
    return ["n", *(f"n{index + 1}" for index in range(len(rel_vars) - 1)), "m"]


def _endpoints_of(
    rel_vars: Sequence[str], index: int, direction: ExpandDirection
) -> Tuple[str, str]:
    """
    Which variable is the source of a relationship, and which the target.

    Only meaningful for a directed pattern. ``directed_patterns_only`` is what
    guarantees one, by splitting an undirected expand into a statement per
    direction before this is reached.
    """
    chain = _node_chain(rel_vars)
    near, far = chain[index], chain[index + 1]
    return (far, near) if direction == "to" else (near, far)


def _return_clause(
    dialect: StatementDialect,
    rel_vars: Sequence[str],
    direction: ExpandDirection = "from",
) -> str:
    names: List[str] = [*dialect.return_vars]
    for index, rel_var in enumerate(rel_vars[1:], start=1):
        names.extend([f"n{index}", rel_var])

    relationship_names = set(rel_vars)
    items: List[str] = []
    for name in names:
        if name not in relationship_names:
            items.append(_project(dialect, name, "node"))
            continue
        items.append(_project(dialect, name, "relationship"))
        if dialect.rel_endpoints_projection is not None:
            source, target = _endpoints_of(rel_vars, rel_vars.index(name), direction)
            items.append(dialect.rel_endpoints_projection(name, source, target))

    return "RETURN " + dialect.return_separator.join(items)


def project_variables(
    dialect: StatementDialect,
    entries: Sequence[Tuple[str, str, Optional[Tuple[str, str, str]]]],
) -> str:
    """
    ``RETURN`` naming exactly these variables, each relationship with its ends.

    Public because a *rewritten* statement needs the same projection a built one
    gets, and what an entity looks like in a RETURN should have one implementation.
    Each entry is ``(variable, kind, endpoints)``, where ``endpoints`` is
    ``(relationship, start, end)`` for a relationship whose ends the row has to
    carry -- a built statement has at most one, a query the user wrote may have
    several, each with different ends.
    """
    items: List[str] = []
    for var, kind, endpoints in entries:
        items.append(_project(dialect, var, kind))
        if kind == "relationship" and endpoints and dialect.rel_endpoints_projection:
            items.append(dialect.rel_endpoints_projection(*endpoints))
    return "RETURN " + dialect.return_separator.join(items)


def _return_star(
    dialect: StatementDialect,
    *variables: Tuple[str, str],
    endpoints: Optional[Tuple[str, str, str]] = None,
) -> str:
    """
    ``RETURN *``, or the projection of exactly the variables it stands for.

    ``*`` is the right answer wherever a bare variable carries the whole entity.
    Where it does not -- and where the parser rejects ``*`` outright, as LatticeDB's
    does -- the variables have to be named, so the caller says which are in scope.
    """
    if dialect.projection is None:
        return "RETURN *"
    return project_variables(
        dialect,
        [
            (var, kind, endpoints if kind == "relationship" else None)
            for var, kind in variables
        ],
    )


def build_expand(
    dialect: StatementDialect,
    refs: Sequence[NodeRef],
    *,
    direction: ExpandDirection = "all",
    relationships: Sequence[str] = (),
    exclude_relationship_types: Sequence[str] = (),
    exclude_relationship_ids: Sequence[str] = (),
    hops: int = 1,
    only_between_selected: bool = False,
    limit: int = 1000,
    skip: int = 0,
    category: Optional[str] = None,
    key_types: Optional[Dict[str, str]] = None,
) -> List[str]:
    """
    One statement for an id predicate; one per category for a key predicate,
    because the key property differs per category.
    """
    if not refs:
        return []

    if dialect.directed_patterns_only and direction in ("all", "both"):
        # An undirected pattern answers the same edge twice with its ends swapped,
        # and nothing in the row says which way round it really is. Two directed
        # statements say it unambiguously; ``_run_statements`` merges them by id.
        return [
            statement
            for one_way in ("from", "to")
            for statement in build_expand(
                dialect, refs,
                direction=one_way, relationships=relationships,
                exclude_relationship_types=exclude_relationship_types,
                exclude_relationship_ids=exclude_relationship_ids,
                hops=hops, only_between_selected=only_between_selected,
                limit=limit, skip=skip, category=category, key_types=key_types,
            )
        ]

    return (
        _expand_by_key(
            dialect, refs,
            direction=direction, relationships=relationships,
            exclude_relationship_types=exclude_relationship_types,
            only_between_selected=only_between_selected,
            limit=limit, skip=skip, category=category, key_types=key_types or {},
            hops=hops,
        )
        if dialect.predicate == "primary-key"
        else _expand_by_id(
            dialect, refs,
            direction=direction, relationships=relationships,
            exclude_relationship_types=exclude_relationship_types,
            exclude_relationship_ids=exclude_relationship_ids,
            hops=hops, only_between_selected=only_between_selected,
            limit=limit, skip=skip, category=category,
        )
    )


def _expand_by_id(
    dialect: StatementDialect,
    refs: Sequence[NodeRef],
    *,
    direction: ExpandDirection,
    relationships: Sequence[str],
    exclude_relationship_types: Sequence[str],
    exclude_relationship_ids: Sequence[str],
    hops: int,
    only_between_selected: bool,
    limit: int,
    skip: int,
    category: Optional[str],
) -> List[str]:
    ids = [ref.internal_id for ref in refs if ref.internal_id]
    if not ids:
        return []

    rel_vars = _relationship_vars(dialect, hops)
    hidden = list(exclude_relationship_types) if dialect.rel_type_expr else []
    selected = [t for t in relationships if t not in hidden]
    pattern_types = selected if dialect.relationship_filter == "pattern" else []

    id_list = dialect.id_list([dialect.id_literal(i) for i in ids])
    clauses = [
        _match_clause(dialect, direction, rel_vars, pattern_types, category),
        f"{dialect.where_keyword} {dialect.node_id_expr('n')} IN {id_list}",
    ]

    if only_between_selected and dialect.supports_only_between_selected:
        clauses.append(f"AND {dialect.node_id_expr('m')} <> {dialect.node_id_expr('n')}")
        clauses.append(f"AND {dialect.node_id_expr('m')} IN {id_list}")

    if hidden and dialect.rel_type_expr:
        hidden_list = "[" + ",".join(cypher_string(t) for t in hidden) + "]"
        for rel_var in rel_vars:
            clauses.append(f"AND NOT({dialect.rel_type_expr(rel_var)} IN {hidden_list})")

    if exclude_relationship_ids and dialect.rel_id_expr:
        excluded = dialect.id_list([dialect.id_literal(i) for i in exclude_relationship_ids])
        for rel_var in rel_vars:
            clauses.append(f"AND NOT({dialect.rel_id_expr(rel_var)} IN {excluded})")

    if selected and dialect.relationship_filter == "where" and dialect.rel_type_expr:
        selected_list = "[" + ",".join(cypher_string(t) for t in selected) + "]"
        for rel_var in rel_vars:
            clauses.append(f"AND {dialect.rel_type_expr(rel_var)} IN {selected_list}")
    elif pattern_types:
        # A pattern-filtering backend that could not write every selected type into
        # the pattern has to say the rest in a predicate, or they would be dropped.
        for predicate in _type_predicates(dialect, rel_vars, selected):
            clauses.append(f"AND {predicate}")

    label_predicate = _label_predicate(dialect, "n", category)
    if label_predicate:
        clauses.append(f"AND {label_predicate}")

    clauses.append(_return_clause(dialect, rel_vars, direction))
    clauses.append(dialect.pagination(skip, limit))
    return [" ".join(clauses)]


def _expand_by_key(
    dialect: StatementDialect,
    refs: Sequence[NodeRef],
    *,
    direction: ExpandDirection,
    relationships: Sequence[str],
    exclude_relationship_types: Sequence[str],
    only_between_selected: bool,
    limit: int,
    skip: int,
    category: Optional[str],
    key_types: Dict[str, str],
    hops: int = 1,
) -> List[str]:
    grouped: Dict[str, List[NodeRef]] = {}
    for ref in refs:
        if not ref.category or ref.key_value is None:
            continue
        if category and ref.category != category:
            continue
        bucket = grouped.setdefault(ref.category, [])
        if all(existing.key_value != ref.key_value for existing in bucket):
            bucket.append(ref)

    hidden = list(exclude_relationship_types) if dialect.rel_type_expr else []
    selected = [t for t in relationships if t not in hidden]
    pattern_types = selected if dialect.relationship_filter == "pattern" else []
    # Hops are the dialect's to allow, exactly as on the id path. Pinning this to a
    # single hop made every key-predicate backend answer a two-hop expand with a
    # one-hop graph, whatever its capability record claimed.
    rel_vars = _relationship_vars(dialect, hops)

    statements: List[str] = []
    for group_category, group in grouped.items():
        key_prop = group[0].key_prop
        if not key_prop:
            continue
        key_type = key_types.get(group_category)
        literals = [dialect.key_literal(ref.key_value, key_type) for ref in group]
        clauses = [
            _match_clause(dialect, direction, rel_vars, pattern_types, group_category),
            f"{dialect.where_keyword} {dialect.key_access('n', key_prop)} IN {dialect.key_list(literals)}",
        ]
        if only_between_selected and dialect.supports_only_between_selected:
            clauses.append(f"AND {dialect.node_id_expr('m')} <> {dialect.node_id_expr('n')}")
        # A backend with a relationship-type function can hide types here too. Inert
        # where there is none -- RocketGraph -- and the reason a hidden type used to
        # come back on the key path while the id path filtered it out.
        if hidden and dialect.rel_type_expr:
            hidden_list = "[" + ",".join(cypher_string(t) for t in hidden) + "]"
            for rel_var in rel_vars:
                clauses.append(f"AND NOT({dialect.rel_type_expr(rel_var)} IN {hidden_list})")
        group_label_predicate = _label_predicate(dialect, "n", group_category)
        if group_label_predicate:
            clauses.append(f"AND {group_label_predicate}")
        clauses.append(_return_clause(dialect, rel_vars, direction))
        clauses.append(dialect.pagination(skip, limit))
        statements.append(" ".join(clauses))
    return statements


def build_pull_category(
    dialect: StatementDialect,
    category: str,
    *,
    limit: int = 1000,
    skip: int = 0,
    loaded: Sequence[NodeRef] = (),
    key_types: Optional[Dict[str, str]] = None,
) -> List[str]:
    head = (
        dialect.label_pattern("n", category)
        if dialect.pattern_identifier_ok(str(category))
        else "n"
    )
    clauses = [f"MATCH ({head})"]
    clauses.extend(
        _where_clause(
            dialect,
            [
                _label_predicate(dialect, "n", category),
                _exclude_loaded(dialect, category, loaded, key_types or {}),
            ],
        )
    )
    clauses.append(_return_star(dialect, ("n", "node")))
    clauses.append(dialect.pagination(skip, limit))
    return [" ".join(clauses)]


def _exclude_loaded(
    dialect: StatementDialect,
    category: str,
    loaded: Sequence[NodeRef],
    key_types: Dict[str, str],
) -> str:
    """The predicate alone -- the caller decides whether it opens a WHERE or joins one."""
    if not loaded:
        return ""
    if dialect.predicate == "primary-key":
        refs = [ref for ref in loaded if ref.category == category and ref.key_value is not None]
        if not refs:
            return ""
        key_prop = refs[0].key_prop
        if not key_prop:
            return ""
        key_type = key_types.get(category)
        literals = _unique([dialect.key_literal(ref.key_value, key_type) for ref in refs])
        return (
            f"NOT {dialect.key_access('n', key_prop)} IN {dialect.key_list(literals)}"
        )
    ids = _unique([dialect.id_literal(ref.internal_id) for ref in loaded if ref.internal_id])
    if not ids:
        return ""
    return f"NOT {dialect.node_id_expr('n')} IN {dialect.id_list(ids)}"


def build_pull_relationship(
    dialect: StatementDialect,
    relationship: str,
    *,
    limit: int = 1000,
    skip: int = 0,
    loaded_ids: Sequence[str] = (),
) -> List[str]:
    clauses = [f"MATCH (n)-[{_relationship_pattern(dialect, 'r', [relationship])}]->(m)"]

    predicates: List[str] = list(_type_predicates(dialect, ["r"], [relationship]))
    if loaded_ids and dialect.rel_id_expr:
        literals = _unique([dialect.id_literal(i) for i in loaded_ids])
        predicates.append(f"NOT {dialect.rel_id_expr('r')} IN {dialect.id_list(literals)}")
    clauses.extend(_where_clause(dialect, predicates))

    clauses.append(
        _return_star(
            dialect,
            ("n", "node"),
            ("r", "relationship"),
            ("m", "node"),
            endpoints=("r", "n", "m"),
        )
    )
    clauses.append(dialect.pagination(skip, limit))
    return [" ".join(clauses)]


def _unique(values: Sequence[str]) -> List[str]:
    seen: Dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return list(seen.keys())


# ---------------------------------------------------------------------------
# Per-backend token tables
# ---------------------------------------------------------------------------

#: Spanner Graph GQL: FILTER WHERE, ELEMENT_ID(), IN UNNEST(...).
SPANNER_DIALECT = StatementDialect(
    name="spanner",
    node_id_expr=lambda var: f"ELEMENT_ID({var})",
    rel_id_expr=lambda var: f"ELEMENT_ID({var})",
    rel_type_expr=None,
    id_list=lambda items: "UNNEST ([" + ",".join(items) + "])",
    where_keyword="FILTER WHERE",
    relationship_filter="pattern",
)

#: RocketGraph / XGT: no node-identity function, so seeds are re-selected by key.
ROCKETGRAPH_DIALECT = StatementDialect(
    name="rocketgraph",
    predicate="primary-key",
    node_id_expr=lambda var: f"id({var})",
    rel_id_expr=None,
    rel_type_expr=None,
    relationship_filter="pattern",
    return_vars=("n", "r", "m"),
    return_separator=", ",
    supports_multi_hop=False,
    supports_only_between_selected=False,
)

def _bigquery_identity(var: str) -> str:
    """BigQuery has no ``ELEMENT_ID()``; identity is read out of the element's JSON."""
    return f"JSON_VALUE(TO_JSON({var}), '$.identifier')"


#: BigQuery graph: Spanner's GQL, minus ``ELEMENT_ID()``.
BIGQUERY_DIALECT = StatementDialect(
    name="bigquery",
    node_id_expr=_bigquery_identity,
    rel_id_expr=_bigquery_identity,
    rel_type_expr=None,
    id_list=lambda items: "UNNEST ([" + ",".join(items) + "])",
    where_keyword="FILTER WHERE",
    relationship_filter="pattern",
)


def _bolt_family_dialect(name: str) -> StatementDialect:
    """
    The shared base for every bolt-compatible backend, mirroring the client's
    ``boltFamilyProfile``.

    ``ID()`` rather than ``elementId()``: the latter only exists from Neo4j 5, and
    Memgraph has no equivalent at all, so the older spelling is the one both
    backends answer. Ids therefore round-trip as digit strings.
    """
    return StatementDialect(
        name=name,
        node_id_expr=lambda var: f"ID({var})",
        rel_id_expr=lambda var: f"ID({var})",
        rel_type_expr=lambda var: f"TYPE({var})",
        id_literal=numeric_id_literal,
        relationship_filter="where",
    )


#: Neo4j, and the family it heads.
NEO4J_DIALECT = _bolt_family_dialect("neo4j")

#: Memgraph speaks bolt and Cypher, so its statements are Neo4j's unchanged.
MEMGRAPH_DIALECT = _bolt_family_dialect("memgraph")

def _embedded_dialect(name: str) -> StatementDialect:
    """
    The shared base for Kuzu and Ladybug, which are one engine with two names.

    Every token below was checked against a running engine rather than inferred
    from the Cypher it advertises:

      - ``predicate="primary-key"``. ``ID(n)`` reads back as an ``INTERNAL_ID`` but
        there is no literal for one -- ``n._id`` is rejected as "reserved for system
        usage" -- so a seed can only be re-selected by its key. Node tables always
        have one; a table declared without a primary key is a parse error.
      - ``return_vars=("n", "r", "m")``. An edge carries its endpoints as internal
        ids, and turning those into ``<Label>:<key>`` needs the nodes themselves, so
        the source and target are returned alongside every relationship. Multi-hop
        adds ``n1, r1`` and so on, which keeps every node on the path in the result.
      - ``rel_type_expr=label(r)``, verified in both ``RETURN`` and ``WHERE``, so
        hidden and selected relationship types can be filtered in a predicate.
      - ``relationship_filter="pattern"``: ``[r:`A`|`B`]`` parses.
      - ``rel_id_expr=None``: no relationship-identity literal either.
      - ``supports_only_between_selected=False``: a key predicate names one end of a
        traversal and cannot pin the other.
      - Pagination is ``SKIP n LIMIT m`` and only that order; ``LIMIT m SKIP n`` is a
        parse error.
    """
    return StatementDialect(
        name=name,
        predicate="primary-key",
        node_id_expr=lambda var: f"ID({var})",
        rel_id_expr=None,
        rel_type_expr=lambda var: f"label({var})",
        relationship_filter="pattern",
        return_vars=("n", "r", "m"),
        return_separator=", ",
        supports_multi_hop=True,
        supports_only_between_selected=False,
    )


#: A name LatticeDB's parser will accept unquoted, which is the only way it takes
#: one: there is no quoted-identifier syntax at all, and a backtick is an "Invalid
#: token" wherever it appears.
_BARE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _lattice_projection(var: str, kind: str) -> str:
    """
    The parts of an entity, because the entity itself does not come back.

    ``RETURN n`` is accepted and answers ``{"n": 1}`` -- the node id and nothing
    else. LatticeDB's value types are the scalars plus list and map; there is no
    node or relationship among them, so a whole entity has no representation to be
    returned in. What the graph needs is projected instead, and every piece of it
    was checked against a running engine.
    """
    if kind == "relationship":
        return f"id({var}), type({var}), properties({var})"
    return f"id({var}), labels({var}), properties({var})"


def _lattice_dialect() -> StatementDialect:
    """
    LatticeDB: Cypher over a single file, with a narrower grammar than the name
    suggests and a wider identity surface than the other embedded engines.

    Every token was checked against a running 0.14.0 rather than inferred:

      - ``predicate="internal-id"``, unlike Kuzu and Ladybug. ``id(n)`` both reads
        back and matches -- ``WHERE id(n) IN [1,2]`` is accepted -- so a seed is
        re-selected by identity and node ids round-trip as the integers they are.
        There is no primary key to fall back on: LatticeDB has no schema, no node
        tables and no declared keys.
      - **Lowercase function names.** ``id``, ``labels``, ``type``, ``properties``.
        ``ID(n)`` is not the same function spelled louder -- it fails outright --
        which is why this cannot share the bolt family's tokens.
      - ``relationship_filter="where"``. Type alternation in a pattern is rejected
        with "Relationship type alternation (|) is not supported yet", so selected
        and hidden types go into ``type(r) IN [...]`` predicates instead.
      - ``rel_id_expr=id(r)``: edges have matchable identity too, so a relationship
        can be excluded by id -- something neither Kuzu nor Ladybug can do.
      - ``supports_only_between_selected=True``: ``id(m) IN [...]`` pins the far end.
      - ``return_vars=("n", "r", "m")`` for the same reason as the Kuzu family: an
        edge answers its own id, not its endpoints', so the nodes have to travel
        with it.
      - ``pattern_identifier_ok``: with no quoting syntax, only a bare identifier
        can go in a pattern. A label like ``Odd Label`` is legal to store and
        impossible to write there, so it moves to ``"Odd Label" IN labels(n)``,
        which is also what keeps a label out of the statement's syntax entirely.
      - ``rel_endpoints_projection``: an edge here does not carry its ends and
        cannot be asked for them -- ``startNode(r)`` and ``endNode(r)`` both fail,
        and ``properties(r)`` holds only user data. The pattern is the only thing
        that knows, so each edge's ends are named in the RETURN, which makes a row
        readable without knowing which statement produced it.
      - ``directed_patterns_only``: ``(n)-[r]-(m)`` answers the same edge twice with
        its ends swapped and no way to tell which orientation is real, so an
        undirected expand is served as one directed statement each way instead.
      - ``supports_multi_hop=False``, and this one is not a grammar limit but a
        crash. A chained pattern parses and answers correctly, but the projection
        grows by eight columns a hop, and at that width the engine corrupts memory:
        the two-hop statement this dialect would build segfaults CPython on its
        third execution against an open store, and three hops goes down sooner.
        One hop with the same projection survives repetition at any width tried.
        Measured against 0.14.0; the client can still walk out a hop at a time.
      - Pagination is ``SKIP n LIMIT m``. The other order parses and then quietly
        answers nothing, which is worse than an error.
    """
    return StatementDialect(
        name="latticedb",
        predicate="internal-id",
        node_id_expr=lambda var: f"id({var})",
        rel_id_expr=lambda var: f"id({var})",
        rel_type_expr=lambda var: f"type({var})",
        id_literal=numeric_id_literal,
        relationship_filter="where",
        return_vars=("n", "r", "m"),
        return_separator=", ",
        projection=_lattice_projection,
        rel_endpoints_projection=(
            lambda rel, source, target: f"id({source}) AS {rel}_src, id({target}) AS {rel}_dst"
        ),
        directed_patterns_only=True,
        pattern_identifier_ok=lambda name: bool(_BARE_IDENTIFIER.match(str(name))),
        label_predicate=lambda var, label: f"{cypher_string(label)} IN labels({var})",
        quote_identifier=lambda name: str(name),
        label_pattern=lambda var, label: f"{var}:{label}",
        supports_multi_hop=False,
        supports_only_between_selected=True,
    )


def _relational_dialect(name: str) -> StatementDialect:
    """
    MSSQL, PostgreSQL and MySQL share one token table, because the statement these
    dialects emit is never SQL: it is Cypher that ``drivers/relational/translate.py``
    reads and ``compile.py`` turns into per-engine SQL.

    So the per-engine differences (quoting, ``TOP`` / ``ROW_NUMBER`` / ``LIMIT``,
    parameter ceilings) are the compiler's and the engine spelling's job, and nothing
    here. What is here is the same identity problem RocketGraph and the embedded
    family have:

      - ``predicate="primary-key"``. A relational row is addressed by its key column;
        there is no node identity to re-select a seed by, so node ids are
        ``<Label>:<key>`` and a seed comes back as ``n.`<key>` IN [...]``.
      - ``key_access`` is the default, quoted. The bare form was justified as saving the
        translator one thing to unquote, but a statement this dialect emits is Cypher
        *this proxy parses again itself* — ``/expand`` builds one and hands it to
        ``query.prepare``, and in legacy mode the client builds its own — and its parser
        reads ``n.first name`` as one property plus trailing garbage and refuses it. So a
        key column holding a space, or any other token-breaker, could not be expanded at
        all. Quoting is what a column name like that costs, and it is what the client's
        own profile for these backends already emits, so both ends of the wire agree.
      - ``rel_type_expr=type(r)`` with ``relationship_filter="pattern"``: selected and
        hidden types can go in the pattern *and* be filtered in a predicate, because a
        type is a column of the link table rather than a thing the SQL has to know.
      - ``rel_id_expr=None``, so an edge cannot be excluded by id, and
        ``supports_only_between_selected=False``: a key predicate pins one end of a
        traversal and no SQL here can pin the other for free.
      - ``return_vars=("n", "r", "m")`` because a link row carries the two records it
        joins, and an edge's endpoint *ids* are minted from their keys.
      - ``node_id_expr`` is inert on every path a ``primary-key`` dialect takes; it is
        spelled like the engines' own so a reader is not left with a hole.
    """
    return StatementDialect(
        name=name,
        predicate="primary-key",
        node_id_expr=lambda var: f"id({var})",
        rel_id_expr=None,
        rel_type_expr=lambda var: f"type({var})",
        relationship_filter="pattern",
        return_vars=("n", "r", "m"),
        return_separator=", ",
        supports_multi_hop=True,
        supports_only_between_selected=False,
    )


#: LatticeDB is its own project rather than a fork of either other engine, and
#: shares no tokens with them beyond the word MATCH.
LATTICEDB_DIALECT = _lattice_dialect()

#: Kuzu, and the family it heads.
KUZU_DIALECT = _embedded_dialect("kuzu")

#: Ladybug forked Kuzu and kept its Cypher, so the statements are identical. What
#: differs is on either side of them: the magic bytes in the file and the key
#: casing in the results.
LADYBUG_DIALECT = _embedded_dialect("ladybug")

#: The three relational engines, one table and three names. SQL here is what
#: ``compile.py`` emits, never what a dialect token spells.
MSSQL_DIALECT = _relational_dialect("mssql")
POSTGRESQL_DIALECT = _relational_dialect("postgresql")
MYSQL_DIALECT = _relational_dialect("mysql")

DIALECTS: Dict[str, StatementDialect] = {
    "spanner": SPANNER_DIALECT,
    "bigquery": BIGQUERY_DIALECT,
    "rocketgraph": ROCKETGRAPH_DIALECT,
    "neo4j": NEO4J_DIALECT,
    "memgraph": MEMGRAPH_DIALECT,
    "kuzu": KUZU_DIALECT,
    "ladybug": LADYBUG_DIALECT,
    "latticedb": LATTICEDB_DIALECT,
    "mssql": MSSQL_DIALECT,
    "postgresql": POSTGRESQL_DIALECT,
    "mysql": MYSQL_DIALECT,
}


def dialect_for(database_type: str) -> StatementDialect:
    """The token table for a proxy database type, defaulting to Spanner GQL."""
    return DIALECTS.get(str(database_type).lower(), SPANNER_DIALECT)
