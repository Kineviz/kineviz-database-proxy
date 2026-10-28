"""
The Python dialect layer, asserted the same way the TypeScript one is.

Both ports build from the same token tables, so the statements have to agree —
the GraphXR client falls back to building them itself against an older proxy, and
the two must not diverge.
"""

from __future__ import annotations

import pytest

from kineviz_database_proxy.drivers.dialect import (
    BIGQUERY_DIALECT,
    KUZU_DIALECT,
    LADYBUG_DIALECT,
    LATTICEDB_DIALECT,
    MEMGRAPH_DIALECT,
    MSSQL_DIALECT,
    MYSQL_DIALECT,
    NEO4J_DIALECT,
    NodeRef,
    POSTGRESQL_DIALECT,
    ROCKETGRAPH_DIALECT,
    SPANNER_DIALECT,
    backtick,
    build_expand,
    build_pull_category,
    build_pull_relationship,
    cypher_string,
    dialect_for,
    is_numeric_key_type,
    numeric_id_literal,
    render_key_literal,
)

SPANNER_SEEDS = [NodeRef(category="Person", internal_id=str(i)) for i in (101, 102, 103)]
ROCKET_SEEDS = [
    NodeRef(category="Person", internal_id="Person:1", key_prop="id", key_value="1"),
    NodeRef(category="Person", internal_id="Person:2", key_prop="id", key_value="2"),
    NodeRef(category="Company", internal_id="Company:7", key_prop="id", key_value="7"),
]


def test_spanner_expand_matches_the_client_statement():
    (statement,) = build_expand(SPANNER_DIALECT, SPANNER_SEEDS, direction="all", limit=25, skip=0)
    assert statement == (
        'MATCH (n)-[r]-(m) FILTER WHERE ELEMENT_ID(n) IN UNNEST (["101","102","103"]) '
        "RETURN r,m SKIP 0 LIMIT 25"
    )


@pytest.mark.parametrize(
    "direction,pattern",
    [("all", "(n)-[r]-(m)"), ("from", "(n)-[r]->(m)"), ("to", "(n)<-[r]-(m)"), ("both", "(n)<-[r]->(m)")],
)
def test_direction_arrows(direction, pattern):
    (statement,) = build_expand(SPANNER_DIALECT, SPANNER_SEEDS, direction=direction, limit=10)
    assert pattern in statement


def test_relationship_types_go_into_the_pattern_for_spanner():
    (statement,) = build_expand(
        SPANNER_DIALECT, SPANNER_SEEDS, relationships=["WORKS_AT", "KNOWS"], limit=10
    )
    assert "-[r:`WORKS_AT`|`KNOWS`]-" in statement


def test_multi_hop_chains_the_pattern_and_returns_the_intermediates():
    (statement,) = build_expand(SPANNER_DIALECT, SPANNER_SEEDS, hops=2, limit=10)
    assert "(n)-[r]-(n1)-[r1]-(m)" in statement
    assert statement.endswith("RETURN r,m,n1,r1 SKIP 0 LIMIT 10")


def test_only_between_selected_constrains_the_far_endpoint():
    (statement,) = build_expand(SPANNER_DIALECT, SPANNER_SEEDS, only_between_selected=True, limit=10)
    assert "AND ELEMENT_ID(m) <> ELEMENT_ID(n)" in statement
    assert "AND ELEMENT_ID(m) IN UNNEST" in statement


def test_excluded_relationship_ids_are_filtered_when_the_backend_can():
    (statement,) = build_expand(
        SPANNER_DIALECT, SPANNER_SEEDS, exclude_relationship_ids=["901", "902"], limit=10
    )
    assert 'AND NOT(ELEMENT_ID(r) IN UNNEST (["901","902"]))' in statement

    # RocketGraph has no edge identity, so the filter is silently unavailable
    # rather than emitted as broken SQL.
    (rocket,) = build_expand(
        ROCKETGRAPH_DIALECT, ROCKET_SEEDS[:1], exclude_relationship_ids=["901"], direction="from", limit=10
    )
    assert "901" not in rocket


