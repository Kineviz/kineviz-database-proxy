"""
A link's endpoint pairs — ``mapping_pairs``: what flipping does to them, and what
happens when the same two ends are named twice.
"""

from __future__ import annotations

from graphxr_database_proxy.drivers.relational.mapping import (
    PROVENANCE_FLIPPED,
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


def test_flipping_swaps_every_pair_of_the_link_and_marks_the_decision() -> None:
    doc = document(
        nodes=[person_node(), org_node()],
        edges=[works_for(flip=True, endpoints=[pair("Person", "Organization", rows=7),
                                                pair("Person", "Organization", "alt_from", "alt_to", rows=3)])],
    )
    out = from_document(doc, catalog())
    endpoints = out.edges["WORKS_FOR"].endpoints
    assert {p.src.label for p in endpoints} == {"Organization"}
    assert out.provenance["WORKS_FOR"] == PROVENANCE_FLIPPED
    # The estimate went with the pair, so the representative is still the same link.
    assert out.edges["WORKS_FOR"].representative.row_estimate == 7


def test_the_representative_pair_and_the_row_count_come_from_the_document() -> None:
    doc = document(nodes=[person_node(), org_node()],
                   edges=[works_for(endpoints=[pair("Person", "Organization", rows=5),
                                                pair("Organization", "Person", rows=50)])])
    out = from_document(doc, None)
    assert out.edges["WORKS_FOR"].representative.src.label == "Organization"


def test_the_duplicate_endpoint_pair_a_person_deletes_by_hand_is_not_two_links() -> None:
    doc = document(
        nodes=[person_node(), org_node()],
        edges=[works_for(endpoints=[pair("Person", "Organization", rows=1),
                                    pair("Person", "Organization", rows=400)])],
    )
    out = from_document(doc, None)
    assert len(out.edges["WORKS_FOR"].endpoints) == 1
    assert out.edges["WORKS_FOR"].endpoints[0].row_estimate == 400
