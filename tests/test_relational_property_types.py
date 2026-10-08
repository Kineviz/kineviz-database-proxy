"""
What a live reflection hands ``column_type`` for a type it cannot name, and who wins
that argument — ``mapping_props``.

The spellings below are not typed in. Each one is produced by the engine's **own**
reflection resolver, on the payload the server sends for a type that resolver has no
name for: SQL Server's ``TYPE_NAME`` for a CLR column, MySQL's ``SHOW CREATE TABLE``
line for a spatial column, PostgreSQL's ``format_type`` for a composite column. That is
what makes the list a measurement rather than an argument: an audit over each engine's
``_TYPE_TABLE`` can only ever find the bases that table already contains, and a
user-defined type is by definition absent from it.

What the readers hand over is one of two different things, and the rule differs:

* **no answer at all** — all three dialects warn "Did not recognize type" and put
  ``sqltypes.NULLTYPE`` in the row, which the reader spells ``NULL``. Nobody knows what
  that column is, and the person who wrote the mapping is the only party left with an
  opinion, so the document keeps its type.
* **a name the engine has no binding for** — ``daterange``, ``hstore``, an array. The
  catalog *did* answer; only this proxy lacks a binding, which no edit to a mapping
  fixes. There the catalog still wins, because an unchecked stated type is the silent
  full scan the correction rule was written to prevent.
"""

from __future__ import annotations

from dataclasses import replace
import inspect
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
import sqlalchemy as sa
from sqlalchemy import types as sa_types
from sqlalchemy.dialects import mssql as mssql_dialect
from sqlalchemy.dialects import mysql as mysql_dialect
from sqlalchemy.dialects import postgresql as postgresql_dialect
from sqlalchemy.sql import visitors

from graphxr_database_proxy.drivers.relational import catalog, compile, mapping, plan, query
from graphxr_database_proxy.drivers.relational.engine_spelling import UnbindableType
from graphxr_database_proxy.drivers.relational.engines.mssql import MSSQLEngine
from graphxr_database_proxy.drivers.relational.engines.mysql import MySQLEngine
from graphxr_database_proxy.drivers.relational.engines.postgresql import PostgreSQLEngine
from graphxr_database_proxy.drivers.relational.mapping_index import names_a_type
from graphxr_database_proxy.drivers.relational.reflect import type_name
from graphxr_database_proxy.models.graph_mapping import GraphMapping
from tests.test_relational_reflect import FakeInspector


def mssql_column(declared: str, base: Optional[str] = None) -> Any:
    """The reflected ``type`` a live SQL Server inspector hands for a column of ``declared``."""
    dialect = mssql_dialect.dialect()
    if not hasattr(dialect, "_parse_column_info"):
        # SQLAlchemy 2.0 resolves types inside get_columns; 2.1 extracts the
        # resolver. Exercise the real 2.0 resolver with one catalog result row.
        class Connection:
            def execution_options(self, **kwargs):
                return self

            def execute(self, statement):
                values = ["odd", declared, base, 1, None, None, None, None,
                          None, None, False, None, None, None, None]
                row = dict(zip(statement.selected_columns, values))
                row.update({column.key: value for column, value in zip(statement.selected_columns, values)})
                for column, value in zip(statement.selected_columns, values):
                    for proxy in column.proxy_set:
                        row.setdefault(proxy, value)
                return SimpleNamespace(mappings=lambda: [row])

        return dialect.get_columns(Connection(), "Widget", schema="dbo")[0]["type"]
    row = dialect._parse_column_info(
        "odd", declared, True, None, None, None, None, None, None, False, False, None, None, None,
        base_type=base,
    )
    return row["type"]


def mysql_column(definition: str) -> Any:
    """The reflected ``type`` a live MySQL inspector hands for one ``SHOW CREATE TABLE`` line."""
    state = mysql_dialect.dialect()._tabledef_parser.parse(
        "CREATE TABLE `Widget` (\n%s\n) ENGINE=InnoDB" % definition, None
    )
    return state.columns[0]["type"]


def postgresql_column(format_type: str, domain: Optional[str] = None) -> Any:
    """
    The reflected ``type`` a live PostgreSQL inspector hands for one ``format_type`` payload.

    ``domain`` is what the server would have let the dialect look up: a user-defined domain
    arrives as its own name, which is why it needs a named-type loader rather than a
    recognised base type.
    """
    named = SimpleNamespace(enums={}, domains={})
    if domain is not None:
        named.domains = {
            (domain,): {
                "name": domain, "type": "character varying(11)", "visible": True, "schema": "public",
                "nullable": True, "default": None, "constraints": [], "collation": None,
                "collation_schema": None,
            }
        }
    reflect_type = postgresql_dialect.dialect()._reflect_type
    if "domains" in inspect.signature(reflect_type).parameters:
        return reflect_type(format_type, named.domains, named.enums,
                            type_description="column odd", collation=None)
    return reflect_type(
        format_type, named, type_description="column odd", collation=None
    )


