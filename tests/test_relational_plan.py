"""
Branch enumeration: every reading of one pattern, or a refusal that says which.

The cases here are the ones the reference bridge carried, because they are the ones a
real schema produces: an unlabelled node that the edges pin down, a link type that joins
several kinds of record, an undirected arrow that means two joins rather than one, and a
pattern that could mean so many things it must not be run.
"""

from __future__ import annotations

import pytest

from graphxr_database_proxy.drivers.relational import query, translate
from graphxr_database_proxy.drivers.relational.plan import (
    DEFAULT_MAX_BRANCHES,
    BranchLimitExceeded,
    PatternNotInMapping,
    concrete_plans,
    describe,
)
from graphxr_database_proxy.drivers.relational.schemas import Endpoint, NodeSchema, Pair, RelSchema


def node(label, table, key="id", key_type="bigint", **props):
    columns = {key: key_type}
    columns.update(props)
    return NodeSchema(label=label, table=table, key=key, key_type=key_type, props=columns)


def rel(type_name, table, pairs, key="id", key_type="bigint", **props):
    return RelSchema(
        type=type_name,
        table=table,
        key=key,
        key_type=key_type,
        endpoints=list(pairs),
        props={key: key_type, **props},
    )


def pair(src_label, src_column, dst_label, dst_column, estimate=1):
    return Pair(
        src=Endpoint(src_label, src_column, "bigint"),
        dst=Endpoint(dst_label, dst_column, "bigint"),
        row_estimate=estimate,
    )


NODES = {
    "Person": node("Person", "Person", key="person_id"),
    "Organization": node("Organization", "Organization", key="organization_id"),
    "City": node("City", "City", key="city_id"),
}
EDGES = {
    "EMPLOYMENT": rel(
        "EMPLOYMENT",
        "Employment",
        [pair("Person", "person_id", "Organization", "organization_id", 120)],
        key="employment_id",
    ),
    "LIVES_IN": rel("LIVES_IN", "Person", [pair("Person", "city_id", "City", "city_id")], key=None, key_type=None),
}


def plan(statement):
    return translate.translate(statement)


def branches(statement, nodes=NODES, edges=EDGES, **kwargs):
    return concrete_plans(plan(statement), nodes, edges, **kwargs)


# -- what the pattern leaves open --


def test_a_named_label_binds_the_variable_and_leaves_one_branch():
    found = branches("MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN p, r, o")
    assert len(found) == 1
    assert found[0].labels == {"p": "Person", "o": "Organization"}
    assert found[0].edges[0].rel_type == "EMPLOYMENT"
    assert found[0].edges[0].swapped is False


def test_an_unlabelled_node_takes_the_label_every_candidate_forces():
    """``(a)-[:EMPLOYMENT]->(b)`` has exactly one reading: Person to Organization."""
    found = branches("MATCH (a)-[:EMPLOYMENT]->(b) RETURN a, b")
    assert len(found) == 1
    assert found[0].labels == {"a": "Person", "b": "Organization"}


def test_an_untyped_connector_fans_out_over_every_type_that_can_answer_it():
    found = branches("MATCH (p:Person)-[r]->(x) RETURN p, r, x")
    assert sorted(branch.edges[0].rel_type for branch in found) == ["EMPLOYMENT", "LIVES_IN"]
    labels = {branch.edges[0].rel_type: branch.labels["x"] for branch in found}
    assert labels == {"EMPLOYMENT": "Organization", "LIVES_IN": "City"}


def test_an_alternation_names_its_types_and_nothing_else():
    found = branches("MATCH (p:Person)-[r:EMPLOYMENT|LIVES_IN]->(x) RETURN p, r, x")
    assert len(found) == 2
    assert {branch.edges[0].rel_type for branch in found} == {"EMPLOYMENT", "LIVES_IN"}


