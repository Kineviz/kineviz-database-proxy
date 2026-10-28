# -*- coding: utf-8 -*-
"""
The catalog probe, over rows recorded from real engines.

The rows below are the actual output of ``show_tables``, ``table_info`` and
``show_connection`` against a store created on 2026-08-27 -- including the Ladybug
quirk that makes filtering in Cypher unsafe.
"""

from __future__ import annotations

import pytest

from kineviz_database_proxy.drivers.embedded.kuzu_schema import (
    build_graph_schema,
    build_table_schema,
    load_kuzu_schema,
    normalize_kuzu_type,
    quote_literal,
    split_tables,
)
from kineviz_database_proxy.models.project import QueryData

SHOW_TABLES = [
    {"id": 0, "name": "Person", "type": "NODE", "database name": "local(kuzu)", "comment": ""},
    {"id": 1, "name": "City", "type": "NODE", "database name": "local(kuzu)", "comment": ""},
    {"id": 3, "name": "LivesIn", "type": "REL", "database name": "local(kuzu)", "comment": ""},
]

TABLE_INFO = {
    "Person": [
        {"property id": 0, "name": "name", "type": "STRING", "primary key": True},
        {"property id": 1, "name": "age", "type": "INT64", "primary key": False},
    ],
    "City": [
        {"property id": 0, "name": "cid", "type": "INT64", "primary key": True},
        {"property id": 1, "name": "name", "type": "STRING", "primary key": False},
    ],
    "LivesIn": [{"property id": 0, "name": "since", "type": "INT64", "primary key": False}],
}

CONNECTIONS = {
    "LivesIn": [
        {
            "source table name": "Person",
            "destination table name": "City",
            "source table primary key": "name",
            "destination table primary key": "cid",
        }
    ]
}


# -- table classification ---------------------------------------------------


def test_node_and_relationship_tables_are_told_apart():
    assert split_tables(SHOW_TABLES) == {
        "nodes": ["Person", "City"],
        "relationships": ["LivesIn"],
    }


def test_a_relationship_group_counts_as_a_relationship_table():
    rows = [{"name": "Knows", "type": "REL_GROUP"}]
    assert split_tables(rows)["relationships"] == ["Knows"]


def test_an_unknown_table_kind_is_ignored_rather_than_guessed():
    rows = [{"name": "Something", "type": "SEQUENCE"}]
    assert split_tables(rows) == {"nodes": [], "relationships": []}


# -- categories -------------------------------------------------------------


def test_a_categorys_key_is_read_from_the_catalog_not_guessed():
    # This is what makes a `label-key` identity safe here where it is fragile
    # elsewhere: the primary key is declared, so /expand never has to guess which
    # property to match on.
    schema = build_graph_schema(SHOW_TABLES, TABLE_INFO, CONNECTIONS)
    person = next(c for c in schema.categories if c.name == "Person")

    assert person.props == ["name", "age"]
    assert person.keys == ["name"]
    assert person.keysTypes == {"name": "STRING"}
    assert person.propsTypes == {"name": "STRING", "age": "INT64"}


def test_a_numeric_key_type_reaches_the_dialect_so_the_literal_is_emitted_bare():
    schema = build_graph_schema(SHOW_TABLES, TABLE_INFO, CONNECTIONS)
    city = next(c for c in schema.categories if c.name == "City")
    assert city.keysTypes == {"cid": "INT64"}


def test_a_boolean_primary_key_flag_is_read_whichever_way_it_is_spelled():
    info = {"T": [{"name": "id", "type": "STRING", "primary key": "true"}]}
    tables = [{"name": "T", "type": "NODE"}]
    assert build_graph_schema(tables, info, {}).categories[0].keys == ["id"]


# -- relationships ----------------------------------------------------------


def test_a_relationship_carries_its_endpoints_and_its_own_properties():
    schema = build_graph_schema(SHOW_TABLES, TABLE_INFO, CONNECTIONS)
    (lives_in,) = schema.relationships

    assert (lives_in.name, lives_in.startCategory, lives_in.endCategory) == (
        "LivesIn", "Person", "City",
    )
    assert lives_in.props == ["since"]
    # A relationship has no primary key in either engine.
    assert lives_in.keys == []


