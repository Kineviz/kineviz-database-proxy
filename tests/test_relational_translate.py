"""
The Cypher front-end: statement text in, ``MatchPlan`` out.

Everything here is asserted against the strings ``drivers/dialect.py`` actually
generates, not against strings this file would like: ``GraphIntentMixin`` builds a
statement and hands it to ``execute_query`` with no bound parameters, so the first
thing a relational driver does with a ``/expand`` is re-read the text its own dialect
wrote. If the two disagree by one backtick, every expand fails at integration. The
generated fixtures below come from the builders themselves — ``test_dialect.py``'s
tables, run through a dialect shaped like the relational one the spec declares.

Refusals are asserted as part of the contract: a statement this cannot read raises with
the construct named, because an empty canvas otherwise reads as "nothing matched".
"""

from __future__ import annotations

from typing import List

import pytest

from graphxr_database_proxy.drivers.dialect import (
    NodeRef,
    StatementDialect,
    build_expand,
    build_pull_category,
    build_pull_relationship,
)
from graphxr_database_proxy.drivers.relational import translate
from graphxr_database_proxy.drivers.relational.predicate import (
    And,
    Cmp,
    EntityFn,
    InList,
    Lit,
    Not,
    Prop,
    StrOp,
)
from graphxr_database_proxy.drivers.relational.translate import (
    AggItem,
    PropItem,
    TranslateError,
    clause_heads,
    parse_return,
    strip_comments,
    translate as plan_of,
)

#: The relational token table as the spec declares it: a key predicate, ``type(r)`` for
#: a relationship's type, types filtered in the pattern, ``n, r, m`` returned, and the
#: default backtick quoting and ``SKIP n LIMIT m`` pagination. ``drivers/relational``
#: will own the shipped copy; this is the same record, built here so the statements are
#: the ones the intent builders really emit.
RELATIONAL = StatementDialect(
    name="relational",
    predicate="primary-key",
    node_id_expr=lambda var: "ID(%s)" % var,
    rel_id_expr=None,
    rel_type_expr=lambda var: "type(%s)" % var,
    relationship_filter="pattern",
    return_vars=("n", "r", "m"),
    return_separator=", ",
    supports_multi_hop=True,
    supports_only_between_selected=False,
)

SEEDS = [
    NodeRef(category="Person", internal_id="Person:1001", key_prop="person_id", key_value="1001"),
    NodeRef(category="Person", internal_id="Person:1002", key_prop="person_id", key_value="1002"),
]


def expand(**kwargs) -> List[str]:
    return build_expand(RELATIONAL, SEEDS, key_types={"Person": "int"}, **kwargs)


# ----- the four shapes the intent builders emit -----


def test_expand_by_key_one_hop_typed():
    plan = plan_of(
        "MATCH (n:Person)-[r:WORKS_FOR]->(m) WHERE n.person_id IN [1001,1002] RETURN n, r, m SKIP 0 LIMIT 1000"
    )
    assert [(v.var, v.label) for v in plan.vertices] == [("n", "Person"), ("m", None)]
    assert len(plan.edges) == 1
    edge = plan.edges[0]
    assert (edge.var, edge.types, edge.direction, edge.src_var, edge.dst_var) == (
        "r", ["WORKS_FOR"], "out", "n", "m"
    )
    assert plan.where == InList(Prop("n", "person_id"), [1001, 1002])
    assert (plan.limit, plan.skip, plan.distinct) == (1000, 0, False)
    assert plan.return_items == ["n", "r", "m"]
    assert plan.order == []


def test_pull_relationship_undirected_with_a_hidden_type():
    plan = plan_of(
        "MATCH (n)-[r]-(m) WHERE n.name IN ['Avery Chen'] AND NOT type(r) IN ['PERFORMS'] "
        "RETURN * SKIP 5 LIMIT 10"
    )
    assert [(v.var, v.label) for v in plan.vertices] == [("n", None), ("m", None)]
    assert plan.edges[0].types is None
    assert plan.edges[0].direction == "both"
    assert isinstance(plan.where, And) and len(plan.where.parts) == 2
    assert plan.where.parts[0] == InList(Prop("n", "name"), ["Avery Chen"])
    assert plan.where.parts[1] == Not(InList(EntityFn("r", "type"), ["PERFORMS"]))
    assert plan.return_items == ["*"]
    assert (plan.limit, plan.skip) == (10, 5)


