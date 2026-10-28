"""
``RETURN``: the three kinds and the shape each item has to come back in.

Lives with ``drivers/relational/translate_return.py``. These assert the spec a
statement plans against — graph, scalar or aggregate — because the answer's shape
is decided here and nowhere else.
"""

from kineviz_database_proxy.drivers.relational.translate import (
    PropItem,
    parse_return,
    translate as plan_of,
)


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
