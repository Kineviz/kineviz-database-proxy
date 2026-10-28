# -*- coding: utf-8 -*-
"""
The inferred schema for LatticeDB.

There is no catalog behind these answers, because there is no schema in the store
to have one: a LatticeDB node is created without declaring a label or a property.
So the statements are recorded ones -- each was run against latticedb 0.14.0 --
and the rows are what it answered.
"""

from __future__ import annotations

from kineviz_database_proxy.drivers.embedded.lattice_schema import (
    build_category,
    category_names,
    distinct_labels_statement,
    distinct_relationships_statement,
    load_lattice_schema,
    node_sample_statement,
    property_shape,
    relationship_sample_statement,
    relationship_triples,
)


def reader(canned):
    """A row reader over recorded answers, remembering what it was asked."""
    asked = []

    async def read(statement):
        asked.append(statement)
        return canned.get(statement)

    return read, asked


# -- statements --------------------------------------------------------------


def test_the_discovery_statements_are_paged_at_the_source():
    assert distinct_labels_statement(50).endswith("SKIP 0 LIMIT 50")
    assert distinct_relationships_statement(50).endswith("SKIP 0 LIMIT 50")
    assert node_sample_statement("Person", 10).endswith("SKIP 0 LIMIT 10")
    assert relationship_sample_statement("KNOWS", 10).endswith("SKIP 0 LIMIT 10")


def test_a_plain_label_is_sampled_through_the_pattern():
    assert node_sample_statement("Person").startswith("MATCH (n:Person) RETURN properties(n)")
    assert relationship_sample_statement("KNOWS").startswith("MATCH (n)-[r:KNOWS]->(m)")


def test_a_label_that_cannot_be_written_in_a_pattern_is_sampled_by_predicate():
    """No quoted-identifier syntax exists here, so the label becomes a string."""
    assert node_sample_statement("Odd Label").startswith(
        'MATCH (n) WHERE "Odd Label" IN labels(n)'
    )
    assert relationship_sample_statement("IS FRIEND OF").startswith(
        'MATCH (n)-[r]->(m) WHERE type(r) IN ["IS FRIEND OF"]'
    )


# -- reading the answers -----------------------------------------------------


def test_each_label_of_a_multi_label_node_is_its_own_category():
    rows = [{"labels(n)": ["Person", "Employee"]}, {"labels(n)": ["Person"]}]
    assert category_names(rows) == ["Person", "Employee"]


def test_an_unlabelled_node_contributes_no_category():
    assert category_names([{"labels(n)": []}]) == []


def test_property_types_come_from_the_values_because_nothing_declares_them():
    rows = [
        {"properties(n)": {"name": "Alice", "age": 30, "score": 1.5, "ok": True}},
        {"properties(n)": {"tag": None}},
    ]
    assert property_shape(rows, "properties(n)") == {
        "name": "STRING",
        "age": "INT64",
        "score": "DOUBLE",
        "ok": "BOOLEAN",
        # Seen only as null: the property exists, nothing yet says what it holds.
        "tag": "",
    }


def test_a_property_null_on_one_node_takes_the_type_the_next_one_shows():
    rows = [{"properties(n)": {"nick": None}}, {"properties(n)": {"nick": "bobby"}}]
    assert property_shape(rows, "properties(n)")["nick"] == "STRING"


def test_a_category_reports_no_keys_because_there_are_none_to_report():
    """
    LatticeDB has no primary key, and needs none: identity is ``internal-id``, so
    expand re-selects a seed by ``id(n)`` rather than by a key property.
    """
    category = build_category("Person", [{"properties(n)": {"name": "Alice"}}])
    assert category.keys == [] and category.keysTypes == {}
    assert category.props == ["name"]


def test_a_relationship_endpoint_is_named_by_its_first_label():
    rows = [
        {"labels(n)": ["Person", "Employee"], "type(r)": "KNOWS", "labels(m)": ["Person"]},
        {"labels(n)": ["Person"], "type(r)": "LIVES_IN", "labels(m)": ["City"]},
    ]
    assert relationship_triples(rows) == [
        {"start": "Person", "name": "KNOWS", "end": "Person"},
        {"start": "Person", "name": "LIVES_IN", "end": "City"},
    ]


# -- the whole load ----------------------------------------------------------

CANNED = {
    "MATCH (n) RETURN DISTINCT labels(n) SKIP 0 LIMIT 200": [
        {"labels(n)": ["Person", "Employee"]},
        {"labels(n)": ["City"]},
    ],
    "MATCH (n:Person) RETURN properties(n) SKIP 0 LIMIT 100": [
        {"properties(n)": {"name": "Alice", "age": 30}}
    ],
    "MATCH (n:Employee) RETURN properties(n) SKIP 0 LIMIT 100": [
        {"properties(n)": {"badge": 7}}
    ],
    "MATCH (n:City) RETURN properties(n) SKIP 0 LIMIT 100": [
        {"properties(n)": {"name": "Portland"}}
    ],
    "MATCH (n)-[r]->(m) RETURN DISTINCT labels(n), type(r), labels(m) SKIP 0 LIMIT 200": [
        {"labels(n)": ["Person"], "type(r)": "KNOWS", "labels(m)": ["Person"]},
        {"labels(n)": ["Person"], "type(r)": "LIVES_IN", "labels(m)": ["City"]},
        {"labels(n)": ["Employee"], "type(r)": "KNOWS", "labels(m)": ["City"]},
    ],
    "MATCH (n)-[r:KNOWS]->(m) RETURN properties(r) SKIP 0 LIMIT 100": [
        {"properties(r)": {"since": 2020}}
    ],
    "MATCH (n)-[r:LIVES_IN]->(m) RETURN properties(r) SKIP 0 LIMIT 100": [
        {"properties(r)": {}}
    ],
}


async def test_the_whole_schema_comes_back_from_recorded_answers():
    read, _ = reader(CANNED)

    schema = await load_lattice_schema(read)

    assert [category.name for category in schema.categories] == ["Person", "Employee", "City"]
    assert [(r.startCategory, r.name, r.endCategory) for r in schema.relationships] == [
        ("Person", "KNOWS", "Person"),
        ("Person", "LIVES_IN", "City"),
        ("Employee", "KNOWS", "City"),
    ]
    assert schema.relationships[0].propsTypes == {"since": "INT64"}


async def test_a_relationship_type_is_sampled_once_however_many_pairs_it_joins():
    """KNOWS appears between two different pairs; it is still one sampling call."""
    read, asked = reader(CANNED)

    await load_lattice_schema(read)

    knows = [s for s in asked if s.startswith("MATCH (n)-[r:KNOWS]->")]
    assert len(knows) == 1


async def test_a_statement_the_engine_refuses_leaves_the_rest_of_the_schema_standing():
    """A store with no relationships still has categories worth reporting."""
    canned = dict(CANNED)
    canned["MATCH (n)-[r]->(m) RETURN DISTINCT labels(n), type(r), labels(m) SKIP 0 LIMIT 200"] = None

    read, _ = reader(canned)
    schema = await load_lattice_schema(read)

    assert [category.name for category in schema.categories] == ["Person", "Employee", "City"]
    assert schema.relationships == []


async def test_an_empty_store_is_an_empty_schema_rather_than_a_failure():
    read, _ = reader({})
    schema = await load_lattice_schema(read)
    assert schema.categories == [] and schema.relationships == []
