"""
Query policy: what is refused, what limit lands, what shape the answer has.

The refusals are the substance here. The reference bridge answered an unplannable
statement with a two-cell table that said ``unsupported query: ...``, which on a graph
canvas is indistinguishable from a query that matched nothing; here every one of them
raises, and the message names the construct that caused it. The tests below are
therefore mostly about the *message*, one per construct, and about the two shapes that
must not be confused: a warehouse column called ``update`` and a literal that says
``DELETE`` are not writes.
"""

from __future__ import annotations

import pytest

from graphxr_database_proxy.drivers.relational import query, translate
from graphxr_database_proxy.drivers.relational.ir import MatchPlan
from graphxr_database_proxy.drivers.relational.predicate import Prop
from graphxr_database_proxy.drivers.relational.query import DEFAULT_LIMIT, MAX_QUERY_LENGTH

SIMPLE = "MATCH (n:`Person`) RETURN *"


# ----- validate: shape and writes -----


def test_validate_passes_a_read():
    assert query.validate(SIMPLE) == (True, "ok")
    assert query.validate("MATCH (n)-[r:`WORKS_FOR`]->(m) RETURN n, r, m")[0] is True


def test_an_empty_query_is_refused_by_name():
    for text in ("", "   ", "\n\t "):
        ok, why = query.validate(text)
        assert not ok and "empty" in why, text


def test_an_oversized_query_is_refused_before_anything_is_parsed():
    huge = "MATCH (n) WHERE n.a = '" + "z" * (MAX_QUERY_LENGTH + 2) + "' RETURN n"
    ok, why = query.validate(huge)
    assert not ok and "too long" in why and str(MAX_QUERY_LENGTH) in why


@pytest.mark.parametrize(
    "verb,statement",
    [
        ("CREATE", "MATCH (n) CREATE (m)"),
        ("MERGE", "MERGE (n:Person)"),
        ("DELETE", "MATCH (n) DELETE n"),
        ("DETACH", "MATCH (n) DETACH DELETE n"),
        ("SET", "MATCH (n) SET n.name = 'x'"),
        ("REMOVE", "MATCH (n) REMOVE n.name"),
        ("DROP", "MATCH (n) DROP n"),
        ("ALTER", "ALTER TABLE Person ADD x int"),
        ("INSERT", "INSERT INTO Person VALUES (1)"),
        ("UPDATE", "UPDATE Person SET x = 1"),
        ("CALL", "CALL dbms.showRouterRole()"),
    ],
)
def test_every_write_verb_is_refused_with_its_own_reason(verb, statement):
    ok, why = query.validate(statement)
    assert not ok, statement
    assert why.startswith(verb), why
    assert len(why) > len(verb) + 10, "the refusal has to say why, not just what: %r" % why


def test_writes_are_distinct_from_each_other():
    reasons = {query.validate(st)[1] for _verb, st in [
        ("CREATE", "CREATE (n)"), ("DELETE", "DELETE n"), ("SET", "SET n.x = 1"), ("DROP", "DROP n"),
    ]}
    assert len(reasons) == 4, "one message per verb, so a person can tell which one arrived"


@pytest.mark.parametrize(
    "statement",
    [
        "MATCH (n) WHERE n.is_set = 1 AND n.dataset > 2 RETURN n",
        "MATCH (n) WHERE n.note = 'please DELETE and DROP this' RETURN n",
        "MATCH (n) RETURN n.order_id AS ordered LIMIT 5",
        "MATCH (n:`Set Cards`) RETURN n",
        "MATCH (n) WHERE n.`update` = 1 RETURN n",
    ],
)
def test_a_read_that_mentions_a_write_only_inside_a_name_or_a_literal_is_a_read(statement):
    ok, why = query.validate(statement)
    assert ok, "%r -> %r" % (statement, why)


def test_a_column_called_update_is_a_read_and_plans():
    prepared = query.prepare("MATCH (n) WHERE n.update > 1 RETURN n ORDER BY n.update DESC LIMIT 5")
    assert prepared.plan.where.lhs == Prop("n", "update")
    assert prepared.plan.order == [("n.update", "DESC")]


@pytest.mark.parametrize("statement", ["SHOW TABLES", "desc Person", "n.name = 'x'", "ORDER BY n.x", "LIMIT 10"])
def test_a_statement_that_is_not_a_read_query_at_all_is_refused_by_its_shape(statement):
    ok, why = query.validate(statement)
    assert not ok and "MATCH" in why, (statement, why)


