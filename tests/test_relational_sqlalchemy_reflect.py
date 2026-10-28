"""Catalog reflection through an actual SQLAlchemy Inspector, without a server."""

from unittest.mock import patch

import pytest
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    create_engine,
    inspect,
)

from kineviz_database_proxy.drivers.relational.catalog import discover
from kineviz_database_proxy.drivers.relational.reflect import _bare_keys


@pytest.fixture
def inspector():
    engine = create_engine("sqlite://")
    metadata = MetaData()
    Table(
        "person",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("external_id", Integer, nullable=False, unique=True, index=True),
    )
    Table(
        "employment",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("person_id", Integer, ForeignKey("person.id")),
    )
    Table("unrequested", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)
    try:
        yield inspect(engine)
    finally:
        engine.dispose()


def test_real_inspector_returns_columns_keys_and_foreign_keys(inspector):
    found = discover(inspector, page_size=1)
    assert set(found.by_name) == {"person", "employment", "unrequested"}
    person = found.by_name["person"]
    assert person.columns == {"id": "INTEGER", "external_id": "INTEGER"}
    assert person.pk_columns == ("id",)
    assert "external_id" in person.uniques
    assert found.by_name["employment"].foreign_keys[0].ref_table == "person"
    assert found.warnings == []


@pytest.mark.parametrize(
    "method",
    [
        "get_multi_columns",
        "get_multi_pk_constraint",
        "get_multi_foreign_keys",
        "get_multi_unique_constraints",
        "get_multi_indexes",
    ],
)
def test_every_real_reflection_call_filters_names_at_the_source(inspector, method):
    with patch.object(inspector, method, wraps=getattr(inspector, method)) as probe:
        found = discover(inspector, page_size=1)
    assert len(found.tables) == 3
    assert [call.kwargs["filter_names"] for call in probe.call_args_list] == [
        ["employment"],
        ["person"],
        ["unrequested"],
    ]
    assert all("tables" not in call.kwargs for call in probe.call_args_list)


def test_tuple_and_dotted_table_keys_keep_the_table_spelling():
    assert _bare_keys(
        {("public", "a.b"): [1], (None, "Person"): [2]}, ["a.b", "Person"]
    ) == {
        "a.b": [1],
        "Person": [2],
    }
    assert _bare_keys({"a.b": [3]}, ["a.b"]) == {"a.b": [3]}
