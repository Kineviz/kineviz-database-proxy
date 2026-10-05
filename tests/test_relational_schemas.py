"""
The mapping IR: the four shapes every other relational layer is written against.

These tests pin the judgements a lane depends on rather than the fields it can see:
which pair an edge reports, what a swap carries across, and that a node's key is
part of its properties without being listed twice.
"""

from __future__ import annotations

import dataclasses

import pytest

from graphxr_database_proxy.drivers.relational.schemas import (
    BLANK_ENDPOINT,
    Endpoint,
    NodeSchema,
    Pair,
    RelSchema,
)


def account() -> Endpoint:
    return Endpoint(label="Account", column="account_id", column_type="bigint")


def order() -> Endpoint:
    return Endpoint(label="Order", column="order_id", column_type="int")


# --------------------------------------------------------------------- endpoint


def test_an_endpoint_is_immutable_because_a_plan_holds_thousands_of_them() -> None:
    end = account()
    with pytest.raises(dataclasses.FrozenInstanceError):
        end.column = "other"  # type: ignore[misc]


def test_a_pair_swaps_every_end_and_keeps_the_count_it_was_measured_at() -> None:
    pair = Pair(src=account(), dst=order(), row_estimate=42)
    swapped = pair.swapped()
    assert swapped.src == order()
    assert swapped.dst == account()
    # The estimate describes the link, not its direction, so it must survive the swap:
    # it is what `representative` ranks on, and a flipped edge that changed rank would
    # report a different pair to the schema panel than the same edge read forwards.
    assert swapped.row_estimate == 42
    assert swapped.swapped() == pair


def test_a_pair_nobody_counted_defaults_to_no_number_rather_than_to_an_empty_table() -> None:
    # `0` is a claim the engine made; `None` is the absence of one. A default of 0 here
    # would make every unanalysed MySQL link table look measured-and-empty, and would then
    # let that fabricated number decide which pair the edge is drawn between.
    assert Pair(src=account(), dst=order()).row_estimate is None
    # The gap survives the swap too: a flipped edge that invented a count would rank itself
    # differently from the same edge read forwards.
    assert Pair(src=account(), dst=order()).swapped().row_estimate is None


# ---------------------------------------------------------------------- records


def node(**overrides: object) -> NodeSchema:
    fields = {
        "label": "Person",
        "table": "Person",
        "key": "person_id",
        "key_type": "int",
        "props": {"full_name": "varchar(100)", "hired": "date"},
    }
    fields.update(overrides)
    return NodeSchema(**fields)  # type: ignore[arg-type]


def test_a_records_own_key_is_one_of_its_properties_and_is_not_listed_twice() -> None:
    schema = node()
    assert list(schema.all_props()) == ["person_id", "full_name", "hired"]
    assert schema.all_props()["person_id"] == "int"
    # The client's category record reads `props` and `keys` separately and types the
    # key from `keysTypes`, so a key that also sat in `props` would arrive twice.
    assert "person_id" not in schema.props


def test_a_key_that_is_also_a_declared_column_still_comes_from_the_key_type() -> None:
    schema = node(props={"person_id": "varchar(9)", "full_name": "varchar(100)"})
    assert schema.all_props()["person_id"] == "int"


def test_the_optionals_default_to_absent_rather_than_to_an_empty_guess() -> None:
    schema = node()
    assert schema.schema is None
    assert schema.show_as is None
    assert schema.notes == ""


# ----------------------------------------------------------------------- links


def rel(endpoints: list = None) -> RelSchema:
    return RelSchema(
        type="SHIPS_TO",
        table="OrderLine",
        key="line_id",
        key_type="int",
        endpoints=endpoints if endpoints is not None else [],
        props={"qty": "int"},
    )


def test_an_edge_reports_the_pair_most_likely_to_be_the_one_on_the_screen() -> None:
    small = Pair(src=account(), dst=order(), row_estimate=3)
    big = Pair(src=order(), dst=account(), row_estimate=900)
    schema = rel([small, big])
    assert schema.representative is big


def test_a_pair_nobody_counted_never_represents_an_edge_one_was_counted_for() -> None:
    uncounted = Pair(src=account(), dst=order())
    counted = Pair(src=order(), dst=account(), row_estimate=900)
    # Order both ways: the rule is "a gap loses", not "a gap happens to be first".
    assert rel([uncounted, counted]).representative is counted
    assert rel([counted, uncounted]).representative is counted


def test_a_counted_empty_table_beats_the_table_nobody_counted() -> None:
    """
    The tie that decides an edge's direction.

    Both candidates answer "as small as it gets" if an absent count is read as zero, so this
    is where a statistics gap would silently pick which label a relationship starts at. A
    pair the engine counted and found empty has *evidence*; an uncounted one has none.
    """
    uncounted = Pair(src=account(), dst=order())
    counted_empty = Pair(src=order(), dst=account(), row_estimate=0)
    assert rel([uncounted, counted_empty]).representative is counted_empty
    assert rel([counted_empty, uncounted]).representative is counted_empty


def test_two_uncounted_pairs_are_broken_by_document_order_not_by_which_is_reported() -> None:
    first = Pair(src=account(), dst=order())
    second = Pair(src=order(), dst=account())
    # Nothing ranks them, so the only honest answer is the stable one: a reload of the same
    # mapping must not redraw the edge the other way round.
    assert rel([first, second]).representative is first
    assert rel([second, first]).representative is second


def test_an_edge_with_no_pairs_at_all_reports_a_blank_end_instead_of_raising() -> None:
    representative = rel([]).representative
    assert representative.src == BLANK_ENDPOINT
    assert representative.dst == BLANK_ENDPOINT


def test_pairs_touching_prunes_to_the_labels_a_expand_actually_seeded() -> None:
    person_to_org = Pair(src=Endpoint("Person", "p_id", "int"), dst=Endpoint("Org", "o_id", "int"))
    org_to_thing = Pair(src=Endpoint("Org", "o_id", "int"), dst=Endpoint("Thing", "t_id", "int"))
    schema = rel([person_to_org, org_to_thing])
    assert schema.pairs_touching({"Thing"}) == [org_to_thing]
    assert schema.pairs_touching({"Org"}) == [person_to_org, org_to_thing]
    assert schema.pairs_touching(set()) == []
    # A set, not a list: the caller has one label per selected node and the check runs
    # per pair of every type in the mapping.
    assert schema.pairs_touching({"Org"}) is not schema.endpoints


def test_the_endpoint_columns_are_the_edge_tables_own_not_the_nodes() -> None:
    pair = Pair(src=Endpoint("Person", "person_id", "int"), dst=Endpoint("Org", "org_ref", "int"))
    assert pair.dst.column == "org_ref"
    # Nothing here resolves a node's key column: `NodeSchema.key` is the other half of
    # the join, and that split is what lets one column name serve both directions.
    assert pair.src.label == "Person"