def test_rocketgraph_expands_by_key_one_statement_per_category():
    statements = build_expand(
        ROCKETGRAPH_DIALECT,
        ROCKET_SEEDS,
        direction="from",
        limit=25,
        key_types={"Person": "TEXT", "Company": "TEXT"},
    )
    assert statements == [
        'MATCH (n:`Person`)-[r]->(m) WHERE n.`id` IN ["1","2"] RETURN n, r, m SKIP 0 LIMIT 25',
        'MATCH (n:`Company`)-[r]->(m) WHERE n.`id` IN ["7"] RETURN n, r, m SKIP 0 LIMIT 25',
    ]


def test_a_numeric_key_type_emits_bare_literals():
    statements = build_expand(
        ROCKETGRAPH_DIALECT, ROCKET_SEEDS[:2], direction="from", limit=10, key_types={"Person": "INT64"}
    )
    assert "n.`id` IN [1,2]" in statements[0]


def test_duplicate_keys_collapse():
    duplicated = [*ROCKET_SEEDS[:2], NodeRef(category="Person", key_prop="id", key_value="1")]
    statements = build_expand(ROCKETGRAPH_DIALECT, duplicated, direction="from", limit=10)
    assert statements[0].count('"1"') == 1


def test_category_narrowing_drops_the_other_categories():
    statements = build_expand(
        ROCKETGRAPH_DIALECT, ROCKET_SEEDS, direction="from", limit=10, category="Company"
    )
    assert len(statements) == 1
    assert "n:`Company`" in statements[0]


def test_no_seeds_means_no_statement():
    assert build_expand(SPANNER_DIALECT, [], limit=10) == []
    assert build_expand(ROCKETGRAPH_DIALECT, [], limit=10) == []


def test_pull_category_excludes_what_is_already_loaded():
    (statement,) = build_pull_category(
        SPANNER_DIALECT, "Person", limit=1000, loaded=[NodeRef(category="Person", internal_id="101")]
    )
    assert statement == (
        'MATCH (n:`Person`) FILTER WHERE NOT ELEMENT_ID(n) IN UNNEST (["101"]) RETURN * SKIP 0 LIMIT 1000'
    )

    (bare,) = build_pull_category(SPANNER_DIALECT, "Person", limit=1000)
    assert "NOT ELEMENT_ID" not in bare


def test_pull_category_excludes_by_key_for_a_key_backend():
    (statement,) = build_pull_category(
        ROCKETGRAPH_DIALECT,
        "Person",
        limit=1000,
        loaded=[NodeRef(category="Person", key_prop="id", key_value="1")],
        key_types={"Person": "TEXT"},
    )
    assert 'NOT n.`id` IN ["1"]' in statement


def test_pull_relationship_excludes_loaded_edges_only_where_possible():
    (statement,) = build_pull_relationship(SPANNER_DIALECT, "WORKS_AT", limit=1000, loaded_ids=["2:0"])
    assert 'NOT ELEMENT_ID(r) IN UNNEST (["2:0"])' in statement

    (rocket,) = build_pull_relationship(ROCKETGRAPH_DIALECT, "WORKS_AT", limit=1000, loaded_ids=["2:0"])
    assert "NOT" not in rocket


def test_identifier_and_literal_quoting():
    assert backtick("first name") == "`first name`"
    assert backtick("we`ird") == "`we``ird`"
    assert cypher_string('say "hi"') == '"say \\"hi\\""'


def test_numeric_key_types_cover_every_spelling():
    for name in ("INT64", "int8", "BIGINT", "numeric", "NUMERIC(10,2)", "SERIAL"):
        assert is_numeric_key_type(name), name
    for name in ("STRING", "STRING(36)", "TEXT", "BOOL", None, ""):
        assert not is_numeric_key_type(name), name