def test_a_return_with_nothing_in_it_clears_the_guard_and_is_refused_as_a_pattern():
    assert query.validate("   RETURN   ")[0] is True
    with pytest.raises(query.RefusedQuery) as caught:
        query.prepare("   RETURN   ")
    assert "MATCH" in str(caught.value)


def test_with_is_a_shape_the_guard_lets_through_for_a_precise_refusal_later():
    assert query.validate("MATCH (n) WITH n RETURN n")[0] is True
    with pytest.raises(query.RefusedQuery) as caught:
        query.prepare("MATCH (n) WITH n RETURN n")
    assert "WITH" in str(caught.value)


# ----- prepare: limits -----


def test_an_absent_limit_becomes_the_default():
    prepared = query.prepare(SIMPLE)
    assert prepared.limit == DEFAULT_LIMIT
    assert prepared.auto_limited is True and prepared.capped is False
    assert prepared.plan.limit == DEFAULT_LIMIT, "the plan the compiler reads carries it too"
    assert prepared.skip == 0


def test_an_explicit_limit_is_kept():
    prepared = query.prepare("MATCH (n) RETURN n SKIP 5 LIMIT 20")
    assert (prepared.limit, prepared.skip, prepared.auto_limited) == (20, 5, False)


def test_max_limit_caps_an_absent_limit_and_an_explicit_one_alike():
    auto = query.prepare(SIMPLE, default_limit=1000, max_limit=200)
    assert (auto.limit, auto.auto_limited, auto.capped) == (200, True, True)
    typed = query.prepare("MATCH (n) RETURN n LIMIT 9000", default_limit=1000, max_limit=200)
    assert (typed.limit, typed.auto_limited, typed.capped) == (200, False, True)
    assert typed.plan.limit == 200


def test_an_explicit_limit_under_the_ceiling_is_left_alone():
    prepared = query.prepare("MATCH (n) RETURN n LIMIT 7", max_limit=2000)
    assert (prepared.limit, prepared.capped) == (7, False)


def test_a_default_limit_of_zero_means_the_caller_wants_no_limit():
    prepared = query.prepare(SIMPLE, default_limit=0)
    assert prepared.limit is None and prepared.auto_limited is False


def test_prepare_normalises_the_statement_it_reports():
    prepared = query.prepare("  " + SIMPLE + " ;  ")
    assert prepared.statement == SIMPLE


def test_every_generated_intent_statement_prepares_as_a_graph():
    from graphxr_database_proxy.drivers.dialect import NodeRef, StatementDialect, build_expand, build_pull_relationship

    relational = StatementDialect(
        name="relational",
        predicate="primary-key",
        rel_type_expr=lambda var: "type(%s)" % var,
        relationship_filter="pattern",
        return_vars=("n", "r", "m"),
        return_separator=", ",
    )
    seeds = [NodeRef(category="Person", internal_id="Person:1", key_prop="person_id", key_value="1")]
    statements = build_expand(relational, seeds, direction="from", relationships=["WORKS_FOR"], category="Person")
    statements += build_pull_relationship(relational, "WORKS_FOR")
    assert statements
    for statement in statements:
        prepared = query.prepare(statement)
        assert prepared.kind == "graph", statement
        assert prepared.limit is not None, statement


# ----- classify: the shape of the answer -----


def test_classify_sees_a_graph_a_table_and_an_aggregate():
    assert query.classify(translate.translate("MATCH (n)-[r]->(m) RETURN n, r, m")) == "graph"
    assert query.classify(translate.translate("MATCH (n) RETURN *")) == "graph"
    assert query.classify(translate.translate("MATCH (n)-[r]->(m) RETURN n.name, m.title")) == "scalar"
    assert query.classify(translate.translate("MATCH (n)-[r]->(m) RETURN n.city, count(m) AS total")) == "aggregate"


def test_prepare_records_the_same_shape_classify_reports():
    for statement in ["MATCH (n) RETURN n", "MATCH (n) RETURN n.name", "MATCH (n) RETURN count(n)"]:
        prepared = query.prepare(statement)
        assert prepared.kind == query.classify(prepared.plan), statement


def test_a_grouped_aggregate_keeps_its_keys_and_its_functions():
    spec = query.prepare("MATCH (n)-[r]->(m) RETURN n.`full name`, count(m) AS total, sum(r.amount)").spec
    assert spec.kind == "aggregate"
    assert [(c.var, c.prop) for c in spec.columns] == [("n", "full name")]
    assert [a.func for a in spec.aggregates] == ["count", "sum"]
    assert spec.aggregates[0].alias == "total"