def test_an_unknown_direction_is_two_branches_because_a_key_predicate_pins_one_end():
    """
    ``-[r]-`` fans out per orientation rather than becoming one ``OR``ed join.

    One statement with ``a.key = e.src OR a.key = e.dst`` cannot use the index on either
    side, and a relational engine will scan the link table for it. Two branches, each
    pinned by one column, is the shape the planner can actually run.
    """
    found = branches("MATCH (a)-[r:EMPLOYMENT]-(b) RETURN a, r, b")
    assert len(found) == 2
    assert sorted(branch.edges[0].swapped for branch in found) == [False, True]
    swapped = [branch for branch in found if branch.edges[0].swapped][0]
    assert swapped.labels == {"a": "Organization", "b": "Person"}


def test_a_labelled_seed_pins_one_orientation_so_an_undirected_arrow_yields_one_branch():
    """
    Why the capability record lists ``all`` and not ``both``.

    ``(p:Person)-[r:EMPLOYMENT]-(o)`` can only run one way: ``p`` is the Person end, so
    it is the source end, and the other orientation contradicts the label the pattern
    already stated. The directions fan out in the *planner*, one statement per
    orientation, which is exactly what ``directions: ["all", "from", "to"]`` promises the
    client without the client having to ask twice.
    """
    found = branches("MATCH (p:Person)-[r:EMPLOYMENT]-(o) RETURN p, r, o")
    assert len(found) == 1
    assert found[0].edges[0].swapped is False
    assert found[0].labels["o"] == "Organization"


def test_a_reverse_arrow_is_the_orientation_against_the_mapping():
    found = branches("MATCH (o:Organization)<-[r:EMPLOYMENT]-(p:Person) RETURN o, r, p")
    assert len(found) == 1
    assert found[0].edges[0].swapped is True
    assert found[0].labels == {"o": "Organization", "p": "Person"}


def test_a_polymorphic_type_becomes_one_branch_per_endpoint_pair():
    nodes = dict(NODES, Contact=node("Contact", "Contact", key="contact_id"))
    edges = dict(
        EDGES,
        KNOWS=rel(
            "KNOWS",
            "Knows",
            [pair("Person", "person_id", "Person", "friend_id"), pair("Person", "person_id", "Contact", "contact_id")],
            key="knows_id",
        ),
    )
    found = branches("MATCH (p:Person)-[r:KNOWS]->(x) RETURN p, r, x", nodes, edges)
    assert len(found) == 2
    assert sorted(branch.labels["x"] for branch in found) == ["Contact", "Person"]


# -- what a pattern must not be allowed to mean --


def test_one_variable_cannot_be_two_labels():
    """The shared variable in a two-hop pattern is what stops the whole thing multiplying."""
    nodes = dict(NODES, City=node("City", "City", key="city_id"))
    edges = dict(
        EDGES,
        IN_CITY=rel("IN_CITY", "Organization", [pair("Organization", "city_id", "City", "city_id")], key=None, key_type=None),
    )
    found = branches("MATCH (p:Person)-[:EMPLOYMENT]->(o)-[:IN_CITY]->(c:City) RETURN p, o, c", nodes, edges)
    assert len(found) == 1
    assert found[0].labels == {"p": "Person", "o": "Organization", "c": "City"}


def test_a_pattern_that_pins_a_shared_variable_to_two_labels_has_no_branch_at_all():
    found = branches("MATCH (p:Person)-[:EMPLOYMENT]->(o:City) RETURN p, o")
    assert found == []


def test_a_relationship_variable_keeps_its_type_across_two_uses():
    """``[r:A]->()<-[:B]`` written twice as ``r`` cannot be two types at once."""
    edges = dict(EDGES, LIVES_IN=EDGES["LIVES_IN"])
    found = branches(
        "MATCH (p:Person)-[r:EMPLOYMENT]->(o)<-[r:LIVES_IN]-(c) RETURN p, r, o",
        NODES,
        edges,
    )
    assert found == []


def test_a_label_the_mapping_does_not_have_is_refused_by_name():
    with pytest.raises(PatternNotInMapping) as refused:
        branches("MATCH (p:Person)-[:EMPLOYMENT]->(o:Division) RETURN p, o")
    assert "Division" in str(refused.value)


