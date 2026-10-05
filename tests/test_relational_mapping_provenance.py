"""
Who decided each record — ``mapping_provenance``: the closed set of kinds a page may
show, and the rename half of it, where a record's kind follows what discovery called
the same table.
"""

from __future__ import annotations

from graphxr_database_proxy.drivers.relational.mapping import (
    PROVENANCE_KINDS,
    PROVENANCE_RENAMED,
    from_document,
)
from tests.test_relational_mapping import (
    catalog,
    document,
    org_node,
    pair,
    person_node,
    works_for,
)


def test_every_provenance_a_page_can_show_is_one_of_the_five_it_documents() -> None:
    out = from_document(
        document(
            nodes=[person_node(label="Contact"), org_node()],
            edges=[works_for(endpoints=[pair("Contact", "Organization")]),
                   works_for(type="MANAGES", endpoints=[pair("Contact", "Contact")], flip=True)],
        ),
        catalog(),
    )
    assert out.valid, out.problems
    assert set(out.provenance.values()) <= set(PROVENANCE_KINDS)


def test_a_rename_rewrites_every_endpoint_pointing_at_the_old_label() -> None:
    doc = document(
        nodes=[person_node(label="Contact"), org_node()],
        edges=[works_for(endpoints=[pair("Person", "Organization")])],
    )
    out = from_document(doc, catalog())
    assert out.valid, out.problems
    assert "Person" not in out.nodes
    assert out.edges["WORKS_FOR"].endpoints[0].src.label == "Contact"
    assert out.provenance["Contact"] == PROVENANCE_RENAMED
    # The table never moved, so neither does the physical join: only the name GraphXR
    # sees changed, which is the entire point of a rename being safe to click.
    assert out.nodes["Contact"].table == "Person"


def test_a_rename_survives_being_written_by_hand_rather_than_clicked() -> None:
    doc = document(
        nodes=[person_node(label="Contact")],
        edges=[works_for(endpoints=[pair("Contact", "Organization")]),
               works_for(type="OLD_WAY", endpoints=[pair("Person", "Organization")])],
    )
    out = from_document(doc, catalog())
    assert out.valid, out.problems
    assert {p.src.label for p in out.edges["OLD_WAY"].endpoints} == {"Contact"}


def test_a_label_a_rename_left_behind_is_not_reported_as_a_typo() -> None:
    doc = document(nodes=[person_node(label="Contact"), org_node(label="Org")],
                   edges=[works_for(endpoints=[pair("Person", "Organization")])])
    out = from_document(doc, catalog())
    assert out.problems == []
    assert out.provenance["Contact"] == PROVENANCE_RENAMED
    assert out.provenance["Org"] == PROVENANCE_RENAMED
