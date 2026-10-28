"""
What the document's ``include`` / ``exclude`` lists decide — ``mapping_scope``.

The scope rule is a name matched against either a record's label or its table, and
the consequence worth pinning is that taking a record out takes the links that needed
it out with it, as a warning the file still loads under.
"""

from __future__ import annotations

from kineviz_database_proxy.drivers.relational.mapping import (
    PROVENANCE_EXCLUDED,
    from_document,
)
from tests.test_relational_mapping import (
    catalog,
    document,
    org_node,
    person_node,
    works_for,
)


def test_excluding_a_record_drops_the_links_that_needed_it_and_says_which() -> None:
    out = from_document(
        document(
            nodes=[person_node(include=False), org_node()],
            edges=[works_for()],
        ),
        catalog(),
    )
    # The file still loads: unticking a record is a decision, not a mistake. What has
    # to be visible is that a link went with it.
    assert out.valid, out.problems
    assert "WORKS_FOR" not in out.edges
    assert out.provenance["Person"] == PROVENANCE_EXCLUDED
    assert out.provenance["WORKS_FOR"] == PROVENANCE_EXCLUDED
    assert any("WORKS_FOR" in text and "Person" in text for text in out.warnings)


def test_the_include_list_is_a_whitelist_and_its_names_are_matched_both_ways() -> None:
    doc = document(
        nodes=[person_node(), org_node()],
        edges=[works_for()],
        include=["Employment"],
    )
    out = from_document(doc, catalog())
    assert out.nodes == {}
    # `Employment` names a table, not a label, and still excluded the records that
    # were only ever reachable through it.
    assert out.provenance["WORKS_FOR"] == PROVENANCE_EXCLUDED


def test_an_exclude_naming_a_table_removes_the_record_by_that_name() -> None:
    out = from_document(document(nodes=[person_node(), org_node()], edges=[works_for()]),
                        catalog())
    assert out.valid
    dropped = from_document(
        document(nodes=[person_node(), org_node()], edges=[works_for()], exclude=["Person"]),
        catalog(),
    )
    assert "Person" not in dropped.nodes
    assert dropped.provenance["WORKS_FOR"] == PROVENANCE_EXCLUDED
