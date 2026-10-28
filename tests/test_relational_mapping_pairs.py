"""
A link's endpoint pairs — ``mapping_pairs``: what flipping does to them, and what
happens when the same two ends are named twice.
"""

from __future__ import annotations

from typing import List, Optional

from kineviz_database_proxy.drivers.relational.mapping import (
    PROVENANCE_FLIPPED,
    from_document,
)
from kineviz_database_proxy.models.graph_mapping import MappingPair
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


def _kept_estimate(endpoints: List[MappingPair]) -> Optional[int]:
    """Fold two duplicate pairs and hand back the estimate the survivor carries."""
    doc = document(nodes=[person_node(), org_node()], edges=[works_for(endpoints=endpoints)])
    out = from_document(doc, None)
    kept = out.edges["WORKS_FOR"].endpoints
    assert len(kept) == 1, "the same two ends named twice is one pair"
    return kept[0].row_estimate


def test_the_duplicate_pair_that_was_counted_is_the_one_the_fold_keeps() -> None:
    uncounted = pair("Person", "Organization", rows=None)
    counted = pair("Person", "Organization", rows=400)
    # Both orders: a fold that kept whichever came first would drop a real count on the
    # way past it, and the surviving pair would then be ranked on no evidence at all.
    assert _kept_estimate([uncounted, counted]) == 400
    assert _kept_estimate([counted, uncounted]) == 400


def test_a_duplicate_pair_found_empty_outranks_one_nobody_counted() -> None:
    """The tie inside the fold: `0` is an answer, `None` is the absence of one."""
    uncounted = pair("Person", "Organization", rows=None)
    counted_empty = pair("Person", "Organization", rows=0)
    assert _kept_estimate([uncounted, counted_empty]) == 0
    assert _kept_estimate([counted_empty, uncounted]) == 0
