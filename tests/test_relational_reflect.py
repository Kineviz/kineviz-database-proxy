"""
The paged schema walk, through a fake inspector and no database at all.

This file also holds that fake — the reader every relational discovery test reads a
schema through — because paging is what it exists to prove: a walk that pulled the whole
library to answer one page would pass every judgement case in the sibling file and still
be the thing that takes a 3 000-table ERP down.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from graphxr_database_proxy.drivers.relational import reflect
from graphxr_database_proxy.drivers.relational.catalog import (
    PAGE_SIZE,
    Catalog,
    discover,
    propose,
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
    tree = ast.parse(Path(reflect.__file__).read_text(encoding="utf-8"))
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


def test_a_statistics_answer_of_nothing_is_a_gap_and_not_a_crash() -> None:
    # An engine with no statistics answers None for a page. The catalog still has to
    # finish with the tables it already read, or a missing catalog view would cost the
    # whole schema its labels.
    found = discover(FakeInspector(SPEC), row_counts=lambda page: None)
    assert len(found.tables) == len(SPEC)
    assert all(table.row_estimate is None for table in found.tables)
    assert len(draft(found).nodes) > 0


def test_a_statistics_answer_that_is_not_a_mapping_is_a_gap_too() -> None:
    found = discover(FakeInspector(SPEC), row_counts=lambda page: "no statistics here")
    assert len(found.tables) == len(SPEC)
    assert all(table.row_estimate is None for table in found.tables)

