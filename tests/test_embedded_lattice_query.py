# -*- coding: utf-8 -*-
"""
Turning a statement a Cypher client wrote into one LatticeDB can answer.

Three things any other backend here accepts have no spelling in this grammar.
``RETURN *`` stops at the parser. ``RETURN n, r, m`` parses and answers three
integers, because there is no node value for a bare name to stand for. And a
backtick is an invalid token wherever it appears, which is what GraphXR's search
builder puts around every label and every type it emits.

The refusals matter as much as the rewrites: this touches statements someone else
wrote, and a rewrite it got wrong would return wrong data where the engine would
have returned an error. Every case below that is left alone is left alone on
purpose.
"""

from __future__ import annotations

from kineviz_database_proxy.drivers.embedded.lattice_query import (
    explain,
    move_unquotable_labels,
    prepare_statement,
    rewrite_for_projection,
    unquote_identifiers,
)

#: Spelled rather than typed, so the fixtures read as the statements they are and
#: no editor turns them into anything else.
TICK = chr(96)


def ticked(text: str) -> str:
    """``text`` with every ~ turned into a backtick."""
    return text.replace("~", TICK)


#: What the dialect projects for a directed one-hop pattern, and what the engine
#: was verified to accept.
ONE_HOP = (
    "RETURN id(n), labels(n), properties(n), "
    "id(r), type(r), properties(r), id(n) AS r_src, id(m) AS r_dst, "
    "id(m), labels(m), properties(m)"
)


# -- the queries people actually type ---------------------------------------


def test_the_first_query_anyone_types_is_rewritten():
    # Verbatim from this proxy's own API documentation, and verbatim what it
    # answered "Expected expression" to.
    assert rewrite_for_projection("MATCH (n)-[r]->(m) RETURN * LIMIT 100") == (
        "MATCH (n)-[r]->(m) " + ONE_HOP + " LIMIT 100"
    )


def test_naming_the_variables_is_rewritten_too():
    """
    The trap this closes. ``RETURN n, r, m`` is what someone tries after ``*`` is
    refused; it parses and answers ``{"n": 1, "r": 1, "m": 2}``, which is not an
    error and not a graph either.
    """
    assert rewrite_for_projection("MATCH (n)-[r]->(m) RETURN n, r, m") == (
        "MATCH (n)-[r]->(m) " + ONE_HOP
    )


def test_a_pattern_of_nodes_alone_is_rewritten():
    expected = "MATCH (n:Person) RETURN id(n), labels(n), properties(n)"
    assert rewrite_for_projection("MATCH (n:Person) RETURN *") == expected
    assert rewrite_for_projection("MATCH (n:Person) RETURN n") == expected


def test_the_where_clause_and_the_tail_are_kept_as_written():
    rewritten = rewrite_for_projection(
        "MATCH (n:Person)-[r:LivesIn]->(m:City) WHERE n.name = 'Alice' "
        "RETURN * ORDER BY id(n) SKIP 5 LIMIT 10"
    )
    assert rewritten == (
        "MATCH (n:Person)-[r:LivesIn]->(m:City) WHERE n.name = 'Alice' "
        + ONE_HOP
        + " ORDER BY id(n) SKIP 5 LIMIT 10"
    )


def test_keywords_are_recognised_whatever_their_case():
    assert rewrite_for_projection("match (n)-[r]->(m) return * limit 5") == (
        "match (n)-[r]->(m) " + ONE_HOP + " limit 5"
    )


def test_a_trailing_semicolon_does_not_prevent_the_rewrite():
    assert rewrite_for_projection("MATCH (n) RETURN *;") == (
        "MATCH (n) RETURN id(n), labels(n), properties(n)"
    )


# -- direction --------------------------------------------------------------


def test_the_arrow_decides_the_endpoints_not_the_order_written():
    """
    ``(m)<-[r]-(n)`` binds the same three variables as ``(n)-[r]->(m)`` and means
    the opposite edge. Reading the ends off the pattern order rather than the arrow
    would place every relationship backwards.
    """
    rewritten = rewrite_for_projection("MATCH (m)<-[r]-(n) RETURN *")
    assert "id(n) AS r_src, id(m) AS r_dst" in rewritten


def test_an_undirected_pattern_is_left_alone():
    # Nothing in the row would say which way the edge runs, and the ends cannot be
    # read off the edge itself. Half the rows would be silently reversed.
    query = "MATCH (n)-[r]-(m) RETURN *"
    assert rewrite_for_projection(query) == query


def test_a_variable_length_relationship_is_left_alone():
    # It matches a path, and a path has no single pair of ends to project.
    query = "MATCH (n)-[r*1..3]->(m) RETURN *"
    assert rewrite_for_projection(query) == query


