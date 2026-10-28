"""
The ``MATCH`` pattern: groups, arrows and what a group carries.

Lives with ``drivers/relational/translate_pattern.py``. Every statement here goes
through the front-end, so the direction and label facts are the ones the intent
builders depend on, not the private helpers' own signatures.
"""

import pytest

from kineviz_database_proxy.drivers.relational.translate import translate as plan_of


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
