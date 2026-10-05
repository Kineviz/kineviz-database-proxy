"""
Which columns a record exposes, and what type they carry — ``mapping_props``.

A stated list is a whitelist, an empty one takes discovery's columns, a known table
outvotes the type the file remembered, and the columns that place an edge's ends are
structure rather than properties.
"""

from __future__ import annotations

from graphxr_database_proxy.drivers.relational.mapping import from_document
from tests.test_relational_mapping import (
    catalog,
    document,
    org_node,
    person_node,
    works_for,
)


def test_a_column_that_no_longer_exists_is_a_warning_on_the_column() -> None:
    doc = document(nodes=[person_node(properties={"person_id": "int", "nickname": "varchar(9)"})])
    out = from_document(doc, catalog())
    assert out.valid
    assert any("nickname" in text for text in out.warnings)


def test_the_live_schema_wins_a_type_argument() -> None:
    doc = document(nodes=[person_node(properties={"person_id": "varchar(9)"})])
    out = from_document(doc, catalog())
    # A key bound as the wrong type is a full scan that raises nothing, so the schema
    # in front of us outvotes the file about a schema someone remembered.
    assert out.nodes["Person"].key_type == "int"
    assert out.nodes["Person"].all_props()["person_id"] == "int"


def test_a_stated_property_list_is_a_whitelist_and_the_key_is_never_in_it_twice() -> None:
    out = from_document(document(nodes=[person_node()]), catalog())
    props = out.nodes["Person"].props
    assert "full_name" in props and "person_id" not in props
    assert list(out.nodes["Person"].all_props()) == ["person_id", "full_name"]


def test_excluding_a_property_takes_it_out_whatever_the_source_of_the_list_was() -> None:
    listed = from_document(
        document(nodes=[person_node(properties={"full_name": "varchar(100)", "photo": "varbinary(max)"}),
                        org_node()],),
        catalog(),
    )
    assert "photo" in listed.nodes["Person"].props
    dropped = from_document(
        document(nodes=[person_node(properties={"full_name": "varchar(100)", "photo": "varbinary(max)"},
                                     exclude_properties=["photo"]), org_node()]),
        catalog(),
    )
    assert "photo" not in dropped.nodes["Person"].props


def test_no_stated_properties_means_every_column_discovery_saw() -> None:
    out = from_document(document(nodes=[person_node(properties={})]), catalog())
    assert out.nodes["Person"].props == {"full_name": "varchar(100)"}


def test_an_edges_endpoint_columns_are_not_also_its_properties() -> None:
    edge = works_for(
        properties={"from_id": "int", "to_id": "bigint", "started_on": "date",
                    "employment_id": "int"}
    )
    out = from_document(document(nodes=[person_node(), org_node()], edges=[edge]), catalog())
    props = out.edges["WORKS_FOR"].props
    # The two columns that place the ends and the column that ids the link are all
    # structure; repeating them as relationship properties would double the panel and
    # let a person edit an endpoint by typing a property.
    assert props == {"started_on": "date"}