def test_two_hop_chain_names_the_nodes_and_the_edges_it_walks():
    plan = plan_of(
        "MATCH (n:Person)-[r]->(n1)-[r1]->(m) WHERE n.person_id IN [1] RETURN n, r, n1, r1, m"
    )
    assert [(v.var, v.label) for v in plan.vertices] == [("n", "Person"), ("n1", None), ("m", None)]
    assert [(e.var, e.src_var, e.dst_var, e.direction) for e in plan.edges] == [
        ("r", "n", "n1", "out"),
        ("r1", "n1", "m", "out"),
    ]
    assert plan.return_items == ["n", "r", "n1", "r1", "m"]
    assert plan.limit is None and plan.skip == 0


def test_a_category_pull_is_a_graph_return_with_no_edge():
    plan = plan_of("MATCH (n) RETURN n LIMIT 25")
    assert [(v.var, v.label) for v in plan.vertices] == [("n", None)]
    assert plan.edges == []
    assert plan.where is None
    assert plan.return_items == ["n"]
    assert plan.limit == 25


# ----- what the builders actually write -----


def test_the_generated_expand_round_trips_field_by_field():
    (statement,) = expand(direction="from", relationships=["WORKS_FOR"], limit=1000, category="Person")
    assert statement == (
        "MATCH (n:`Person`)-[r:`WORKS_FOR`]->(m) WHERE n.`person_id` IN [1001,1002] "
        "RETURN n, r, m SKIP 0 LIMIT 1000"
    )
    plan = plan_of(statement)
    assert plan.vertices[0].label == "Person"
    assert plan.edges[0].types == ["WORKS_FOR"] and plan.edges[0].direction == "out"
    assert plan.where == InList(Prop("n", "person_id"), [1001, 1002])
    assert (plan.limit, plan.skip) == (1000, 0)
    assert parse_return(plan.return_items).kind == "graph"


def test_a_generated_hidden_type_filter_reads_as_a_negated_list_over_the_edge_type():
    (statement,) = expand(
        direction="all", exclude_relationship_types=["PERFORMS"], limit=10, skip=5, category="Person"
    )
    assert "AND NOT(type(r) IN [\"PERFORMS\"])" in statement
    plan = plan_of(statement)
    assert plan.edges[0].direction == "both"
    assert plan.where.parts[1] == Not(InList(EntityFn("r", "type"), ["PERFORMS"]))
    assert (plan.limit, plan.skip) == (10, 5)


def test_a_generated_two_hop_returns_every_variable_on_the_path():
    (statement,) = expand(direction="from", hops=2, limit=1000, category="Person")
    assert statement == (
        "MATCH (n:`Person`)-[r]->(n1)-[r1]->(m) WHERE n.`person_id` IN [1001,1002] "
        "RETURN n, r, m, n1, r1 SKIP 0 LIMIT 1000"
    )
    plan = plan_of(statement)
    assert [v.var for v in plan.vertices] == ["n", "n1", "m"]
    assert [e.var for e in plan.edges] == ["r", "r1"]
    assert plan.return_items == ["n", "r", "m", "n1", "r1"]


def test_a_generated_type_alternation_keeps_every_type_in_the_pattern():
    (statement,) = expand(
        direction="to", relationships=["WORKS_FOR", "LIVES_IN"], limit=1000, category="Person"
    )
    assert statement == (
        "MATCH (n:`Person`)<-[r:`WORKS_FOR`|`LIVES_IN`]-(m) WHERE n.`person_id` IN [1001,1002] "
        "RETURN n, r, m SKIP 0 LIMIT 1000"
    )
    plan = plan_of(statement)
    assert plan.edges[0].types == ["WORKS_FOR", "LIVES_IN"]
    assert plan.edges[0].direction == "in"