def test_a_numeric_column_holding_text_is_still_quoted():
    # So the backend reports a type error rather than a syntax error.
    assert render_key_literal("abc", "INT64", cypher_string) == '"abc"'
    assert render_key_literal("42", "INT64", cypher_string) == "42"


def test_dialect_lookup_defaults_to_spanner():
    assert dialect_for("rocketgraph") is ROCKETGRAPH_DIALECT
    assert dialect_for("spanner") is SPANNER_DIALECT
    assert dialect_for("bigquery") is BIGQUERY_DIALECT
    assert dialect_for("neo4j") is NEO4J_DIALECT
    assert dialect_for("memgraph") is MEMGRAPH_DIALECT
    assert dialect_for("something-else") is SPANNER_DIALECT


# ---------------------------------------------------------------------------
# BigQuery — Spanner's GQL without ELEMENT_ID()
# ---------------------------------------------------------------------------


def test_bigquery_reads_identity_out_of_the_elements_json():
    (statement,) = build_expand(BIGQUERY_DIALECT, SPANNER_SEEDS, direction="all", limit=25, skip=0)
    assert statement == (
        "MATCH (n)-[r]-(m) FILTER WHERE JSON_VALUE(TO_JSON(n), '$.identifier') "
        'IN UNNEST (["101","102","103"]) RETURN r,m SKIP 0 LIMIT 25'
    )


def test_bigquery_is_spanner_in_every_other_respect():
    for dialect in (BIGQUERY_DIALECT, SPANNER_DIALECT):
        assert dialect.where_keyword == "FILTER WHERE"
        assert dialect.relationship_filter == "pattern"
        assert dialect.rel_type_expr is None
        assert dialect.id_list(['"1"']) == 'UNNEST (["1"])'


# ---------------------------------------------------------------------------
# The bolt family — Neo4j's tokens, shared by Memgraph
# ---------------------------------------------------------------------------

BOLT_SEEDS = [NodeRef(internal_id=str(i)) for i in (101, 102)]


def test_neo4j_expand_matches_the_client_bolt_profile():
    (statement,) = build_expand(NEO4J_DIALECT, BOLT_SEEDS, direction="all", limit=25, skip=0)
    assert statement == "MATCH (n)-[r]-(m) WHERE ID(n) IN [101,102] RETURN r,m SKIP 0 LIMIT 25"


def test_memgraph_emits_exactly_what_neo4j_emits():
    # Memgraph is bolt-compatible: same statements, so the two token tables differ
    # only in their name.
    (neo4j_statement,) = build_expand(NEO4J_DIALECT, BOLT_SEEDS, direction="from", limit=10)
    (memgraph_statement,) = build_expand(MEMGRAPH_DIALECT, BOLT_SEEDS, direction="from", limit=10)
    assert neo4j_statement == memgraph_statement


def test_bolt_ids_are_emitted_bare_so_64_bit_values_keep_their_precision():
    big = "1152921504606846979"
    assert numeric_id_literal(big) == big
    (statement,) = build_expand(NEO4J_DIALECT, [NodeRef(internal_id=big)], limit=10)
    assert f"ID(n) IN [{big}]" in statement


def test_a_non_numeric_bolt_id_is_quoted_rather_than_pasted_raw():
    assert numeric_id_literal("4:abc:1") == '"4:abc:1"'


def test_neo4j_filters_relationship_types_in_a_predicate_not_the_pattern():
    # Unlike the GQL backends, TYPE(r) exists, so both the selected and the hidden
    # types can be expressed without touching the pattern.
    (statement,) = build_expand(
        NEO4J_DIALECT,
        BOLT_SEEDS,
        relationships=["KNOWS"],
        exclude_relationship_types=["HIDDEN"],
        limit=10,
    )
    assert "-[r]-" in statement
    assert 'AND NOT(TYPE(r) IN ["HIDDEN"])' in statement
    assert 'AND TYPE(r) IN ["KNOWS"]' in statement


