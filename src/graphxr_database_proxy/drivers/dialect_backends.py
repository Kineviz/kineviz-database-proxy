"""
One ``StatementDialect`` token table per proxy backend, and the map that
picks one by database type.

The builders these tables are handed to live in ``dialect_core.py``.
"""

from __future__ import annotations

import re
from typing import Dict

from .dialect_core import StatementDialect, cypher_string, numeric_id_literal

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