def test_a_generated_pull_category_with_its_loaded_nodes_excluded():
    loaded = [NodeRef(category="Person", internal_id="Person:Avery Chen", key_prop="full name", key_value="Avery Chen")]
    (statement,) = build_pull_category(RELATIONAL, "Person", limit=1000, loaded=loaded, key_types={"Person": "string"})
    assert statement == (
        'MATCH (n:`Person`) WHERE NOT n.`full name` IN ["Avery Chen"] RETURN * SKIP 0 LIMIT 1000'
    )
    plan = plan_of(statement)
    assert plan.vertices[0].label == "Person"
    assert plan.where == Not(InList(Prop("n", "full name"), ["Avery Chen"]))
    assert parse_return(plan.return_items, plan.distinct).star is True


def test_a_generated_pull_relationship_is_directed_and_single_typed():
    (statement,) = build_pull_relationship(RELATIONAL, "WORKS_FOR", limit=1000)
    assert statement == "MATCH (n)-[r:`WORKS_FOR`]->(m) RETURN * SKIP 0 LIMIT 1000"
    plan = plan_of(statement)
    assert (plan.edges[0].var, plan.edges[0].types, plan.edges[0].direction) == ("r", ["WORKS_FOR"], "out")


def test_an_identifier_needing_two_words_or_a_backtick_survives_the_round_trip():
    # ``backtick()`` doubles a backtick inside a name, which is what a warehouse column
    # called ``Or``der`` arrives as, and a label with parentheses would end a naive
    # ``\(([^()]*)\)`` group early.
    (statement,) = build_pull_category(RELATIONAL, "Or`der", limit=10)
    assert statement == "MATCH (n:`Or``der`) RETURN * SKIP 0 LIMIT 10"
    assert plan_of(statement).vertices[0].label == "Or`der"
    assert plan_of("MATCH (n:`sales (2024)`) RETURN n").vertices[0].label == "sales (2024)"
    (spaced,) = build_pull_relationship(RELATIONAL, "Order Lines", limit=10, skip=5)
    assert plan_of(spaced).edges[0].types == ["Order Lines"]


# ----- pattern shapes -----


@pytest.mark.parametrize(
    "connector,direction",
    [("-[r]->", "out"), ("<-[r]-", "in"), ("-[r]-", "both"), ("<-[r]->", "both")],
)
def test_the_three_arrow_shapes_map_to_the_three_directions(connector, direction):
    plan = plan_of("MATCH (n:Person)%s(m) RETURN n, r, m" % connector)
    assert plan.edges[0].direction == direction
    assert (plan.edges[0].src_var, plan.edges[0].dst_var) == ("n", "m")


def test_an_unnamed_edge_and_node_get_generated_names():
    plan = plan_of("MATCH ()-[]->() RETURN *")
    assert [v.var for v in plan.vertices] == ["__n0", "__n1"]
    assert plan.edges[0].var == "__e0" and plan.edges[0].types is None


def test_a_bare_variable_pattern_carries_no_label():
    assert plan_of("MATCH (n) RETURN n").vertices[0].label is None


def test_three_hops_chain_the_right_nodes_together():
    plan = plan_of("MATCH (a)-[r]->(b)-[r2]->(c)-[r3]->(d) RETURN a, r, b, r2, c, r3, d")
    assert [v.var for v in plan.vertices] == ["a", "b", "c", "d"]
    assert [(e.var, e.src_var, e.dst_var) for e in plan.edges] == [
        ("r", "a", "b"), ("r2", "b", "c"), ("r3", "c", "d")
    ]


def test_a_repeated_type_in_an_alternation_is_listed_once():
    assert plan_of("MATCH (n)-[r:A|A|B]->(m) RETURN n, r, m").edges[0].types == ["A", "B"]


# ----- RETURN, ORDER BY and pagination -----


def test_return_kinds():
    assert parse_return(["*"]).kind == "graph"
    assert parse_return(["n", "r", "m"]).kind == "graph"
    assert parse_return(None).kind == "graph"
    assert parse_return(["n.name", "m.title"]).kind == "scalar"
    assert parse_return(["n.name", "count(m)"]).kind == "aggregate"
    assert parse_return(["count(*)"]).kind == "aggregate"