def test_neo4j_can_pin_a_traversal_to_the_selected_node_set():
    (statement,) = build_expand(NEO4J_DIALECT, BOLT_SEEDS, only_between_selected=True, limit=10)
    assert "AND ID(m) <> ID(n)" in statement
    assert "AND ID(m) IN [101,102]" in statement


def test_neo4j_excludes_relationships_already_on_the_canvas():
    (statement,) = build_expand(NEO4J_DIALECT, BOLT_SEEDS, exclude_relationship_ids=["901"], limit=10)
    assert "AND NOT(ID(r) IN [901])" in statement


def test_neo4j_pull_statements_exclude_by_internal_id():
    (category,) = build_pull_category(
        NEO4J_DIALECT, "Person", limit=1000, loaded=[NodeRef(internal_id="101")]
    )
    assert category == "MATCH (n:`Person`) WHERE NOT ID(n) IN [101] RETURN * SKIP 0 LIMIT 1000"

    (relationship,) = build_pull_relationship(NEO4J_DIALECT, "WORKS_AT", limit=1000, loaded_ids=["9"])
    assert relationship == (
        "MATCH (n)-[r:`WORKS_AT`]->(m) WHERE NOT ID(r) IN [9] RETURN * SKIP 0 LIMIT 1000"
    )


# ---------------------------------------------------------------------------
# Kuzu and Ladybug
#
# Every statement asserted here was run against kuzu 0.11.3 and ladybug 0.19.1 on
# 2026-08-27 and parsed on both. The engines are one codebase with two names, so
# the two dialects must agree token for token.
# ---------------------------------------------------------------------------

KUZU_SEEDS = [
    NodeRef(category="Person", internal_id="Person:Alice", key_prop="name", key_value="Alice"),
]


def test_kuzu_expands_by_key_because_there_is_no_writable_identity():
    # ID(n) reads back as an INTERNAL_ID but `n._id` is rejected as "reserved for
    # system usage", so a seed can only be re-selected by its primary key.
    (statement,) = build_expand(KUZU_DIALECT, KUZU_SEEDS, direction="all", limit=25, skip=0)
    assert statement == (
        'MATCH (n:`Person`)-[r]-(m) WHERE n.`name` IN ["Alice"] '
        "RETURN n, r, m SKIP 0 LIMIT 25"
    )


def test_kuzu_returns_both_endpoint_nodes_with_every_edge():
    # An edge carries its endpoints as internal ids; turning those into
    # <Label>:<key> needs the nodes themselves, so dropping `n` from the RETURN
    # would leave every relationship unplaceable.
    (statement,) = build_expand(KUZU_DIALECT, KUZU_SEEDS, limit=25, skip=0)
    assert statement.count("RETURN n, r, m") == 1


def test_kuzu_multi_hop_chains_the_pattern_and_returns_every_node_on_it():
    # A regression: the key-predicate path used to pin itself to one hop, so a
    # two-hop expand quietly answered with a one-hop graph.
    (statement,) = build_expand(KUZU_DIALECT, KUZU_SEEDS, hops=2, limit=25, skip=0)
    assert "MATCH (n:`Person`)-[r]-(n1)-[r1]-(m)" in statement
    assert statement.count("RETURN n, r, m, n1, r1") == 1


def test_rocketgraph_stays_single_hop_even_when_hops_are_asked_for():
    # It declares multiHop false and its dialect says so; the shared builder must
    # keep honouring that.
    (statement,) = build_expand(ROCKETGRAPH_DIALECT, ROCKET_SEEDS[:1], hops=3, limit=5, skip=0)
    assert "n1" not in statement


def test_kuzu_hides_relationship_types_in_a_predicate():
    # `label(r)` is verified in both RETURN and WHERE on both engines. A backend
    # without a type function -- RocketGraph -- emits nothing here.
    (statement,) = build_expand(
        KUZU_DIALECT, KUZU_SEEDS, exclude_relationship_types=["Hidden"], limit=25, skip=0
    )
    assert 'AND NOT(label(r) IN ["Hidden"])' in statement

    (rocket,) = build_expand(
        ROCKETGRAPH_DIALECT, ROCKET_SEEDS[:1], exclude_relationship_types=["Hidden"], limit=5, skip=0
    )
    assert "Hidden" not in rocket