#: Where the reader had **no** answer. ``declared`` is the type a mapping states instead:
#: a name that engine's own table binds.
UNNAMED: List[Dict[str, Any]] = [
    {"name": "mssql-geometry", "spelling": MSSQLEngine(), "reflected": mssql_column("geometry"),
     "schema": "dbo", "declared": "varchar(100)"},
    {"name": "mssql-hierarchyid", "spelling": MSSQLEngine(), "reflected": mssql_column("hierarchyid"),
     "schema": "dbo", "declared": "nvarchar(4000)"},
    {"name": "mssql-alias-over-geometry", "spelling": MSSQLEngine(),
     "reflected": mssql_column("shape_t", base="geometry"), "schema": "dbo", "declared": "varchar(100)"},
    {"name": "mysql-point", "spelling": MySQLEngine(), "reflected": mysql_column("  `odd` point NOT NULL,"),
     "schema": None, "declared": "varchar(64)"},
    {"name": "mysql-geometry", "spelling": MySQLEngine(), "reflected": mysql_column("  `odd` geometry NOT NULL,"),
     "schema": None, "declared": "varchar(64)"},
    {"name": "postgresql-composite", "spelling": PostgreSQLEngine(), "reflected": postgresql_column("address"),
     "schema": "public", "declared": "text"},
    {"name": "postgresql-xml", "spelling": PostgreSQLEngine(), "reflected": postgresql_column("xml"),
     "schema": "public", "declared": "text"},
]

#: Where the reader **named** the type and only this proxy has no binding for the name.
#: ``ARRAY`` and ``DOMAIN`` are in that set on sufferance: they are SQLAlchemy's class
#: names, not the column's ``text[]`` or ``usss``, because the reader compiles a type with
#: no dialect — see ``doc`` in the report for the fix, which belongs beside ``_TYPE_TABLE``.
NAMED: List[Dict[str, Any]] = [
    {"name": "postgresql-daterange", "reflected": postgresql_column("daterange"), "observed": "DATERANGE"},
    {"name": "postgresql-hstore", "reflected": postgresql_column("hstore"), "observed": "HSTORE"},
    {"name": "postgresql-macaddr8", "reflected": postgresql_column("macaddr8"), "observed": "MACADDR8"},
    {"name": "postgresql-array", "reflected": postgresql_column("text[]"), "observed": "ARRAY"},
    {"name": "postgresql-domain", "reflected": postgresql_column("usss", domain="usss"), "observed": "DOMAIN"},
]

by_case = pytest.mark.parametrize("case", UNNAMED, ids=[case["name"] for case in UNNAMED])
by_named = pytest.mark.parametrize("case", NAMED, ids=[case["name"] for case in NAMED])
POSTGRESQL = PostgreSQLEngine()


def resolved(case: Dict[str, Any], properties: Any) -> mapping.EffectiveMapping:
    """
    The project's mapping as the driver resolves it: live discovery, then the document.

    The key's type is filled from the catalog exactly as ``driver_mapping.with_key_types``
    fills it, because that step belongs to the driver and has nothing to do with a
    property's type — leaving it out would refuse every case here on its key instead of on
    its column.
    """
    inspector = FakeInspector(
        {
            "Widget": {
                "columns": [
                    ("id", sa_types.BIGINT(), False),
                    ("caption", sa_types.VARCHAR(100), True),
                    ("odd", case["reflected"], True),
                ],
                "pk": (None, ["id"]),
                "fks": [],
            }
        }
    )
    source = catalog.discover(inspector, schema=case.get("schema"))
    draft = catalog.propose(source)
    doc = GraphMapping.model_validate(
        {"nodes": [{"label": "Widget", "table": "Widget", "key": "id", "properties": properties}]}
    )
    out = mapping.from_document(doc, draft)
    assert out.valid, out.problems
    columns = {table.name: table.columns for table in source.tables}
    filled = dict(out.nodes)
    for label, node in filled.items():
        declared = (columns.get(node.table) or {}).get(node.key)
        if declared:
            filled[label] = replace(node, key_type=declared)
    return replace(out, nodes=filled)


def filtered(spelling: Any, nodes: Any, column: str, literal: str) -> Any:
    """``MATCH (w:Widget) WHERE w.<column> = '<literal>' RETURN w`` through the real pipeline."""
    prepared = query.prepare(
        "MATCH (w:Widget) WHERE w.%s = '%s' RETURN w" % (column, literal), default_limit=0
    )
    (branch,) = plan.concrete_plans(prepared.plan, nodes, {})
    return compile.compile_statement(branch, nodes, {}, spelling=spelling, spec=prepared.spec)


def binds(compiled: Any) -> List[sa.BindParameter]:
    return [element for element in visitors.iterate(compiled.select) if isinstance(element, sa.BindParameter)]