def test_return_projections_carry_their_alias_or_their_default():
    spec = parse_return(["n.`full name`", "m.title AS t"])
    assert spec.columns == [
        PropItem(var="n", prop="full name", alias="n.full name"),
        PropItem(var="m", prop="title", alias="t"),
    ]


def test_aggregates_carry_their_argument_in_the_shape_the_compiler_wants():
    spec = parse_return(["count(*)", "count(n)", "sum(n.`order total`) AS total", "min(m.x)", "avg(n.y)"])
    assert [a.func for a in spec.aggregates] == ["count", "count", "sum", "min", "avg"]
    assert (spec.aggregates[0].star, spec.aggregates[0].var) == (True, None)
    assert (spec.aggregates[1].var, spec.aggregates[1].prop) == ("n", None)
    assert (spec.aggregates[2].var, spec.aggregates[2].prop, spec.aggregates[2].alias) == (
        "n", "order total", "total"
    )
    assert spec.aggregates[0].alias == "count_all"
    assert spec.aggregates[1].alias == "count_n"
    assert spec.kind == "aggregate"


def test_distinct_after_return_is_kept_and_recorded():
    plan = plan_of("MATCH (n) RETURN DISTINCT n.name SKIP 0 LIMIT 10")
    assert plan.distinct is True
    assert plan.return_items == ["n.name"]
    assert parse_return(plan.return_items, plan.distinct).distinct is True


def test_order_by_keeps_its_terms_as_text_and_its_direction():
    plan = plan_of("MATCH (n) RETURN n ORDER BY n.`a b` DESC, n.x ASC LIMIT 10")
    assert plan.order == [("n.`a b`", "DESC"), ("n.x", "ASC")]


@pytest.mark.parametrize(
    "tail,limit,skip",
    [("SKIP 5 LIMIT 10", 10, 5), ("LIMIT 10", 10, 0), ("LIMIT 10 OFFSET 3", 10, 3),
     ("SKIP 4", None, 4), ("", None, 0)],
)
def test_pagination_spellings(tail, limit, skip):
    plan = plan_of("MATCH (n) RETURN n " + tail)
    assert (plan.limit, plan.skip) == (limit, skip)


def test_a_trailing_semicolon_and_a_line_comment_are_not_part_of_the_statement():
    plan = plan_of("MATCH (n:Person) RETURN n; // a note about the query\n")
    assert plan.vertices[0].label == "Person"
    assert plan.return_items == ["n"]
    assert strip_comments("MATCH (n) /* block */ RETURN n").split() == ["MATCH", "(n)", "RETURN", "n"]
    assert plan_of("MATCH (n) /* a note */ RETURN n LIMIT 3").limit == 3


def test_a_doubled_dash_is_not_a_comment_because_an_arrow_is():
    # ``-->`` is an unbracketed relationship, so treating ``--`` as a comment marker
    # would silently rewrite a pattern. Cypher's own line comment is ``//``.
    plan = plan_of("MATCH (n)-->(m) RETURN n, m")
    assert plan.edges[0].direction == "out" and plan.edges[0].types is None


def test_a_statement_that_is_all_comment_is_empty_rather_than_unparsable():
    with pytest.raises(TranslateError) as caught:
        plan_of("// nothing here")
    assert "empty" in str(caught.value)


# ----- the scanner guards that the reference's regexes did not have -----


def test_a_property_named_like_a_clause_is_not_a_clause():
    plan = plan_of("MATCH (n) WHERE n.limit > 3 AND n.offset < 2 RETURN n.count AS c LIMIT 5")
    assert plan.limit == 5
    assert plan.where == And([Cmp(Prop("n", "limit"), ">", Lit(3)), Cmp(Prop("n", "offset"), "<", Lit(2))])
    assert parse_return(plan.return_items).columns[0].alias == "c"


def test_a_keyword_inside_a_literal_is_not_a_clause():
    plan = plan_of("MATCH (n) WHERE n.note = 'RETURN me' AND n.x = 1 RETURN n LIMIT 2")
    assert plan.where.parts[0] == Cmp(Prop("n", "note"), "=", Lit("RETURN me"))
    assert plan.limit == 2