def test_kuzu_puts_selected_relationship_types_in_the_pattern():
    (statement,) = build_expand(
        KUZU_DIALECT, KUZU_SEEDS, relationships=["LivesIn"], limit=25, skip=0
    )
    assert "-[r:`LivesIn`]-" in statement


def test_kuzu_emits_a_numeric_key_bare():
    seeds = [NodeRef(category="City", internal_id="City:1", key_prop="cid", key_value="1")]
    (statement,) = build_expand(
        KUZU_DIALECT, seeds, direction="to", limit=25, skip=0, key_types={"City": "INT64"}
    )
    assert "n.`cid` IN [1]" in statement


def test_kuzu_pagination_is_skip_then_limit_which_is_the_only_order_that_parses():
    (statement,) = build_pull_category(KUZU_DIALECT, "Person", limit=5, skip=10)
    assert statement.endswith("RETURN * SKIP 10 LIMIT 5")


def test_kuzu_pull_category_excludes_loaded_nodes_by_key():
    (statement,) = build_pull_category(KUZU_DIALECT, "Person", limit=5, skip=0, loaded=KUZU_SEEDS)
    assert 'WHERE NOT n.`name` IN ["Alice"]' in statement


def test_kuzu_cannot_exclude_loaded_edges_because_there_is_no_edge_identity():
    (statement,) = build_pull_relationship(KUZU_DIALECT, "LivesIn", limit=5, skip=0, loaded_ids=["x"])
    assert statement == "MATCH (n)-[r:`LivesIn`]->(m) RETURN * SKIP 0 LIMIT 5"


def test_ladybug_emits_exactly_what_kuzu_emits():
    # One engine, two names. A difference here would be a bug in one of them.
    for build in (
        lambda d: build_expand(d, KUZU_SEEDS, direction="all", hops=2, limit=25, skip=0),
        lambda d: build_pull_category(d, "Person", limit=5, skip=0, loaded=KUZU_SEEDS),
        lambda d: build_pull_relationship(d, "LivesIn", limit=5, skip=0),
    ):
        assert build(KUZU_DIALECT) == build(LADYBUG_DIALECT)


def test_dialect_lookup_reaches_the_embedded_pair():
    assert dialect_for("kuzu") is KUZU_DIALECT
    assert dialect_for("ladybug") is LADYBUG_DIALECT


# -- LatticeDB ---------------------------------------------------------------
#
# Every statement asserted here was executed against latticedb 0.14.0 before it
# was written down. The engine's grammar is narrower than the word "Cypher"
# suggests, and each of these encodes one thing it refuses.

LATTICE_SEEDS = [NodeRef(category="Person", internal_id="1"), NodeRef(category="Person", internal_id="2")]


def test_latticedb_selects_seeds_by_identity_unlike_the_other_embedded_engines():
    """``id(n)`` matches as well as reads here, so there is no key detour."""
    statement = build_expand(LATTICEDB_DIALECT, LATTICE_SEEDS, limit=10)[0]
    assert "WHERE id(n) IN [1,2]" in statement


def test_latticedb_function_names_are_lowercase_because_the_uppercase_ones_fail():
    # ID(n) is not id(n) shouted; it is not a function LatticeDB has.
    statement = build_expand(LATTICEDB_DIALECT, LATTICE_SEEDS, limit=10)[0]
    assert "ID(" not in statement and "LABELS(" not in statement and "TYPE(" not in statement


def test_latticedb_projects_the_parts_because_a_bare_variable_is_only_an_id():
    """``RETURN n`` is accepted and answers ``{"n": 1}`` -- the id, and nothing else."""
    statement = build_expand(LATTICEDB_DIALECT, LATTICE_SEEDS, direction="from", limit=10)[0]
    assert (
        "RETURN id(n), labels(n), properties(n), "
        "id(r), type(r), properties(r), id(n) AS r_src, id(m) AS r_dst, "
        "id(m), labels(m), properties(m)"
    ) in statement