# ----- refusal list, per engine -----


def test_regex_is_served_when_the_engine_has_an_operator_for_it():
    prepared = query.prepare("MATCH (n) WHERE n.name =~ 'A.*' RETURN n")
    assert prepared.kind == "graph"


def test_the_same_query_is_refused_by_name_on_an_engine_without_one():
    with pytest.raises(query.RefusedQuery) as caught:
        query.prepare(
            "MATCH (n) WHERE n.name =~ 'A.*' RETURN n", refusals=query.Refusals.of(query.REGEX_REFUSAL)
        )
    message = str(caught.value)
    assert "=~" in message and "regular-expression" in message, message


def test_a_refusal_list_is_additive_and_leaves_the_one_it_came_from_alone():
    base = query.Refusals.of(query.REGEX_REFUSAL)
    wider = base.add(
        query.Refusal(
            name="case-insensitive matching",
            reason="this collation cannot compare without a lower() on both sides",
            detect=lambda plan: False,
        )
    )
    assert len(base.rules) == 1 and len(wider.rules) == 2
    assert query.Refusals.of().rules == ()
    assert base.check(query.prepare("MATCH (n) RETURN n").plan) is None


def test_an_engine_rule_only_fires_on_the_plan_it_describes():
    rules = query.Refusals.of(
        query.Refusal(
            name="a pattern with more than two hops",
            reason="the fan-out is exponential in the branch count",
            detect=lambda plan: len(plan.edges) > 2,
        )
    )
    assert query.prepare("MATCH (a)-[r]->(b)-[r2]->(c) RETURN a, r, b, r2, c", refusals=rules).limit == 1000
    with pytest.raises(query.RefusedQuery) as caught:
        query.prepare(
            "MATCH (a)-[r]->(b)-[r2]->(c)-[r3]->(d) RETURN a, b, c, d", refusals=rules
        )
    assert "more than two hops" in str(caught.value)


@pytest.mark.parametrize(
    "statement,names",
    [
        ("MATCH (n)-[r:KNOWS*1..3]->(m) RETURN n", "variable-length path"),
        ("OPTIONAL MATCH (n) RETURN n", "OPTIONAL MATCH"),
        ("MATCH (n) WITH n RETURN n", "WITH"),
        ("MATCH (n) UNWIND n.x AS y RETURN n", "UNWIND"),
        ("MATCH (n) RETURN n HAVING count(n) > 1", "HAVING"),
        ("MATCH (n) CALL { MATCH (m) RETURN m } RETURN n", "CALL"),
        ("MATCH (n) RETURN count(DISTINCT n.name)", "DISTINCT inside an aggregate"),
        ("MATCH (n) RETURN n, count(m)", "beside an aggregate"),
        ("MATCH (n) WHERE ID(n) IN [internal_id(1,2)] RETURN n", "ID(n)"),
        ("MATCH (n) WHERE id(n) = 'Person:1' RETURN n", "node-identity"),
        ("MATCH (n {x: 1}) RETURN n", "property constraint"),
        ("MATCH (n) RETURN n.foo.bar", "RETURN projection"),
        ("MATCH (n) RETURN n, m", "no pattern in this statement binds"),
    ],
)
def test_a_refused_construct_raises_naming_it_rather_than_answering_empty(statement, names):
    with pytest.raises(query.RefusedQuery) as caught:
        query.prepare(statement)
    assert names.lower() in str(caught.value).lower(), (statement, str(caught.value))


def test_an_identity_refusal_explains_the_substitute():
    with pytest.raises(query.RefusedQuery) as caught:
        query.prepare("MATCH (n) WHERE ID(n) IN [internal_id(1,2)] RETURN n")
    message = str(caught.value)
    assert "primary key" in message, message


def test_refusals_are_translatable_catchable_as_one_type():
    with pytest.raises(translate.TranslateError):
        query.prepare("MATCH (n) RETURN count(DISTINCT n.x)")
    assert issubclass(query.RefusedQuery, translate.TranslateError)


def test_a_plan_is_read_the_same_way_with_and_without_a_prepared_statement():
    plan: MatchPlan = translate.translate("MATCH (n) RETURN n")
    assert plan.vertices and query.classify(plan) == "graph"
    assert plan.limit is None, "the translator records what the statement said"
    prepared = query.prepare("MATCH (n) RETURN n")
    assert (prepared.plan.vertices, prepared.plan.return_items) == (plan.vertices, plan.return_items)
    assert prepared.plan.limit == DEFAULT_LIMIT, "prepare settles the limit on the plan the compiler reads"