def test_a_relationship_group_becomes_one_entry_per_pair_it_connects():
    tables = [{"name": "Knows", "type": "REL_GROUP"}]
    connections = {
        "Knows": [
            {"source table name": "Person", "destination table name": "Person"},
            {"source table name": "Person", "destination table name": "City"},
        ]
    }
    built = build_graph_schema(tables, {"Knows": []}, connections).relationships
    assert [(r.startCategory, r.endCategory) for r in built] == [
        ("Person", "Person"),
        ("Person", "City"),
    ]
    assert {r.name for r in built} == {"Knows"}


def test_a_relationship_whose_connection_probe_failed_is_left_out_not_half_built():
    # `show_connection` failing is an answer, not a fault -- categories still come
    # back -- but an edge with no endpoints could not be drawn anyway.
    schema = build_graph_schema(SHOW_TABLES, TABLE_INFO, {})
    assert schema.relationships == []
    assert len(schema.categories) == 2


# -- types ------------------------------------------------------------------


@pytest.mark.parametrize(
    "declared,expected",
    [
        ("STRING", "STRING"),
        ("INT64", "INT64"),
        ("BOOL", "BOOLEAN"),
        ("INT32", "INT64"),
        ("UINT64", "INT64"),
        ("FLOAT", "DOUBLE"),
        ("TIMESTAMP", "DATETIME"),
        ("BLOB", "BYTEARRAY"),
        ("DATE", "DATE"),
        ("UUID", "UUID"),
    ],
)
def test_engine_type_names_are_translated_to_the_client_vocabulary(declared, expected):
    assert normalize_kuzu_type(declared) == expected


@pytest.mark.parametrize("declared", ["DECIMAL(18,3)", "STRING[]", "STRUCT(a INT64)"])
def test_a_compound_type_is_reported_as_declared(declared):
    # The client shows these verbatim; flattening a list to LIST loses the element
    # type for nothing.
    assert normalize_kuzu_type(declared) == declared


def test_an_absent_type_is_empty_rather_than_the_word_none():
    assert normalize_kuzu_type(None) == ""
    assert normalize_kuzu_type("") == ""


# -- the relational view ----------------------------------------------------


def test_the_table_schema_lists_both_node_and_relationship_tables():
    # Unlike the bolt family, these engines have declared tables, so /schema can
    # genuinely answer instead of refusing.
    schema = build_table_schema(SHOW_TABLES, TABLE_INFO)
    assert set(schema) == {"Person", "City", "LivesIn"}
    assert schema["Person"] == {"name": "STRING", "age": "INT64"}
    assert schema["LivesIn"] == {"since": "INT64"}


# -- the probe --------------------------------------------------------------


def test_a_table_name_is_quoted_as_a_literal_for_the_catalog_call():
    assert quote_literal("Person") == "'Person'"
    assert quote_literal("O'Brien") == "'O\\'Brien'"


async def test_the_probe_asks_for_each_table_and_each_connection():
    asked = []

    async def run(statement):
        asked.append(statement)
        if "show_tables" in statement:
            return table(["id", "name", "type"], [[0, "Person", "NODE"], [3, "LivesIn", "REL"]])
        if "table_info('Person')" in statement:
            return table(["name", "type", "primary key"], [["name", "STRING", True]])
        if "table_info('LivesIn')" in statement:
            return table(["name", "type", "primary key"], [["since", "INT64", False]])
        if "show_connection" in statement:
            return table(
                ["source table name", "destination table name"], [["Person", "Person"]]
            )
        return None

    schema = await load_kuzu_schema(run)

    assert [c.name for c in schema.categories] == ["Person"]
    assert schema.relationships[0].name == "LivesIn"
    assert "CALL show_connection('LivesIn') RETURN *" in asked
    # Filtered in Python, never with a WHERE on the call: `CALL show_tables() WHERE
    # type = 'NODE'` returns relationship tables too on Ladybug 0.19.1.
    assert not any("WHERE" in statement for statement in asked)


async def test_a_probe_the_engine_rejects_still_yields_what_it_could_read():
    async def run(statement):
        if "show_tables" in statement:
            return table(["name", "type"], [["Person", "NODE"], ["LivesIn", "REL"]])
        if "table_info('Person')" in statement:
            return table(["name", "type", "primary key"], [["name", "STRING", True]])
        return None  # everything else refused

    schema = await load_kuzu_schema(run)

    assert [c.name for c in schema.categories] == ["Person"]
    assert schema.relationships == []


def table(columns, rows):
    return QueryData(type="TABLE", data=[list(columns), *[list(row) for row in rows]])
