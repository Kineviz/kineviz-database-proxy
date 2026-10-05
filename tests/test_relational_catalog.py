"""
Discovery from a live schema, against a fake inspector and no database at all.

The reading and the judgement are pinned separately on purpose. A catalog read that
pulled the whole library to answer one page would pass every other test in this file
and still be the thing that takes a 3 000-table ERP down, so the page-by-page
assertions carry as much weight as the classification ones.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from graphxr_database_proxy.drivers.relational import catalog, mapping
from graphxr_database_proxy.drivers.relational.catalog import (
    MAX_EDGES,
    MAX_LABELS,
    MAX_PROPS,
    PAGE_SIZE,
    Catalog,
    ForeignKey,
    discover,
    fk_name,
    propose,
    sanitize_identifier,
    type_name,
)
from graphxr_database_proxy.models.graph_mapping import GraphMapping, MappingEdge, MappingNode

#: Columns are ``(name, type, nullable)``, primary keys ``(constraint, columns)``,
#: foreign keys ``(constraint, columns, referred_table)``.
Spec = Dict[str, Dict[str, Any]]

SPEC: Spec = {
    "Person": {
        "columns": [("person_id", "int", False), ("full_name", "varchar(100)", True)],
        "pk": ("PK_Person", ["person_id"]),
        "fks": [],
    },
    "Organization": {
        "columns": [("org_id", "bigint", False), ("legal_name", "nvarchar(80)", True)],
        "pk": ("PK_Organization", ["org_id"]),
        "fks": [],
    },
    "Employment": {
        "columns": [
            ("employment_id", "int", False),
            ("person_id", "int", False),
            ("org_id", "bigint", False),
            ("started_on", "date", True),
        ],
        "pk": ("PK_Employment", ["employment_id"]),
        "fks": [
            ("FK_Employment_Person", ["person_id"], "Person"),
            ("FK_Employment_Organization", ["org_id"], "Organization"),
        ],
    },
    "Order": {
        "columns": [("order_id", "int", False), ("customer_id", "int", False), ("total", "money", True)],
        "pk": (None, ["order_id"]),
        "fks": [("FK_Order_Customer", ["customer_id"], "Person")],
    },
    # Two foreign keys at the same label, and nothing points back at the table: a link
    # whose direction the schema cannot tell you.
    "Manager": {
        "columns": [("manager_id", "int", False), ("person_id", "int", False), ("boss_id", "int", False)],
        "pk": ("PK_Manager", ["manager_id"]),
        "fks": [
            ("FK_Manager_Person", ["person_id"], "Person"),
            ("FK_Manager_Boss", ["boss_id"], "Person"),
        ],
    },
    # A record table pointing at itself: one label on both ends, and a real direction.
    "TreeNode": {
        "columns": [("node_id", "int", False), ("parent_id", "int", True), ("title", "varchar(60)", True)],
        "pk": ("PK_Tree", ["node_id"]),
        "fks": [("FK_Tree_Parent", ["parent_id"], "TreeNode")],
    },
    "Label": {
        "columns": [("entity_id", "int", False), ("tag", "varchar(30)", True)],
        "pk": (None, []),
        "fks": [],
    },
    "Audit": {
        "columns": [("entity", "varchar(30)", False), ("when_id", "datetime2", False)],
        "pk": ("PK_Audit", ["entity", "when_id"]),
        "fks": [],
    },
    "Session": {
        "columns": [("token", "varchar(64)", False), ("person_id", "int", True)],
        "pk": (None, []),
        "fks": [],
        "unique": [["token"]],
    },
    "Badge": {
        # A unique that can be NULL is not an identity: a row without one cannot be found.
        "columns": [("badge_code", "varchar(20)", True), ("person_id", "int", False)],
        "pk": (None, []),
        "fks": [],
        "unique": [["badge_code"]],
    },
}


def table(
    columns: Sequence[Tuple[str, str, bool]],
    pk: Tuple[Optional[str], List[str]] = (None, []),
    fks: Optional[Sequence[Tuple[str, List[str], str]]] = None,
    unique: Optional[Sequence[List[str]]] = None,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"columns": list(columns), "pk": pk, "fks": list(fks or [])}
    if unique:
        entry["unique"] = [list(cols) for cols in unique]
    return entry


def plain(name: str) -> Dict[str, Any]:
    """A table with nothing in it but a single-column key: filler for the cap tests."""
    return table([("id", "int", False)], (None, ["id"]))


class FakeInspector:
    """The four required methods, the two optional probes, and a log of every ask."""

    def __init__(self, spec: Spec, views: Optional[Sequence[str]] = None) -> None:
        self.spec = spec
        self.views = list(views or [])
        self.calls: List[Tuple[str, Tuple[str, ...]]] = []

    def get_table_names(self, schema: Optional[str] = None) -> List[str]:
        self.calls.append(("tables", ()))
        return sorted(self.spec)

    def get_view_names(self, schema: Optional[str] = None) -> List[str]:
        self.calls.append(("views", ()))
        return list(self.views)

    def get_multi_columns(
        self, tables: Optional[Sequence[str]] = None, schema: Optional[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        self.calls.append(("columns", tuple(tables or ())))
        return {
            name: [
                {"name": column, "type": kind, "nullable": nullable}
                for column, kind, nullable in self.spec[name]["columns"]
            ]
            for name in tables or ()
        }

    def get_multi_pk_constraint(
        self, tables: Optional[Sequence[str]] = None, schema: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        self.calls.append(("pk", tuple(tables or ())))
        out: Dict[str, Dict[str, Any]] = {}
        for name in tables or ():
            constraint, columns = self.spec[name]["pk"]
            out[name] = {"constraint_name": constraint, "constrained_columns": list(columns)}
        return out

    def get_multi_foreign_keys(
        self, tables: Optional[Sequence[str]] = None, schema: Optional[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        self.calls.append(("fk", tuple(tables or ())))
        out: Dict[str, List[Dict[str, Any]]] = {}
        for name in tables or ():
            out[name] = [
                {
                    "name": fk[0],
                    "constrained_columns": list(fk[1]),
                    "referred_table": fk[2],
                    "referred_columns": [self._parent_key(fk[2])],
                }
                for fk in self.spec[name]["fks"]
            ]
        return out

    def get_multi_unique_constraints(
        self, tables: Optional[Sequence[str]] = None, schema: Optional[str] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        self.calls.append(("unique", tuple(tables or ())))
        return {
            name: [{"constrained_columns": list(cols)} for cols in self.spec[name].get("unique", [])]
            for name in tables or ()
        }

    def _parent_key(self, name: str) -> str:
        columns = self.spec[name]["pk"][1] if name in self.spec else []
        return columns[0] if columns else "id"


def read(
    spec: Optional[Spec] = None,
    views: Optional[Sequence[str]] = None,
    rows: Optional[Dict[str, int]] = None,
    page_size: int = PAGE_SIZE,
) -> Catalog:
    catalog, _ = logged(spec, views, rows, page_size)
    return catalog


def logged(
    spec: Optional[Spec] = None,
    views: Optional[Sequence[str]] = None,
    rows: Optional[Dict[str, int]] = None,
    page_size: int = PAGE_SIZE,
) -> Tuple[Catalog, FakeInspector]:
    inspector = FakeInspector(spec or SPEC, views)
    estimate = None
    if rows:
        def estimate(page: Sequence[str]) -> Dict[str, int]:
            return {name: rows[name] for name in page if name in rows}

    return discover(inspector, row_counts=estimate, page_size=page_size), inspector


def draft(source: Catalog) -> GraphMapping:
    return propose(source)


def node_of(doc: GraphMapping, label: str) -> MappingNode:
    return next(node for node in doc.nodes if node.label == label)


def edge_of(doc: GraphMapping, etype: str) -> MappingEdge:
    return next(edge for edge in doc.edges if edge.type == etype)


def pages_of(inspector: FakeInspector, method: str) -> List[Tuple[str, ...]]:
    return [names for name, names in inspector.calls if name == method]


# ------------------------------------------------------------------- the reading


def test_every_reflection_call_is_asked_for_one_page_of_names_at_a_time() -> None:
    """A 250-table schema is three pages, never one call holding 250 table names."""
    spec = {"T{}".format(i): plain("T{}".format(i)) for i in range(250)}
    catalog, inspector = logged(spec, page_size=100)
    assert len(catalog.tables) == 250
    for method in ("columns", "pk", "fk", "unique"):
        pages = pages_of(inspector, method)
        assert len(pages) == 3, (method, len(pages))
        for names in pages:
            assert len(names) <= 100, (method, len(names))
    assert sorted(name for page in pages_of(inspector, "columns") for name in page) == sorted(spec)


def test_an_empty_page_size_is_not_a_request_for_everything() -> None:
    catalog, inspector = logged(SPEC, page_size=0)
    assert len(catalog.tables) == len(SPEC)
    assert sum(len(names) for names in pages_of(inspector, "columns")) == len(SPEC)


def test_the_table_list_is_one_call_because_a_catalog_starts_at_the_names() -> None:
    _, inspector = logged(SPEC)
    assert len(pages_of(inspector, "tables")) == 1


def test_nothing_in_the_catalog_needs_sqlalchemy_or_a_driver() -> None:
    """The duck-typed protocol is why these tests run with no DBAPI installed."""
    tree = ast.parse(Path(catalog.__file__).read_text(encoding="utf-8"))
    imported: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "sqlalchemy" not in imported
    assert "typing_extensions" not in imported


def test_a_type_the_inspector_reports_as_an_object_still_gets_a_name() -> None:
    class Spelled:
        def compile(self) -> str:
            return "VARCHAR(50)"

    assert type_name(Spelled()) == "VARCHAR(50)"
    assert type_name("bigint") == "bigint"
    assert type_name("") == "STRING"
    assert type_name(None) == "STRING"


def test_an_probe_the_engine_refuses_to_answer_is_a_gap_and_not_a_failure() -> None:
    class NoViews(FakeInspector):
        def get_view_names(self, schema: Optional[str] = None) -> List[str]:  # type: ignore[override]
            raise NotImplementedError("this dialect has no view list")

    catalog = catalog_outcome(NoViews(SPEC))
    assert len(catalog.tables) == len(SPEC)


def catalog_outcome(inspector: FakeInspector) -> Catalog:
    return discover(inspector)


# ------------------------------------------------------------------ the filtering


def test_a_view_is_never_offered_as_a_node() -> None:
    spec = dict(SPEC)
    spec["vwPerson"] = table([("id", "int", False)], (None, ["id"]))
    catalog = read(spec, views=["vwPerson"])
    assert "vwPerson" in catalog.skipped
    assert "vwPerson" not in catalog.by_name
    assert "vwPerson" not in {node.label for node in draft(catalog).nodes}


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


def test_a_table_with_no_statistics_is_zero_and_not_invented() -> None:
    doc = draft(read())
    assert edge_of(doc, "EMPLOYMENT").endpoints[0].row_estimate == 0


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