def test_a_relationship_type_the_mapping_does_not_have_is_refused_by_name():
    with pytest.raises(PatternNotInMapping) as refused:
        branches("MATCH (p:Person)-[r:REPORTS_TO]->(o:Organization) RETURN p, r, o")
    assert "REPORTS_TO" in str(refused.value)


def test_an_excluded_composite_key_table_refuses_rather_than_reading_as_missing_data():
    """
    The message has to say the label is not declared, not that it has no rows.

    A table with a composite key never reaches the mapping at all, so this is the only
    place a person learns the difference between an empty table and an unmappable one.
    """
    with pytest.raises(PatternNotInMapping) as refused:
        branches("MATCH (n:OrderLine) RETURN n")
    assert "excluded" in str(refused.value)


# -- the cap --


def test_the_default_cap_is_stated_and_small():
    assert DEFAULT_MAX_BRANCHES == 10


def test_a_pattern_wider_than_the_cap_is_refused_rather_than_trimmed():
    """Ten branches is the ceiling; the eleventh does not quietly disappear."""
    nodes = {
        "L%d" % index: node("L%d" % index, "T%d" % index, key="k") for index in range(24)
    }
    edges = {
        "E%d" % index: rel(
            "E%d" % index,
            "X%d" % index,
            [pair("L%d" % index, "a_id", "L%d" % (index + 1), "b_id")],
            key="id",
        )
        for index in range(24)
    }
    found = branches("MATCH (a)-[r]->(b) RETURN a, r, b", nodes, edges, max_branches=64)
    assert len(found) == 24
    with pytest.raises(BranchLimitExceeded) as refused:
        branches("MATCH (a)-[r]->(b) RETURN a, r, b", nodes, edges, max_branches=10)
    message = str(refused.value)
    assert "MATCH (a)-[r]->(b)" in message and "10" in message


def test_an_unbounded_pattern_is_refused_before_the_cross_product_is_walked():
    """The pre-estimate guard: 24 types x several pairs each is a number, not an enumeration."""
    nodes = {"Person": node("Person", "Person", key="person_id"), "City": node("City", "City", key="city_id")}
    edges = {}
    for index in range(30):
        edges["E%d" % index] = rel(
            "E%d" % index,
            "T%d" % index,
            [pair("Person", "a%d" % index, "City", "b%d" % index)],
            key="id",
        )
    with pytest.raises(BranchLimitExceeded):
        branches("MATCH (p:Person)-[r]->(c:City) RETURN p, r, c", nodes, edges)


def test_an_unlabelled_vertex_alone_is_capped_like_any_other_fan_out():
    """``MATCH (n)`` across 24 labels is 24 statements, and the answer is to name a label."""
    nodes = {"L%d" % index: node("L%d" % index, "T%d" % index, key="k") for index in range(24)}
    assert len(branches("MATCH (n) RETURN n", nodes, {}, max_branches=64)) == 24
    with pytest.raises(BranchLimitExceeded) as refused:
        branches("MATCH (n) RETURN n", nodes, {}, max_branches=10)
    assert "(n)" in str(refused.value)


def test_describe_reads_the_pattern_back_out_of_the_plan():
    found = describe(plan("MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN p, r, o"))
    assert found == "MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization)"


def test_describe_keeps_an_untyped_undirected_connector_visible():
    found = describe(plan("MATCH (a)-[r]-(b) RETURN a, r, b"))
    assert found == "MATCH (a)-[r]-(b)"


def test_case_of_a_stated_label_is_not_a_refusal():
    """A hand-typed ``:person`` means ``Person``; the mapping's spelling wins."""
    found = branches("MATCH (p:person)-[:employment]->(o:organization) RETURN p, o")
    assert len(found) == 1
    assert found[0].labels == {"p": "Person", "o": "Organization"}


def test_a_prepared_statement_plans_through_the_same_door():
    """``prepare`` is what the driver hands over, so the two must line up."""
    prepared = query.prepare("MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN p, r, o")
    found = concrete_plans(prepared.plan, NODES, EDGES)
    assert len(found) == 1
    assert found[0].edges[0].pair.src.label == "Person"
