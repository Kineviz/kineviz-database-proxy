"""
The catalog side of loading — ``mapping_index``: what a live draft corrects, and what
it only ever warns about.

A table or a column the schema no longer has is a warning and never a blocked save,
and a scope name that matches nothing is said once, only when there is a schema to
match it against.
"""

from __future__ import annotations

from kineviz_database_proxy.drivers.relational.mapping import from_document
from tests.test_relational_mapping import (
    catalog,
    document,
    org_node,
    person_node,
    works_for,
)


def test_a_typo_in_a_scope_list_is_said_once_and_only_with_a_schema_in_front_of_us() -> None:
    with_catalog = from_document(
        document(nodes=[person_node()], exclude=["Persn"]), catalog()
    )
    assert any("Persn" in text for text in with_catalog.warnings)
    offline = from_document(document(nodes=[person_node()], exclude=["Persn"]), None)
    assert offline.warnings == []


def test_a_table_that_moved_is_a_warning_and_never_a_blocked_save() -> None:
    out = from_document(document(nodes=[person_node(table="People")]), catalog())
    assert out.valid
    assert any("People" in text and "not in this schema" in text for text in out.warnings)


def test_endpoint_types_are_taken_from_the_edge_table_when_the_file_leaves_them_out() -> None:
    discovered = catalog()
    discovered.edges[0].endpoints[0].src.column_type = "int"
    out = from_document(
        document(nodes=[person_node(), org_node()], edges=[works_for()]), discovered
    )
    assert out.edges["WORKS_FOR"].endpoints[0].src.column_type == "int"