def test_latticedb_names_each_edges_endpoints_because_the_edge_does_not_carry_them():
    """
    There is no ``startNode(r)`` here, and ``properties(r)`` holds only user data,
    so the only thing that knows an edge's ends is the pattern that matched it.
    Naming them in the RETURN is what makes a row self-describing.
    """
    forward = build_expand(LATTICEDB_DIALECT, LATTICE_SEEDS, direction="from", limit=10)[0]
    backward = build_expand(LATTICEDB_DIALECT, LATTICE_SEEDS, direction="to", limit=10)[0]

    assert "id(n) AS r_src, id(m) AS r_dst" in forward
    # Reversing the arrow reverses which variable is the source.
    assert "id(m) AS r_src, id(n) AS r_dst" in backward


def test_latticedb_splits_an_undirected_expand_into_one_statement_per_direction():
    """
    ``(n)-[r]-(m)`` answers the same edge twice with its ends swapped, and no row
    says which orientation is the real one. Two directed statements do.
    """
    for direction in ("all", "both"):
        statements = build_expand(
            LATTICEDB_DIALECT, LATTICE_SEEDS, direction=direction, limit=10
        )
        assert len(statements) == 2
        assert "(n)-[r]->(m)" in statements[0]
        assert "(n)<-[r]-(m)" in statements[1]
        # Never the ambiguous form.
        assert not any("(n)-[r]-(m)" in statement for statement in statements)


def test_latticedb_filters_relationship_types_in_a_predicate_not_the_pattern():
    # "Relationship type alternation (|) is not supported yet".
    statement = build_expand(
        LATTICEDB_DIALECT, LATTICE_SEEDS, relationships=["KNOWS", "LIVES_IN"], limit=10
    )[0]
    assert "[r]" in statement
    assert 'type(r) IN ["KNOWS","LIVES_IN"]' in statement


def test_latticedb_can_exclude_an_edge_by_id_which_kuzu_cannot():
    statement = build_expand(
        LATTICEDB_DIALECT, LATTICE_SEEDS, exclude_relationship_ids=["7"], limit=10
    )[0]
    assert "NOT(id(r) IN [7])" in statement


def test_latticedb_can_pin_both_ends_of_a_traversal():
    statement = build_expand(
        LATTICEDB_DIALECT, LATTICE_SEEDS, only_between_selected=True, limit=10
    )[0]
    assert "id(m) <> id(n)" in statement and "id(m) IN [1,2]" in statement


def test_latticedb_offers_all_four_directions():
    """
    All four are answerable, but only two are ever written: the undirected pair is
    served as one statement each way, so no ambiguous arrow reaches the engine.
    """
    assert build_expand(LATTICEDB_DIALECT, LATTICE_SEEDS, direction="from", limit=10) == [
        st for st in build_expand(LATTICEDB_DIALECT, LATTICE_SEEDS, direction="from", limit=10)
    ]
    assert "(n)-[r]->(m)" in build_expand(
        LATTICEDB_DIALECT, LATTICE_SEEDS, direction="from", limit=10
    )[0]
    assert "(n)<-[r]-(m)" in build_expand(
        LATTICEDB_DIALECT, LATTICE_SEEDS, direction="to", limit=10
    )[0]
    assert len(build_expand(LATTICEDB_DIALECT, LATTICE_SEEDS, direction="all", limit=10)) == 2
    assert len(build_expand(LATTICEDB_DIALECT, LATTICE_SEEDS, direction="both", limit=10)) == 2


def test_latticedb_writes_a_plain_label_into_the_pattern():
    statement = build_pull_category(LATTICEDB_DIALECT, "Person", limit=10)[0]
    assert "MATCH (n:Person)" in statement
    # No backticks anywhere: they are an "Invalid token" to this parser.
    assert "`" not in statement


