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

# Re-exported so every importer of ``drivers.dialect`` keeps working while the
# body sits in two modules, one for the shared machinery and one for the tables.
from .dialect_core import (
    NodeRef,
    StatementDialect,
    _ARROWS,
    _NUMERIC_INTERNAL_ID,
    _NUMERIC_KEY_TYPES,
    _NUMERIC_LITERAL,
    _endpoints_of,
    _exclude_loaded,
    _expand_by_id,
    _expand_by_key,
    _label_predicate,
    _match_clause,
    _node_chain,
    _pattern_types,
    _project,
    _relationship_pattern,
    _relationship_vars,
    _return_clause,
    _return_star,
    _type_predicates,
    _unique,
    _where_clause,
    backtick,
    build_expand,
    build_pull_category,
    build_pull_relationship,
    cypher_string,
    is_numeric_key_type,
    numeric_id_literal,
    project_variables,
    render_key_literal,
)
from .dialect_backends import (
    BIGQUERY_DIALECT,
    DIALECTS,
    KUZU_DIALECT,
    LADYBUG_DIALECT,
    LATTICEDB_DIALECT,
    MEMGRAPH_DIALECT,
    MSSQL_DIALECT,
    MYSQL_DIALECT,
    NEO4J_DIALECT,
    POSTGRESQL_DIALECT,
    ROCKETGRAPH_DIALECT,
    SPANNER_DIALECT,
    _BARE_IDENTIFIER,
    _bigquery_identity,
    _bolt_family_dialect,
    _embedded_dialect,
    _lattice_dialect,
    _lattice_projection,
    _relational_dialect,
    dialect_for,
)