def test_a_relationship_with_an_unnamed_end_is_left_alone():
    query = "MATCH (n)-[r]->() RETURN *"
    assert rewrite_for_projection(query) == query


def test_each_relationship_gets_its_own_endpoints():
    rewritten = rewrite_for_projection("MATCH (a)-[r]->(b)<-[s]-(c) RETURN *")
    assert "id(a) AS r_src, id(b) AS r_dst" in rewritten
    assert "id(c) AS s_src, id(b) AS s_dst" in rewritten


def test_returning_only_the_relationship_still_names_its_ends():
    # An edge that cannot be placed is dropped by the mapper, so the endpoint
    # columns are projected even when the caller did not ask for the nodes.
    rewritten = rewrite_for_projection("MATCH (n)-[r]->(m) RETURN r")
    assert rewritten == (
        "MATCH (n)-[r]->(m) RETURN id(r), type(r), properties(r), "
        "id(n) AS r_src, id(m) AS r_dst"
    )


# -- everything it refuses to touch -----------------------------------------


def test_a_projection_the_user_wrote_is_left_alone():
    for query in (
        "MATCH (n) RETURN n.name",
        "MATCH (n) RETURN count(n)",
        "MATCH (n) RETURN n AS person",
        "MATCH (n) RETURN DISTINCT n",
        "MATCH (n) RETURN id(n), labels(n), properties(n)",
    ):
        assert rewrite_for_projection(query) == query


def test_a_variable_the_pattern_did_not_bind_is_left_alone():
    query = "MATCH (n)-[r]->(m) RETURN n, other"
    assert rewrite_for_projection(query) == query


def test_a_query_with_a_second_source_of_variables_is_left_alone():
    for query in (
        "MATCH (n)-[r]->(m) WITH n RETURN *",
        "MATCH (n) OPTIONAL MATCH (n)-[r]->(m) RETURN *",
        "MATCH (n) RETURN * UNION MATCH (m) RETURN *",
        "UNWIND [1, 2] AS x MATCH (n) RETURN *",
        "MATCH (n) MATCH (m) RETURN *",
        "CREATE (n:Person) RETURN *",
    ):
        assert rewrite_for_projection(query) == query


def test_a_statement_with_no_match_or_no_return_is_left_alone():
    for query in ("RETURN 1", "MATCH (n)", "", "   "):
        assert rewrite_for_projection(query) == query


def test_an_unbalanced_pattern_is_left_alone():
    query = "MATCH (n)-[r->(m) RETURN *"
    assert rewrite_for_projection(query) == query


def test_a_bracket_inside_a_string_does_not_confuse_the_scan():
    rewritten = rewrite_for_projection("MATCH (n {name: 'a)b'})-[r]->(m) RETURN *")
    assert rewritten == "MATCH (n {name: 'a)b'})-[r]->(m) " + ONE_HOP


# -- when it could not help -------------------------------------------------


def test_the_parse_error_arrives_with_the_reason_attached():
    message = explain("MATCH (n)-[r]-(m) RETURN *", "Expected expression")
    assert message.startswith("Expected expression")
    assert "no * in its grammar" in message
    assert "id(n) AS r_src" in message


def test_the_advice_covers_count_star_as_well():
    assert "count(*)" in explain("MATCH (n) RETURN count(*)", "Expected expression")


def test_an_unrelated_error_is_passed_through_untouched():
    assert explain("MATCH (n) RETURN n.name", "no such property") == "no such property"


def test_a_parse_error_with_no_star_in_it_is_passed_through_untouched():
    # The engine says "Expected expression" about more than one thing; only a
    # statement that actually holds a star gets the star explanation.
    assert (
        explain("MATCH (n) RETURN n.", "Expected expression") == "Expected expression"
    )


# -- quoting ----------------------------------------------------------------


def test_the_statement_graphxr_sends_is_accepted():
    """
    Verbatim from the search builder, which quotes every label and every type.
    LatticeDB answers "Invalid token" to the first backtick, before it looks at
    anything else.
    """
    sent = ticked(
        "MATCH (n0:~Document~)-[r0:~CITES~]->(n1:~Document~) "
        "RETURN n0, n1, r0 LIMIT 2000"
    )
    assert prepare_statement(sent) == (
        "MATCH (n0:Document)-[r0:CITES]->(n1:Document) RETURN "
        "id(n0), labels(n0), properties(n0), "
        "id(n1), labels(n1), properties(n1), "
        "id(r0), type(r0), properties(r0), id(n0) AS r0_src, id(n1) AS r0_dst "
        "LIMIT 2000"
    )


