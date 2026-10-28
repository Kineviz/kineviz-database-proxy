"""
What a table means as a node or an edge, against a fake inspector and no database at all.

The paged walk is pinned in `test_relational_reflect.py`, which holds the fake inspector
this file reads a schema through. What stays here is the judgement half — and the row
estimates, still asserted per page, because an estimate attached to the wrong table
passes the walk and still mis-ranks a link.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from kineviz_database_proxy.drivers.relational import catalog, mapping
from kineviz_database_proxy.drivers.relational.catalog import (
    MAX_EDGES,
    MAX_LABELS,
    MAX_PROPS,
    ForeignKey,
    discover,
    fk_name,
    propose,
    sanitize_identifier,
)
from tests.test_relational_reflect import (
    SPEC,
    FakeInspector,
    draft,
    edge_of,
    node_of,
    plain,
    read,
    table,
)


# ------------------------------------------------------------------ the filtering


def test_a_view_is_never_offered_as_a_node() -> None:
    spec = dict(SPEC)
    spec["vwPerson"] = table([("id", "int", False)], (None, ["id"]))
    catalog = read(spec, views=["vwPerson"])
    assert "vwPerson" in catalog.skipped
    assert "vwPerson" not in catalog.by_name
    assert "vwPerson" not in {node.label for node in draft(catalog).nodes}


def test_a_name_the_engine_never_answered_for_is_not_a_table() -> None:
    """
    ``has_table`` only says yes to a table the reflection actually answered for.

    A build can be handed the names to read — that is how ``/tables?table=`` costs one
    table rather than the schema — so an invented one arrives as a listed name with
    nothing reflected behind it, which must not arrive as an entry that answers as though
    the schema had it. The assumption the guard rests on: a real table in mssql,
    postgresql or mysql always has at least one column, so "no columns" can only mean "no
    such table". A view is still not a table either, and stays in ``skipped``.
    """

    class _Asked(FakeInspector):
        """An inspector listing exactly the names it was asked about, as ``_NamePage`` does."""

        def get_table_names(self, schema: Optional[str] = None) -> List[str]:
            return ["Person", "NotATable"]

    found = discover(_Asked(SPEC))

    assert found.has_table("Person")
    assert not found.has_table("NotATable")
    assert "NotATable" not in found.by_name
    assert [one.name for one in found.tables] == ["Person"]


def test_an_engine_internal_schema_says_no_rather_than_returning_nothing() -> None:
    found = discover(FakeInspector(SPEC), schema="information_schema")
    assert found.tables == []
    assert "engine" in found.warnings[0]


def test_diagram_and_temp_tables_are_nobody_business_data() -> None:
    spec = dict(SPEC)
    for name in ("sysdiagrams", "#scratch", "pg_stat_tmp", "mysql_backup"):
        spec[name] = table([("id", "int", False)], (None, ["id"]))
    catalog = read(spec)
    gone = set(spec) - set(SPEC)
    assert not ({t.name for t in catalog.tables} & gone)
    assert gone <= set(catalog.skipped)


# ------------------------------------------------------------------ the judgement


def test_a_junction_table_becomes_one_edge_and_not_two_records() -> None:
    doc = draft(read())
    edge = edge_of(doc, "EMPLOYMENT")
    assert edge.table == "Employment"
    assert edge.endpoints[0].src.label == "Person"
    assert edge.endpoints[0].dst.label == "Organization"
    assert edge.key == "employment_id"
    # The columns that place the ends are structure; only what is left over is a property.
    assert set(edge.properties) == {"started_on"}
    assert "Employment" not in {node.label for node in doc.nodes}


def test_a_record_table_becomes_a_node_with_every_other_column_as_a_property() -> None:
    doc = draft(read())
    person = node_of(doc, "Person")
    assert person.key == "person_id"
    assert person.properties == {"full_name": "varchar(100)"}
    assert person.include
    assert "primary key" in (person.notes or "")


def test_a_direct_foreign_key_points_from_the_child_that_holds_it() -> None:
    doc = draft(read())
    edge = edge_of(doc, "FK_ORDER_CUSTOMER")
    pair = edge.endpoints[0]
    assert pair.src.label == "Order" and pair.dst.label == "Person"
    # Both ends are columns of the edge table: the child's own key and the foreign key.
    assert pair.src.column == "order_id"
    assert pair.dst.column == "customer_id"
    assert "Order is the source" in (edge.notes or "")


def test_a_foreign_key_at_the_table_it_lives_in_gives_one_label_on_both_ends() -> None:
    doc = draft(read())
    edge = edge_of(doc, "FK_TREE_PARENT")
    pair = edge.endpoints[0]
    assert pair.src.label == "TreeNode" == pair.dst.label
    assert pair.dst.column == "parent_id"
    assert "both ends" in (edge.notes or "")


def test_two_keys_at_the_same_label_with_nothing_pointing_back_is_read_as_a_link() -> None:
    doc = draft(read())
    link = edge_of(doc, "MANAGER")
    assert link.endpoints[0].src.label == "Person"
    assert link.endpoints[0].dst.label == "Person"
    assert "both ends point at Person" in (link.notes or "")
    assert "decision, not a fact" in (link.notes or "")


def test_a_junction_named_by_its_table_and_a_foreign_key_by_its_constraint() -> None:
    doc = draft(read())
    assert edge_of(doc, "EMPLOYMENT").table == "Employment"
    assert edge_of(doc, "FK_ORDER_CUSTOMER").table == "Order"


def test_an_unnamed_foreign_key_is_named_from_the_child_the_column_and_the_parent() -> None:
    spec = {
        "A": table(
            [("id", "int", False), ("b1", "int", False), ("b2", "int", False), ("b3", "int", False)],
            (None, ["id"]),
            [("", ["b1"], "B"), ("", ["b2"], "B"), ("", ["b3"], "B")],
        ),
        "B": table([("id", "int", False)], (None, ["id"])),
    }
    doc = draft(read(spec))
    assert sorted(edge.type for edge in doc.edges) == ["A_B1_B", "A_B2_B", "A_B3_B"]
    assert fk_name(ForeignKey(name="", columns=("b1",), ref_table="B"), "A", "B") == "A_B1_B"


def test_two_constraints_of_the_same_name_become_two_types_with_a_suffix() -> None:
    # Three foreign keys, so the child is a record and each key is its own edge: a link
    # table with two of them would have been read as one type instead.
    spec = {
        "A": table(
            [("id", "int", False), ("b1", "int", False), ("b2", "int", False), ("b3", "int", False)],
            ("PK_A", ["id"]),
            [("FK_Same", ["b1"], "B"), ("FK_Same", ["b2"], "B"), ("FK_Other", ["b3"], "B")],
        ),
        "B": table([("id", "int", False)], ("PK_B", ["id"])),
    }
    doc = draft(read(spec))
    assert sorted(edge.type for edge in doc.edges) == ["FK_OTHER", "FK_SAME", "FK_SAME_2"]


def test_a_name_no_engine_would_take_unquoted_is_made_legal() -> None:
    assert sanitize_identifier("2 order") == "_2_order"
    assert sanitize_identifier("[my-order]") == "my_order"
    assert sanitize_identifier("Order") == "Order"
    assert sanitize_identifier("Order", upper=True) == "ORDER"
    assert sanitize_identifier("") == "UNNAMED"


def test_two_tables_differing_only_by_case_do_not_both_become_one_label() -> None:
    spec = {"person": table([("id", "int", False)], (None, ["id"])),
            "Person": table([("id", "int", False)], (None, ["id"]))}
    doc = draft(read(spec))
    assert sorted(node.label for node in doc.nodes) == ["Person", "person_2"]


def test_a_link_reaching_out_of_the_graph_is_reported_instead_of_drawn() -> None:
    spec = {
        "Person": table([("id", "int", False)], (None, ["id"])),
        "Org": table([("id", "int", False)], (None, ["id"])),
        "Employment": table(
            [("a", "int", False), ("b", "int", False)],
            (None, []),
            [("FK_E_P", ["a"], "Person"), ("FK_E_O", ["b"], "Missing")],
        ),
    }
    found = read(spec)
    doc = draft(found)
    assert [edge.type for edge in doc.edges] == []
    assert any("Employment" in text and "outside the graph" in text for text in found.warnings)


def test_a_composite_foreign_key_draws_no_edge_and_says_why() -> None:
    spec = {
        "Person": table([("id", "int", False)], (None, ["id"])),
        "Order": table(
            [("id", "int", False), ("p1", "int", False), ("p2", "int", False)],
            (None, ["id"]),
            [("FK_Order_People", ["p1", "p2"], "Person")],
        ),
    }
    found = read(spec)
    doc = draft(found)
    assert [edge.type for edge in doc.edges] == []
    assert any("composite" in text for text in found.warnings)


# -------------------------------------------------------------------- key choice


def test_the_key_is_the_primary_key_else_a_single_column_unique_else_the_table_is_out() -> None:
    doc = draft(read())
    session = node_of(doc, "Session")
    assert session.key == "token"
    assert "unique" in (session.notes or "")
    badge = node_of(doc, "Badge")
    assert not badge.include
    assert "NOT NULL" in (badge.notes or "")
    audit = node_of(doc, "Audit")
    assert not audit.include
    assert "['entity', 'when_id']" in (audit.notes or "")
    assert "single-column" in (node_of(doc, "Label").notes or "")


def test_a_proposal_of_tables_that_could_not_be_keyed_still_loads_cleanly() -> None:
    doc = draft(read())
    out = mapping.from_document(doc, doc)
    assert out.problems == [], out.problems
    assert sorted(out.nodes) == ["Order", "Organization", "Person", "Session", "TreeNode"]
    # A refused table stays in the file as a record of the decision and its reason,
    # which is the only thing that survives the editor regenerating the document.
    # Provenance is keyed by label for a node and by type for an edge.
    assert out.provenance["Audit"] == mapping.PROVENANCE_EXCLUDED
    assert out.provenance["EMPLOYMENT"] == mapping.PROVENANCE_AUTO


# --------------------------------------------------------------------- estimates


def test_row_estimates_arrive_per_page_and_carry_onto_the_pair() -> None:
    seen: List[Tuple[str, ...]] = []

    class Rows(FakeInspector):
        pass

    inspector = FakeInspector(SPEC)
    found = discover(
        inspector,
        row_counts=lambda page: seen.append(tuple(page)) or {"Employment": 12, "Order": 400},
    )
    doc = draft(found)
    assert edge_of(doc, "EMPLOYMENT").endpoints[0].row_estimate == 12
    assert edge_of(doc, "FK_ORDER_CUSTOMER").endpoints[0].row_estimate == 400
    assert seen == [tuple(sorted(SPEC))], "estimates are asked for one page of names at a time"
    out = mapping.from_document(doc, doc)
    assert out.edges["FK_ORDER_CUSTOMER"].representative.row_estimate == 400


def test_a_table_nobody_counted_stays_unknown_all_the_way_into_the_pair() -> None:
    # The catalog has always known it never counted; what it used to do on the way into the
    # draft was fill that gap in with a 0, which is where the unknown became a claim an edge
    # was ranked on. The ``None`` now travels into the proposal the editor reads.
    found = read()
    assert found.by_name["Employment"].row_estimate is None
    assert edge_of(draft(found), "EMPLOYMENT").endpoints[0].row_estimate is None


def test_a_statistics_gap_changes_the_count_and_nothing_else_in_the_proposal() -> None:
    """
    An engine that answers nothing proposes the same graph as one that answers zero.

    The two differ in the count and nowhere else: a proposal that also dropped the link, or
    named it differently, for want of statistics would be a second way for a missing number
    to be read as a fact about the schema.
    """
    nothing, zero = read(), read(rows={"Employment": 0})
    assert edge_of(draft(nothing), "EMPLOYMENT").endpoints[0].row_estimate is None
    assert edge_of(draft(zero), "EMPLOYMENT").endpoints[0].row_estimate == 0

    uncounted, counted = draft(nothing).model_dump(by_alias=True), draft(zero).model_dump(by_alias=True)
    for doc in (uncounted, counted):
        for endpoint in (pair for edge in doc["edges"] for pair in edge["endpoints"]):
            endpoint.pop("row_estimate", None)
    assert uncounted == counted


# --------------------------------------------------------------------------- caps


def test_the_label_cap_is_named_and_leaves_the_tail_of_the_page_out() -> None:
    spec = {"T{}".format(i): plain("T{}".format(i)) for i in range(MAX_LABELS + 7)}
    found = read(spec)
    doc = draft(found)
    assert len([node for node in doc.nodes if node.include]) == MAX_LABELS
    assert any("labels cap is 200" in text for text in found.warnings)
    assert doc.nodes[-1].include is False
    assert "cap" in (doc.nodes[-1].notes or "")
    assert doc.nodes[0].include is True


def test_the_cap_keeps_an_uncounted_table_ahead_of_one_counted_empty() -> None:
    # One table over the label cap, and only T0 was ever counted: at zero rows. Every
    # other table is unknown, so T0 is the only record anyone can honestly call small.
    spec = {"T{}".format(i): plain("T{}".format(i)) for i in range(MAX_LABELS + 1)}
    found = discover(FakeInspector(spec), row_counts=lambda page: {"T0": 0})
    doc = draft(found)
    assert [node.label for node in doc.nodes if not node.include] == ["T0"]
    warning = [text for text in found.warnings if "labels cap" in text][0]
    assert "200 of them carry no row count" in warning  # 201 offered, one was counted


def test_the_edge_cap_and_the_props_cap_are_reported_the_same_way() -> None:
    columns: List[Tuple[str, str, bool]] = [("id", "int", False)]
    columns += [("c{}".format(i), "int", True) for i in range(MAX_PROPS + 5)]
    columns += [("fk{}".format(i), "int", False) for i in range(MAX_EDGES + 2)]
    fks = [("FK_{}".format(i), ["fk{}".format(i)], "Parent") for i in range(MAX_EDGES + 2)]
    spec = {
        "Parent": table([("id", "int", False)], (None, ["id"])),
        "Child": table(columns, (None, ["id"]), fks),
    }
    found = read(spec)
    doc = draft(found)
    assert any("edges cap is 400" in text for text in found.warnings)
    assert any("props cap is 200" in text for text in found.warnings)
    assert any("carry no row count" in text for text in found.warnings)
    assert len([edge for edge in doc.edges if edge.include]) == MAX_EDGES
    assert len(node_of(doc, "Child").properties) == MAX_PROPS


# ---------------------------------------------------------------------- the draft


def test_a_proposal_is_a_document_a_page_can_edit_immediately() -> None:
    doc = draft(read())
    assert doc.version == 1
    assert all(edge.endpoints for edge in doc.edges)
    assert all(edge.key for edge in doc.edges)
    # Every guess says why it was made: a draft a person cannot argue with is a draft
    # they will only approve.
    assert all(edge.notes for edge in doc.edges)
    assert all(node.notes for node in doc.nodes)


def test_propose_reads_a_schema_itself_when_no_catalog_was_built_first() -> None:
    doc = propose(FakeInspector(SPEC))
    assert edge_of(doc, "EMPLOYMENT").endpoints[0].src.label == "Person"


def test_the_schema_a_catalog_read_carries_down_to_every_record() -> None:
    found = discover(FakeInspector(SPEC), schema="sales")
    doc = propose(found)
    assert doc.schema_name == "sales"
    out = mapping.from_document(doc, doc)
    assert out.nodes["Person"].schema == "sales"
    assert out.edges["EMPLOYMENT"].schema == "sales"