def test_latticedb_moves_a_label_it_cannot_write_into_a_predicate():
    """
    ``Odd Label`` is a legal label and an impossible pattern -- there is no quoting
    syntax to put it in one. It becomes a string literal in a predicate instead,
    which also keeps a stored label out of the statement's syntax entirely.
    """
    statement = build_pull_category(LATTICEDB_DIALECT, "Odd Label", limit=10)[0]
    assert "MATCH (n) WHERE \"Odd Label\" IN labels(n)" in statement


def test_latticedb_moves_a_relationship_type_it_cannot_write_into_a_predicate():
    statement = build_pull_relationship(LATTICEDB_DIALECT, "IS FRIEND OF", limit=10)[0]
    assert "MATCH (n)-[r]->(m)" in statement
    assert 'WHERE type(r) IN ["IS FRIEND OF"]' in statement


def test_latticedb_never_returns_star_because_the_parser_rejects_it():
    for statement in (
        build_pull_category(LATTICEDB_DIALECT, "Person", limit=10)[0],
        build_pull_relationship(LATTICEDB_DIALECT, "KNOWS", limit=10)[0],
    ):
        assert "RETURN *" not in statement
        assert "RETURN id(" in statement


def test_latticedb_excludes_loaded_nodes_and_edges_by_id():
    category = build_pull_category(
        LATTICEDB_DIALECT, "Person", limit=10, loaded=[NodeRef(category="Person", internal_id="1")]
    )[0]
    assert "WHERE NOT id(n) IN [1]" in category

    relationship = build_pull_relationship(LATTICEDB_DIALECT, "KNOWS", limit=10, loaded_ids=["3"])[0]
    assert "WHERE NOT id(r) IN [3]" in relationship


def test_latticedb_pagination_is_skip_then_limit():
    # LIMIT before SKIP parses and then quietly answers nothing.
    statement = build_pull_category(LATTICEDB_DIALECT, "Person", skip=20, limit=10)[0]
    assert statement.endswith("SKIP 20 LIMIT 10")


def test_latticedb_stays_at_one_hop_because_the_engine_crashes_past_it():
    """
    A chained pattern parses and answers correctly here, so this is not a grammar
    limit. The projection grows by eight columns a hop, and at two hops the
    statement segfaults latticedb 0.14.0 on its third execution against an open
    store. Asking for more hops must not produce a longer pattern.
    """
    statement = build_expand(
        LATTICEDB_DIALECT, LATTICE_SEEDS, hops=3, direction="from", limit=10
    )[0]
    assert "(n)-[r]->(m)" in statement
    assert "n1" not in statement and "r1" not in statement


def test_dialect_lookup_reaches_latticedb():
    assert dialect_for("latticedb") is LATTICEDB_DIALECT


def test_latticedb_shares_no_tokens_with_the_kuzu_family():
    """It is a separate project, not a third fork, and the statements show it."""
    lattice = build_expand(LATTICEDB_DIALECT, LATTICE_SEEDS, limit=10)[0]
    kuzu = build_expand(
        KUZU_DIALECT,
        [NodeRef(category="Person", internal_id="Person:1", key_prop="id", key_value="1")],
        limit=10,
    )[0]
    assert lattice != kuzu
    assert "label(" not in lattice  # Kuzu's spelling of type()
    assert "properties(" not in kuzu


def test_relational_backends_address_a_seed_by_a_quoted_key_column():
    """
    A relational key predicate quotes its column, because this proxy parses it again.

    ``drivers/relational`` reads the Cypher ``dialect.py`` writes, so a bare ``n.first
    name`` or ``n.order`` is a statement the proxy refuses on its own way to SQL. The
    quoted default every other backend already uses is what survives that round trip.
    """
    for dialect in (MSSQL_DIALECT, POSTGRESQL_DIALECT, MYSQL_DIALECT):
        assert dialect.key_access("n", "first name") == "n.`first name`"
        assert dialect.key_access("n", "order") == "n.`order`"
        assert dialect.key_access("n", "person_id") == "n.`person_id`"