# -- the measurement -------------------------------------------------------------------


@by_case
def test_the_reader_hands_over_a_spelling_with_no_binding(case: Dict[str, Any]) -> None:
    """
    The measured half, before any rule is applied: what the live resolver produced, what the
    reader wrote down for it, and that this engine cannot bind what it wrote down.
    """
    observed = type_name(case["reflected"])
    assert observed == "NULL", (case["name"], observed)
    assert case["spelling"].column_type(observed) is None, observed
    assert not names_a_type(observed)


@by_named
def test_a_name_the_engine_cannot_bind_is_still_a_name(case: Dict[str, Any]) -> None:
    """The boundary of the measurement: no binding is not the same fact as no name."""
    observed = type_name(case["reflected"])
    assert observed == case["observed"], (case["name"], observed)
    assert POSTGRESQL.column_type(observed) is None, observed
    assert names_a_type(observed)


def test_the_placeholder_a_bare_list_leaves_behind_is_not_a_name() -> None:
    """``STRING`` is what ``_types_from`` writes for a name with no type: the same absence."""
    assert not names_a_type("STRING")
    assert not names_a_type("")
    assert not names_a_type(None)
    for spelling in (MSSQLEngine(), MySQLEngine(), POSTGRESQL):
        assert spelling.column_type("STRING") is None, spelling.name


# -- what the document is for ----------------------------------------------------------


@by_case
def test_a_bare_list_answers_the_graph_and_refuses_only_the_column_no_one_can_name(
    case: Dict[str, Any],
) -> None:
    """
    One column nobody can name must not take the label down with it: the bindable property
    still compiles, the unnameable one is still projected, and a filter on it is refused by
    name rather than answered with whatever text happens to match.
    """
    out = resolved(case, ["caption", "odd"])
    assert out.nodes["Widget"].props["odd"] == "NULL", out.nodes["Widget"].props

    compiled = filtered(case["spelling"], out.nodes, "caption", "Ada")
    (bind,) = binds(compiled)
    assert isinstance(bind.type, sa.String), bind.type
    assert "odd" in str(compiled.select), str(compiled.select)

    with pytest.raises(UnbindableType) as refused:
        filtered(case["spelling"], out.nodes, "odd", "x")
    assert "odd" in str(refused.value), str(refused.value)


@by_case
def test_the_document_wins_where_the_catalog_could_not_name_a_type(case: Dict[str, Any]) -> None:
    """
    The escape hatch the override document exists to be: where discovery has no answer, the
    type a person states for that column is the only type information in the system, so it
    is the one that binds.
    """
    out = resolved(case, {"caption": "varchar(1)", "odd": case["declared"]})
    assert out.nodes["Widget"].props["odd"] == case["declared"], out.nodes["Widget"].props

    compiled = filtered(case["spelling"], out.nodes, "odd", "x")
    (bind,) = binds(compiled)
    assert isinstance(bind.type, sa.String), (case["name"], bind.type)
    assert compiled.parameters[bind.key] == "x"


@by_case
def test_the_document_winning_is_said_out_loud(case: Dict[str, Any]) -> None:
    """A file beating the live schema is a decision a person has to be able to see."""
    out = resolved(case, {"caption": "varchar(1)", "odd": case["declared"]})
    (warning,) = [text for text in out.warnings if "odd" in text]
    assert case["declared"] in warning, warning


@by_named
def test_a_stated_type_does_not_overrule_a_name_the_catalog_gave(case: Dict[str, Any]) -> None:
    """
    The hole this file documents rather than fills. The catalog named this column, so the
    file is disagreeing with an answer instead of supplying one, and only the engine's own
    table can close it — which is why the refusal's remedy has to say so.
    """
    out = resolved(
        {"reflected": case["reflected"], "schema": "public"}, {"caption": "varchar(1)", "odd": "text"}
    )
    assert out.nodes["Widget"].props["odd"] == case["observed"], out.nodes["Widget"].props
    assert not [text for text in out.warnings if "odd" in text], out.warnings

    with pytest.raises(UnbindableType):
        filtered(POSTGRESQL, out.nodes, "odd", "x")


def test_the_catalog_still_outvotes_a_type_it_can_name() -> None:
    """
    The rule that was already here, unchanged: with a live schema that *names* the column, a
    wrong stated type loses, because binding as the wrong type costs a full scan that raises
    nothing. The new exception reaches ``odd`` and not ``caption``.
    """
    out = resolved(UNNAMED[0], {"caption": "bigint", "odd": "int"})
    assert out.nodes["Widget"].props["caption"] == "VARCHAR(100)", out.nodes["Widget"].props
    assert out.nodes["Widget"].props["odd"] == "int", out.nodes["Widget"].props
    assert not [text for text in out.warnings if "caption" in text], out.warnings