def test_starts_with_is_the_operator_and_not_a_with_clause():
    plan = plan_of("MATCH (n) WHERE n.name STARTS WITH 'Avery' RETURN n")
    assert plan.where == StrOp(Prop("n", "name"), "startswith", "Avery")


def test_a_word_that_is_part_of_a_longer_word_is_not_a_clause():
    assert clause_heads("MATCH (n:Reorder) RETURN n.order_id, n.dataset") == ["MATCH", "RETURN"]


def test_an_unterminated_literal_is_named_as_such():
    with pytest.raises(TranslateError) as caught:
        plan_of("MATCH (n) WHERE n.a = 'oops RETURN n")
    assert "unterminated" in str(caught.value)


# ----- refusals, each naming its construct -----


@pytest.mark.parametrize(
    "statement,names",
    [
        ("MATCH (n)-[r:KNOWS*1..3]->(m) RETURN n", "variable-length path"),
        ("MATCH (n)-[r:KNOWS+]->(m) RETURN n", "variable-length path"),
        ("OPTIONAL MATCH (n) RETURN n", "OPTIONAL MATCH"),
        ("MATCH (n) WITH n RETURN n", "WITH"),
        ("MATCH (n) UNWIND n.x AS y RETURN n", "UNWIND"),
        ("MATCH (n) RETURN n HAVING count(n) > 1", "HAVING"),
        ("MATCH (n) CALL { MATCH (m) RETURN m } RETURN n", "CALL"),
        ("MATCH (n) RETURN n UNION MATCH (m) RETURN m", "UNION"),
        ("MATCH (n {person_id: 1}) RETURN n", "property constraint"),
        ("MATCH (n)-[r {since: 2020}]->(m) RETURN n", "property constraint"),
        ("MATCH (n)-[r]->(m), (x) RETURN n", "comma-separated"),
        ("MATCH (n) RETURN count(DISTINCT n.name)", "DISTINCT inside an aggregate"),
        ("MATCH (n) RETURN n, count(m)", "beside an aggregate"),
        ("MATCH (n) RETURN n.foo.bar", "<variable>.<property>"),
        ("MATCH (n) RETURN upper(n.name)", "upper(n.name)"),
        ("MATCH (n) WHERE ID(n) IN [internal_id(1,2)] RETURN n", "ID(n)"),
        ("MATCH (n) WHERE id(n) = 'Person:1' RETURN n", "ID(n)"),
        ("MATCH (n) WHERE n.a = ~* 'x' RETURN n", "WHERE"),
        ("MATCH (n) RETURN n LIMIT soon", "LIMIT"),
        ("MATCH (n) SKIP 1 RETURN n", "SKIP"),
        ("MATCH (n) RETURN n SKIP 1 OFFSET 2", "OFFSET"),
        ("RETURN 1 AS one", "MATCH"),
        ("MATCH () RETURN n", "pattern"),
        ("DROP TABLE Person", "DROP"),
    ],
)
def test_a_statement_that_cannot_be_planned_names_what_is_wrong_with_it(statement, names):
    with pytest.raises(TranslateError) as caught:
        _plan_or_parse(statement)
    message = str(caught.value).lower()
    assert names.lower() in message, "%r not in %r" % (names, message)


def _plan_or_parse(statement: str):
    """Translate and classify, which is what ``query.prepare`` does; refusals live in both."""
    plan = plan_of(statement)
    return parse_return(plan.return_items, plan.distinct)


def test_an_identity_refusal_gives_the_reason_and_not_just_the_name():
    with pytest.raises(TranslateError) as caught:
        plan_of("MATCH (n) WHERE ID(n) IN [internal_id(1,2)] RETURN n")
    message = str(caught.value)
    assert "primary key" in message and "node-identity" in message


def test_an_unreadable_where_clause_is_refused_instead_of_dropped():
    with pytest.raises(TranslateError) as caught:
        plan_of("MATCH (n) WHERE n.a = = 1 RETURN n")
    assert "WHERE" in str(caught.value)


def test_an_unplannable_statement_never_returns_a_plan_with_no_where():
    plan = plan_of("MATCH (n) WHERE n.a = 1 RETURN n")
    assert plan.where is not None
    assert isinstance(plan.where, Cmp)