def test_quoting_comes_off_wherever_it_appears():
    # Not confined to the shapes the projection rewrite understands: a property key
    # and a variable are quoted by the same habit and rejected the same way.
    assert unquote_identifiers(ticked("MATCH (~n~) RETURN ~n~.~title~")) == (
        "MATCH (n) RETURN n.title"
    )


def test_a_backtick_inside_a_string_is_left_where_it_is():
    query = ticked("MATCH (n:~Person~) WHERE n.name = 'a~b' RETURN id(n)")
    assert unquote_identifiers(query) == ticked(
        "MATCH (n:Person) WHERE n.name = 'a~b' RETURN id(n)"
    )


def test_an_unbalanced_backtick_is_left_alone():
    query = ticked("MATCH (n:~Person) RETURN id(n)")
    assert unquote_identifiers(query) == query


# -- names a pattern cannot carry -------------------------------------------


def test_a_label_with_a_space_becomes_a_predicate():
    """
    ```Odd Label``` has no spelling in a LatticeDB pattern: quoted it is an invalid
    token, unquoted it ends the node pattern early. Only a value can hold it.
    """
    assert prepare_statement(ticked("MATCH (n0:~Odd Label~) RETURN n0 LIMIT 10")) == (
        'MATCH (n0) WHERE "Odd Label" IN labels(n0) '
        "RETURN id(n0), labels(n0), properties(n0) LIMIT 10"
    )


def test_a_relationship_type_with_a_space_becomes_a_predicate():
    rewritten = move_unquotable_labels(
        ticked("MATCH (n0)-[r0:~IS FRIEND OF~]->(n1) RETURN *")
    )
    assert rewritten == (
        'MATCH (n0)-[r0]->(n1) WHERE type(r0) IN ["IS FRIEND OF"] RETURN *'
    )


def test_an_existing_condition_is_parenthesised_before_the_predicate_joins_it():
    # ``a OR b AND predicate`` binds the wrong way round; the parentheses are the
    # difference between filtering and quietly widening the match.
    rewritten = move_unquotable_labels(
        ticked("MATCH (n0:~Odd Label~) WHERE n0.a = 1 OR n0.b = 2 RETURN n0")
    )
    assert rewritten == (
        'MATCH (n0) WHERE (n0.a = 1 OR n0.b = 2) AND "Odd Label" IN labels(n0) '
        "RETURN n0"
    )


def test_a_bare_label_keeps_its_place_in_the_pattern():
    # Only a name the pattern cannot carry is moved. ``Document`` is carried fine,
    # and turning it into a predicate would be a slower query for no reason.
    assert move_unquotable_labels(ticked("MATCH (n0:~Document~) RETURN n0")) == (
        ticked("MATCH (n0:~Document~) RETURN n0")
    )


def test_a_label_with_no_variable_to_hang_a_predicate_on_is_left_alone():
    query = ticked("MATCH (:~Odd Label~)-[r]->(m) RETURN r")
    assert move_unquotable_labels(query) == query


def test_a_group_holding_more_than_the_label_is_left_alone():
    # A property map or a second label puts the group outside what this takes apart.
    for query in (
        ticked("MATCH (n0:~Odd Label~ {x: 1}) RETURN n0"),
        ticked("MATCH (n0:~Odd Label~:Other) RETURN n0"),
    ):
        assert move_unquotable_labels(query) == query


def test_the_passes_run_in_the_order_that_keeps_the_evidence():
    """
    The label move recognises an unquotable name *by* its backticks. Unquoting
    first would strip the ones it can and leave the rest in a form it no longer
    matches, so a mixed statement has to survive both.
    """
    prepared = prepare_statement(
        ticked("MATCH (n0:~Document~)-[r0:~IS FRIEND OF~]->(n1) RETURN * LIMIT 5")
    )
    assert prepared.startswith(
        'MATCH (n0:Document)-[r0]->(n1) WHERE type(r0) IN ["IS FRIEND OF"] RETURN '
    )
    assert TICK not in prepared


def test_a_statement_with_no_quoting_is_untouched_by_the_quoting_passes():
    query = "MATCH (n:Person)-[r:KNOWS]->(m) RETURN id(n)"
    assert unquote_identifiers(query) == query
    assert move_unquotable_labels(query) == query


# -- when it could not help -------------------------------------------------


def test_the_invalid_token_error_arrives_with_the_reason_attached():
    message = explain(ticked("MATCH (n:~Odd Label~ {x: 1}) RETURN id(n)"), "Invalid token")
    assert message.startswith("Invalid token")
    assert "no quoted-identifier syntax" in message
    assert "IN labels(n0)" in message


def test_an_invalid_token_with_no_backtick_in_it_is_passed_through_untouched():
    assert explain("MATCH (n) RETURN id(n) @@", "Invalid token") == "Invalid token"
