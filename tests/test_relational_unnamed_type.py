"""
What an engine's refusal to name a declared type costs, and what it must not cost.

``EngineSpelling.column_type`` answers None for a type the engine has no binding for,
because the text stand-in it used to answer is indistinguishable from a real one on the
wire: the value leaves as text, the column matches nothing, and an empty canvas is what
the person has to explain. Two properties are worth pinning about the swap.

The first is the blast radius. ``1d00038`` shipped an audit that no engine answers a bare
``String`` for a type a mapping may legitimately key on; if the None-set were wider than
that String-set, the refusal would start firing on mappings that work today. It is measured
here over every spelling each engine's own vocabulary and type table contain, plus the
placeholders a mapping document can hold, rather than asserted.

The second is where the refusal reaches. A *key* already refused at scope-build time, where
the label and column are known; the new cases are the paths that never had a check — a
property filter, a seed list going over as one array parameter — and the projection, which
is only ever a decode choice and must not refuse.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as postgresql_dialect
from sqlalchemy.sql import visitors

from graphxr_database_proxy.drivers.relational import compile, expression, predicate, reads
from graphxr_database_proxy.drivers.relational.engine import (
    PROPERTY_TYPES,
    ServerInfo,
    parse_declared_type,
)
from graphxr_database_proxy.drivers.relational.engine_spelling import UnbindableType
from graphxr_database_proxy.drivers.relational.engines import mssql as mssql_engine
from graphxr_database_proxy.drivers.relational.engines import mysql as mysql_engine
from graphxr_database_proxy.drivers.relational.engines import postgresql as postgres_engine

#: Each engine, with two declared types taken from *its own* files: one its type table
#: names and one its vocabulary knows but its type table does not. The second is the
#: spelling ``column_type`` refuses, and it is a real column type a customer has, not an
#: invented one — which is what makes the refusal worth pinning per engine.
ENGINES: List[Dict[str, Any]] = [
    {
        "name": "mssql",
        "spelling": mssql_engine.MSSQLEngine(),
        "vocabulary": mssql_engine._TYPE_TABLE,
        "types": mssql_engine._COLUMN_TYPES,
        "unnamed": "xml",
        "named": "bigint",
    },
    {
        "name": "mysql",
        "spelling": mysql_engine.MySQLEngine(),
        "vocabulary": mysql_engine._TYPE_TABLE,
        "types": mysql_engine._COLUMN_TYPES,
        "unnamed": "point",
        "named": "bigint",
    },
    {
        "name": "postgresql",
        "spelling": postgres_engine.PostgreSQLEngine(),
        "vocabulary": postgres_engine._TYPE_TABLE,
        "types": postgres_engine._COLUMN_TYPES,
        "unnamed": "point",
        "named": "bigint",
    },
]

ENGINE_IDS = [engine["name"] for engine in ENGINES]
by_engine = pytest.mark.parametrize("engine", ENGINES, ids=ENGINE_IDS)

#: A declaration that states no type at all: the empty string, the ``STRING`` the
#: catalog and ``mapping`` leave behind when nobody could name a column
#: (``schema_surface.UNKNOWN_TYPE``), and every name of the shared vocabulary a
#: hand-written mapping may hold. All of them are "nobody looked", so none of them is a
#: binding type — and all of them refused a key before this change too.
PLACEHOLDERS: List[Optional[str]] = [None, "", "STRING"] + [str(name).lower() for name in PROPERTY_TYPES]
#: Spellings the parser has to take apart before it looks anything up.
ARGUMENTS: List[str] = ["varchar(50)", "numeric(10,2)", "bigint unsigned", "text[]", "widget(3)"]


def _universe(entry: Dict[str, Any]) -> List[Optional[str]]:
    """Every declared-type spelling this engine can be asked about, from its own files."""
    everything = set(entry["vocabulary"]) | set(entry["types"]) | set(PLACEHOLDERS) | set(ARGUMENTS)
    # ``None`` is in there — the declaration that states nothing — and sorts before strings.
    return sorted(everything, key=lambda declared: (declared is not None, str(declared)))


# -- the blast radius ------------------------------------------------------------------


@by_engine
def test_the_refusal_reaches_exactly_the_types_the_key_guard_already_refused(engine: Dict[str, Any]) -> None:
    """
    None replaces the ``sa.String()`` fallback for the *same* set of spellings and nothing
    else, which is what keeps a working mapping working: ``1d00038``'s guard tested for a
    fallback that was exactly ``sa.String``, so any spelling outside that set would now
    refuse a query that used to run.
    """
    spelling, table = engine["spelling"], engine["types"]
    for declared in _universe(engine):
        base = parse_declared_type(declared or "").base
        answered_before = type(table.get(base, sa.String())) is sa.String
        assert (spelling.column_type(declared) is None) is answered_before, (engine["name"], declared)


@by_engine
def test_every_type_the_engine_names_is_still_handed_over_unchanged(engine: Dict[str, Any]) -> None:
    """
    A nameable declared type binds as *the very object the engine's table holds*, so the
    swap to an Optional answer cannot have re-derived, re-ordered or narrowed one of them.
    Identity is the assertion: a rebuilt ``BIGINT()`` would compare equal and still be a
    different answer.
    """
    spelling, table = engine["spelling"], engine["types"]
    for declared in _universe(engine):
        base = parse_declared_type(declared or "").base
        if base not in table:
            continue
        assert spelling.column_type(declared) is table[base], (engine["name"], declared)


#: Every type each engine's own vocabulary knows that its type table has no binding for,
#: measured from the two tables rather than typed in. These are the spellings that reach
#: the refusal, and they are real column types a customer has: SQL Server's non-comparable
#: ``xml`` and spatial family, MySQL's spatial family, PostgreSQL's geometric and
#: text-search types. Widening a table is a decision to take with ``_TYPE_TABLE`` in the
#: other hand, so the list is pinned to make that decision visible when it happens.
REFUSED_BY_ENGINE: Dict[str, List[str]] = {
    "mssql": ["geography", "geometry", "hierarchyid", "sql_variant", "sysname", "xml"],
    "mysql": [
        "geometry",
        "geometrycollection",
        "linestring",
        "multilinestring",
        "multipoint",
        "multipolygon",
        "point",
        "polygon",
    ],
    "postgresql": [
        "box",
        "circle",
        "line",
        "lseg",
        "path",
        "point",
        "polygon",
        "tsquery",
        "tsvector",
    ],
}


@by_engine
def test_no_engine_hands_out_a_bare_string_as_an_answer(engine: Dict[str, Any]) -> None:
    """
    The general form of the equivalence, and the reason it needs no enumeration: the old
    guard refused whatever ``type(answer) is sa.String`` produced, and no entry of an
    engine's table is that class — the character types are all dialect subclasses
    (``VARCHAR``, ``NVARCHAR``, ``TEXT``, ``name``/``xml`` as ``sa.Text``). So refusing and
    naming partition *every* spelling a mapping can hold, not only the ones listed here, and
    a key that bound before cannot start refusing now.
    """
    for declared, answer in engine["types"].items():
        assert type(answer) is not sa.String, (engine["name"], declared, type(answer))


@by_engine
def test_the_types_that_reach_the_refusal_are_exactly_these(engine: Dict[str, Any]) -> None:
    """
    The blast radius, enumerated from the engine's own two tables instead of asserted.

    A refused spelling is not a broken mapping: ``1d00038``'s guard already refused it as a
    key, because the fallback it tested for was the same answer None replaces. What the
    refusal adds is the name, on the paths that never had a key check.
    """
    spelling, vocabulary = engine["spelling"], engine["vocabulary"]
    refused = sorted(name for name in vocabulary if spelling.column_type(name) is None)
    assert refused == REFUSED_BY_ENGINE[engine["name"]], refused


@by_engine
def test_a_refused_type_that_a_mapping_may_still_key_on_is_refused_by_the_guard(engine: Dict[str, Any]) -> None:
    """
    Of the refused spellings, the ones the engine's own keyability rule lets a single-column
    ``<Label>:<key>`` through are listed here — PostgreSQL's geometric types and SQL Server's
    ``hierarchyid``/``sysname`` — because a person keying on one meets the guard, not a
    silent empty canvas. Nothing about that is *new*: the guard fired on the same set before,
    and it fires on the same set now.
    """
    spelling, vocabulary = engine["spelling"], engine["vocabulary"]
    keyable = sorted(
        name
        for name in vocabulary
        if spelling.column_type(name) is None and spelling.single_column_key_ok(name)
    )
    for declared in keyable:
        with pytest.raises(compile.UnbindableKey) as refused:
            compile._require_bindable_key(spelling, "node label", "Seed", "k", declared)
        assert declared in str(refused.value) and "k" in str(refused.value), refused


# -- where the refusal reaches ---------------------------------------------------------


def _scope(entry: Dict[str, Any], *, declared: str, server: Any = None) -> expression.PredicateScope:
    """One node variable with a ``declared``-typed column called ``detail``, and a bigint key."""
    metadata = sa.MetaData()
    table = sa.Table(
        "T_DETAIL",
        metadata,
        sa.Column("id", sa.BigInteger()),
        sa.Column("detail", sa.String()),
    )
    alias = table.alias("v0")
    bound = expression.BoundVariable(
        var="n",
        entity="Detail",
        kind=expression.VERTEX_KIND,
        columns={"id": alias.c.id, "detail": alias.c.detail},
        types={"id": "bigint", "detail": declared},
        key_prop="id",
    )
    return expression.PredicateScope([bound], spelling=entry["spelling"], server=server)


def _parse(text: str) -> Any:
    return predicate.parse_expression(text)


@by_engine
def test_a_property_filter_on_a_type_the_engine_cannot_name_refuses_by_name(engine: Dict[str, Any]) -> None:
    """
    The path with no early check: nothing here is a key, so ``compile``'s scope guard never
    ran. It used to bind ``'x'`` as text against a ``geometry``/``xml``/``point`` column and
    let the server answer with its own type error; now the refusal names the column, the
    declared type and the engine, and records no parameter on the way out.
    """
    scope = _scope(engine, declared=engine["unnamed"])
    with pytest.raises(UnbindableType) as refused:
        expression.compile_predicate(_parse("n.detail = 'x'"), scope)
    message = str(refused.value)
    assert "detail" in message and engine["unnamed"] in message and engine["name"] in message, message
    assert scope.parameters == {}, scope.parameters


@by_engine
def test_a_nameable_property_filter_still_binds_as_the_columns_own_type(engine: Dict[str, Any]) -> None:
    """The refusal is for what an engine cannot name; a nameable type binds as it did."""
    scope = _scope(engine, declared=engine["named"])
    condition = expression.compile_predicate(_parse("n.detail = 1001"), scope)
    assert scope.parameters == {"gx_p1": 1001}
    (bind,) = [element for element in visitors.iterate(condition) if isinstance(element, sa.BindParameter)]
    assert isinstance(bind.type, sa.Integer), (engine["name"], bind.type)


#: PostgreSQL is the engine whose bulk form is one array parameter, so it is the one whose
#: ``ARRAY(None)`` used to reach the server as a driver error.
POSTGRESQL = next(entry for entry in ENGINES if entry["name"] == "postgresql")
PROBED = ServerInfo(engine="postgresql", major=16, minor=4)


def test_a_seed_list_going_over_as_one_array_parameter_refuses_by_name() -> None:
    """
    The ``IN`` of a big selection leaves as one array parameter, whose element type is the
    column's own — the same answer ``bind`` takes, so a list of seeds on an unnamed column
    is refused the same way and before any value is collected.
    """
    scope = _scope(POSTGRESQL, declared=POSTGRESQL["unnamed"], server=PROBED)
    values = ", ".join("'p%d'" % index for index in range(150))
    with pytest.raises(UnbindableType) as refused:
        expression.compile_predicate(_parse("n.detail IN [%s]" % values), scope)
    assert POSTGRESQL["unnamed"] in str(refused.value)
    assert scope.parameters == {}, scope.parameters


def test_a_seed_list_the_engine_names_still_leaves_as_one_array_parameter() -> None:
    """The unchanged half of the same path: 150 bigint ids, one parameter, ``ANY``."""
    scope = _scope(POSTGRESQL, declared="bigint", server=PROBED)
    values = ", ".join(str(index) for index in range(150))
    condition = expression.compile_predicate(_parse("n.detail IN [%s]" % values), scope)
    rendered = str(
        sa.select(scope.by_var["n"].columns["id"]).where(condition).compile(dialect=postgresql_dialect.dialect())
    )
    assert "ANY" in rendered.replace("\n", " ")
    assert list(scope.parameters.values()) == [list(range(150))]
    # The element type is the column's own, which is the whole reason the parameter is
    # worth typing: an array of text against an int8 column is the comparison the seam
    # exists to prevent.
    (bind,) = [element for element in visitors.iterate(condition) if isinstance(element, sa.BindParameter)]
    assert isinstance(bind.type, sa.ARRAY) and isinstance(bind.type.item_type, sa.BigInteger), bind.type


def test_an_engine_that_cannot_name_its_own_document_type_is_called_out() -> None:
    """
    The shredded document's type is the engine's *own* answer, so a None there is not a
    column nobody mapped — no edit to a mapping fixes it. It refuses, and says which two of
    the engine's own answers disagree.
    """

    class Contradictory(mssql_engine.MSSQLEngine):
        def json_document_type(self) -> str:
            return "widget"

    server = ServerInfo(engine="mssql", major=15, minor=0, compat_level=150)
    entry = dict(next(engine for engine in ENGINES if engine["name"] == "mssql"))
    entry["spelling"] = Contradictory()
    scope = _scope(entry, declared="bigint", server=server)
    values = ", ".join(str(index) for index in range(2001))
    with pytest.raises(UnbindableType) as refused:
        expression.compile_predicate(_parse("n.id IN [%s]" % values), scope)
    message = str(refused.value)
    assert "json_document_type" in message and "widget" in message, message


# -- what it must not reach ------------------------------------------------------------


@by_engine
def test_an_unnamed_type_in_a_projection_is_still_declared_as_text(engine: Dict[str, Any]) -> None:
    """
    The two declaration sites — the branch's table and the preview's — read a column the
    engine may not name and never send a value to it, so they take the text answer at
    ``declared_column_type`` rather than refusing. ``NullType`` would be the literal truth
    and would change the SQL: SQLAlchemy renders a comparison against a NullType column
    with an added ``::VARCHAR`` cast, which is the thing this seam exists to stop doing.
    """
    spelling = engine["spelling"]
    assert isinstance(spelling.declared_column_type(engine["unnamed"]), sa.String)
    table = reads.table_of(spelling, "T_DETAIL", {"id": "bigint", "detail": engine["unnamed"]}, None)
    assert isinstance(table.c.detail.type, sa.String)
    assert table.c.id.type is spelling.column_type("bigint")
