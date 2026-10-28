"""The engine seam's type surface: the vocabulary, what can hold a key, and the casts.

Split out of ``test_relational_engine.py``, which keeps the registry and the
connection half and defines the stand-ins imported here. What is pinned is what the
compiler reads off an engine: where a declared type lands in the shared vocabulary,
whether a column can carry ``<Label>:<key>``, and every cast-name spelling an engine
is allowed to answer with — the last because ``cast_sqlalchemy_type`` is now the one
parse of those names for both sides of the seam, so a name it cannot read has to fail
here, beside the engines that answer it.
"""

from __future__ import annotations

from typing import Optional, Set, Tuple

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mssql as MSSQL
from sqlalchemy.dialects import mysql as MYSQL
from sqlalchemy.dialects import postgresql as POSTGRESQL

from kineviz_database_proxy.drivers.dialect import is_numeric_key_type
from kineviz_database_proxy.drivers.relational import expression
from kineviz_database_proxy.drivers.relational.engine import (
    NUMERIC_PROPERTY_TYPES,
    PROPERTY_TYPES,
    cast_sqlalchemy_type,
    parse_declared_type,
)
from tests.test_relational_engine import ALL_ENGINES, engine

@pytest.mark.parametrize(
    "name,declared,expected",
    [
        # SQL Server
        ("mssql", "nvarchar(200)", "STRING"),
        ("mssql", "varchar(50)", "STRING"),
        ("mssql", "datetime2", "TIMESTAMP"),
        ("mssql", "datetimeoffset", "TIMESTAMP"),
        ("mssql", "date", "DATE"),
        ("mssql", "uniqueidentifier", "STRING"),
        ("mssql", "bigint", "INT64"),
        ("mssql", "int", "INT32"),
        ("mssql", "bit", "BOOL"),
        ("mssql", "float", "DOUBLE"),
        ("mssql", "decimal(19,4)", "DECIMAL"),
        ("mssql", "money", "DECIMAL"),
        ("mssql", "varbinary(max)", "BYTES"),
        # SQL Server's ``timestamp`` is the row version, not a date.
        ("mssql", "timestamp", "BYTES"),
        ("mssql", "hierarchyid", "STRING"),
        # PostgreSQL
        ("postgresql", "int8", "INT64"),
        ("postgresql", "int4", "INT32"),
        ("postgresql", "smallint", "INT32"),
        ("postgresql", "bytea", "BYTES"),
        ("postgresql", "timestamptz", "TIMESTAMP"),
        ("postgresql", "timestamp with time zone", "TIMESTAMP"),
        ("postgresql", "character varying(255)", "STRING"),
        ("postgresql", "double precision", "DOUBLE"),
        ("postgresql", "numeric(10,2)", "DECIMAL"),
        ("postgresql", "boolean", "BOOL"),
        ("postgresql", "uuid", "STRING"),
        ("postgresql", "jsonb", "JSON"),
        # A ``name`` column is a system-identifier string, not a number.
        ("postgresql", "name", "STRING"),
        # MySQL
        ("mysql", "mediumint", "INT32"),
        ("mysql", "bigint unsigned", "INT64"),
        ("mysql", "enum('a','b')", "STRING"),
        ("mysql", "set('a','b')", "STRING"),
        ("mysql", "longtext", "STRING"),
        ("mysql", "double", "DOUBLE"),
        ("mysql", "decimal(10,0)", "DECIMAL"),
        ("mysql", "datetime", "TIMESTAMP"),
        ("mysql", "year", "INT32"),
        ("mysql", "json", "JSON"),
        ("mysql", "longblob", "BYTES"),
        # An unknown type is a string, and it is still recorded as a column.
        ("mssql", "citext_extension_thing", "STRING"),
        ("postgresql", "city_id[]", "STRING"),
        ("mysql", "geometry", "BYTES"),
        ("mysql", "nonsense_type(3)", "STRING"),
    ],
)
def test_declared_types_normalise(name: str, declared: str, expected: str) -> None:
    spelling = engine(name)
    assert spelling.normalise_type(declared) == expected
    assert expected in PROPERTY_TYPES

def test_unknown_types_are_reported_rather_than_quietly_stringified() -> None:
    for name in ALL_ENGINES:
        spelling = engine(name)
        assert spelling.normalise_type("widget") == "STRING"
        assert not spelling.is_known_type("widget")
        assert spelling.is_known_type("varchar(200)")
        # An array is a real column of a type the vocabulary cannot describe.
        assert not spelling.is_known_type("text[]")

def test_explicit_precision_and_scale_win_over_the_spelling() -> None:
    assert engine("mysql").normalise_type("tinyint", precision=1) == "BOOL"
    assert engine("mysql").normalise_type("tinyint", precision=4) == "INT32"
    assert engine("mysql").normalise_type("tinyint(1)", precision=4) == "INT32"
    assert engine("mssql").normalise_type("varchar", precision=200) == "STRING"

def test_every_numeric_vocabulary_name_still_reads_as_a_numeric_key() -> None:
    # ``render_key_literal`` quotes a key unless ``is_numeric_key_type`` recognises
    # the type, and that set is compared lowercased: a numeric key that fails here
    # turns ``IN [1001]`` into ``IN ["1001"]`` and silently loses the index.
    assert NUMERIC_PROPERTY_TYPES == ("INT64", "INT32", "DOUBLE", "DECIMAL")
    for kind in NUMERIC_PROPERTY_TYPES:
        assert is_numeric_key_type(kind), kind
    for kind in ("STRING", "BOOL", "DATE", "TIMESTAMP", "BYTES", "JSON"):
        assert not is_numeric_key_type(kind), kind

def test_parsed_type_keeps_the_parts_a_cast_decision_needs() -> None:
    assert parse_declared_type("numeric(19,4)").scale == 4
    assert parse_declared_type("bigint unsigned").base == "bigint"
    assert parse_declared_type("bigint unsigned").unsigned is True
    assert parse_declared_type("text[]").array == 1
    assert parse_declared_type("varchar(50)").precision == 50
    # An argument that is not a number is not a precision.
    assert parse_declared_type("enum('a','b')").precision is None

@pytest.mark.parametrize("name", ALL_ENGINES)
def test_column_type_keeps_the_distinctions_that_cost_an_index(name: str) -> None:
    spelling = engine(name)
    assert isinstance(spelling.column_type("varchar(50)"), sa.types.String)
    assert isinstance(spelling.column_type("bigint"), sa.types.Integer)
    # SQL Server compares nvarchar against varchar differently, so they must not bind
    # the same way; the other two engines have one text type and get one answer.
    mssql = engine("mssql")
    assert repr(mssql.column_type("varchar(50)")) != repr(mssql.column_type("nvarchar(50)"))


@pytest.mark.parametrize("name", ALL_ENGINES)
def test_a_type_the_engine_has_no_name_for_is_answered_as_none(name: str) -> None:
    """
    The two duties are answered apart, because they fail apart.

    A bind against a text stand-in is a *wrong predicate* that raises nothing, so the
    engine's answer for a type it does not name has to be distinguishable from the answer
    for ``varchar`` — which is why this is None and not ``sa.String()``. Declaring a
    projected column only chooses how the value is decoded, so that duty says text, out
    loud, at ``declared_column_type``.
    """
    spelling = engine(name)
    for declared in (None, "", "widget", "STRING"):
        assert spelling.column_type(declared) is None, declared
    assert isinstance(spelling.declared_column_type("widget"), sa.types.String)
    # The text stand-in is only ever the *other* answer: a nameable type is handed over as
    # the very object the engine's table holds, so nothing in the bind path re-derives it.
    assert spelling.declared_column_type("bigint") is spelling.column_type("bigint")

@pytest.mark.parametrize(
    "name,declared,ok",
    [
        ("mssql", "bigint", True),
        ("mssql", "nvarchar(64)", True),
        # A ``text`` column cannot hold a unique index, so it cannot be an identity.
        ("mssql", "text", False),
        ("mssql", "ntext", False),
        ("mssql", "image", False),
        ("mssql", "xml", False),
        # A row version changes on every write: the node would rename itself.
        ("mssql", "rowversion", False),
        ("postgresql", "int8", True),
        ("postgresql", "uuid", True),
        ("postgresql", "bytea", False),
        ("postgresql", "jsonb", False),
        # A MySQL TEXT column can only be indexed by prefix, and a prefix UNIQUE key
        # lets two rows past char 40 share an id.
        ("mysql", "text", False),
        ("mysql", "longtext", False),
        ("mysql", "varchar(64)", True),
        ("mysql", "bigint", True),
        ("mysql", "json", False),
    ],
)
def test_single_column_key_decisions(name: str, declared: str, ok: bool) -> None:
    assert engine(name).single_column_key_ok(declared) is ok

def test_a_nullable_key_is_no_key_on_any_engine() -> None:
    for name in ALL_ENGINES:
        assert engine(name).single_column_key_ok("bigint", nullable=False) is True
        assert engine(name).single_column_key_ok("bigint", nullable=True) is False

@pytest.mark.parametrize(
    "function,column_type,expected",
    [
        # AVG over an integer column is integer division: the cast is the answer.
        ("avg", "int", "DECIMAL(38, 10)"),
        ("avg", "bigint", "DECIMAL(38, 10)"),
        ("avg", "smallint", "DECIMAL(38, 10)"),
        # A scale-0 decimal truncates the same way, so it gets the same cast.
        ("avg", "decimal(10,0)", "DECIMAL(38, 10)"),
        ("avg", "numeric", "DECIMAL(38, 10)"),
        # Already floating, already scaled: a cast would only cost the index.
        ("avg", "float", None),
        ("avg", "decimal(10,2)", None),
        ("avg", "nvarchar(200)", None),
        # SUM over an integer widens to bigint and then overflows.
        ("sum", "int", "DECIMAL(38, 0)"),
        ("sum", "bigint", "DECIMAL(38, 0)"),
        ("sum", "float", None),
        ("sum", "decimal(10,2)", None),
        # min/max preserve the type and count counts rows, not numbers.
        ("min", "int", None),
        ("max", "int", None),
        ("count", "int", None),
        ("count", "*", None),
    ],
)
def test_mssql_agg_casts_only_where_the_wrong_answer_is_real(
    function: str, column_type: Optional[str], expected: Optional[str]
) -> None:
    assert engine("mssql").agg_cast(function, column_type) == expected

@pytest.mark.parametrize("name", ("postgresql", "mysql"))
@pytest.mark.parametrize("function", ("avg", "sum", "count", "min", "max"))
@pytest.mark.parametrize("column_type", ("int", "bigint", "integer", "numeric", "float", None))
def test_the_other_two_engines_cast_nothing(name: str, function: str, column_type: str) -> None:
    # PostgreSQL's avg is already numeric and sum(int) is already bigint; MySQL's avg
    # is DOUBLE and its SUM over BIGINT widens to DECIMAL. A cast copied from SQL
    # Server would be decoration at best and would break the count shapes at worst.
    assert engine(name).agg_cast(function, column_type) is None

def test_cast_names_round_trip_through_sqlalchemy() -> None:
    # The policy speaks SQL type names and the compiler speaks SQLAlchemy types; one
    # parse, and both renderings have to agree or they will drift.
    numeric = cast_sqlalchemy_type("DECIMAL(38, 10)")
    assert isinstance(numeric, sa.Numeric)
    column = sa.column("n", sa.Integer)
    rendered = str(sa.select(sa.cast(column, numeric)).compile(dialect=MSSQL.dialect()))
    assert "CAST(n AS DECIMAL(38, 10))" in rendered, rendered
    # The policy name is not a dialect curiosity: the same parse renders on the other
    # two engines too, so a cast read off one engine's table cannot be nonsense there.
    for dialect in (POSTGRESQL.dialect(), MYSQL.dialect()):
        assert "DECIMAL(38, 10)" in str(
            sa.select(sa.cast(column, numeric)).compile(dialect=dialect)
        )
    # An uncast avg is exactly what SQLAlchemy 2.1.3 emits, which is the hole the
    # policy fills.
    bare = str(sa.select(sa.func.avg(column)).compile(dialect=MSSQL.dialect()))
    assert "avg(n)" in bare and "CAST" not in bare


@pytest.mark.parametrize(
    "cast_name,rendered",
    [
        ("DECIMAL(38, 10)", "DECIMAL(38, 10)"),
        ("DECIMAL(38, 0)", "DECIMAL(38, 0)"),
        ("decimal (38,10)", "DECIMAL(38, 10)"),
        ("DECIMAL(10)", "DECIMAL(10, 0)"),
        # The character narrowing is in the grammar with the numerics: an engine that
        # says "450 characters so this can be grouped" means it as exactly as the one
        # that says "38, 10 so this average does not truncate".
        ("NVARCHAR(450)", "NVARCHAR(450)"),
        ("nvarchar(4000)", "NVARCHAR(4000)"),
        ("nvarchar(max)", "NVARCHAR(max)"),
        # The whole character family lands on ``nvarchar``, because that is the only
        # target the engines name; an unqualified one is its unbounded member.
        ("varchar(50)", "NVARCHAR(50)"),
        ("char", "NVARCHAR(max)"),
    ],
)
def test_a_cast_name_parses_to_what_the_server_reads(cast_name: str, rendered: str) -> None:
    parsed = cast_sqlalchemy_type(cast_name)
    assert parsed is not None, cast_name
    statement = str(
        sa.select(sa.cast(sa.column("n", sa.Integer), parsed)).compile(dialect=MSSQL.dialect())
    )
    assert f"CAST(n AS {rendered})" in statement, statement


@pytest.mark.parametrize("cast_name", (None, "", "   ", "text", "date", "numeric(10,2)", "widget(3)"))
def test_a_name_outside_the_cast_grammar_is_no_cast_at_all(cast_name: Optional[str]) -> None:
    # ``None`` is every caller's "cast nothing", and it stays that way for a name this
    # seam has no answer for: inventing a type is how a wrong one reaches the server.
    # ``numeric(10,2)`` is in the list on purpose — the engines answer ``DECIMAL`` and
    # nothing else, and widening the grammar for a name nobody emits is how the two
    # tables of type names started drifting in the first place.
    assert cast_sqlalchemy_type(cast_name or "") is None
    assert expression.cast_from_name(cast_name) is None


#: Column types broad enough that every engine answers something for at least one of
#: them; the names below come from the engines, never from this list.
_CAST_SAMPLES: Tuple[Optional[str], ...] = (
    "int", "bigint", "smallint", "bit", "decimal(10, 0)", "numeric(10, 2)", "money",
    "float", "real", "varchar(50)", "nvarchar(200)", "nvarchar(max)", "char", "text",
    "ntext", "image", "xml", "date", "datetime2", "uniqueidentifier", "json", None,
)


def _cast_names(engine_name: str) -> Set[str]:
    """Every cast-name spelling one engine can answer, collected by asking it."""
    spelling = engine(engine_name)
    answered: Set[str] = set()
    for column_type in _CAST_SAMPLES:
        for function in ("avg", "sum", "count", "min", "max"):
            for cast_name in (
                spelling.agg_cast(function, column_type),
                spelling.group_key_cast(column_type),
                spelling.like_cast(column_type),
            ):
                if cast_name:
                    answered.add(cast_name)
    return answered


def test_the_engines_answer_only_cast_names_this_parser_reads() -> None:
    # The tripwire for a second table of type names: an engine that starts answering a
    # spelling this parser cannot read fails here, beside the parse it should have
    # extended, instead of silently compiling no cast at all in the compiler.
    assert _cast_names("mssql") == {
        "DECIMAL(38, 0)",
        "DECIMAL(38, 10)",
        "NVARCHAR(4000)",
        "NVARCHAR(450)",
    }
    for name in ("postgresql", "mysql"):
        assert _cast_names(name) == set()
    for name in ALL_ENGINES:
        for cast_name in sorted(_cast_names(name)):
            assert cast_sqlalchemy_type(cast_name) is not None, (name, cast_name)


def test_the_compilers_import_delegates_to_the_seams_own_parse() -> None:
    # ``expression.cast_from_name`` is what the compiler calls and stays what it calls;
    # it has to answer exactly what the seam answers, or the two halves of one
    # statement are built from two tables of type names again.
    for cast_name in sorted(_cast_names("mssql")):
        assert repr(expression.cast_from_name(cast_name)) == repr(cast_sqlalchemy_type(cast_name))


@pytest.mark.parametrize(
    "name,document_type,bound_type",
    [
        ("mssql", "nvarchar(max)", "NVARCHAR"),
        ("postgresql", "json", "JSON"),
        ("mysql", "json", "JSON"),
    ],
)
def test_a_shredded_document_is_typed_by_the_engine_that_shreds_it(
    name: str, document_type: str, bound_type: str
) -> None:
    spelling = engine(name)
    assert spelling.json_document_type() == document_type
    # The answer has to be a declared name this engine's own ``column_type`` reads,
    # because that is what binds it: the generic String fallback would hand the type
    # back to the DBAPI to guess, which is the conversion the shred clause exists to
    # prevent.
    assert type(spelling.column_type(document_type)).__name__ == bound_type


@pytest.mark.parametrize(
    "name,whole,text_type",
    [
        ("mssql", "bigint", "nvarchar(450)"),
        ("mysql", "BIGINT", "VARCHAR(450)"),
        ("postgresql", "bigint", "text"),
    ],
)
def test_a_shredded_column_is_declared_by_the_engine_that_shreds_it(
    name: str, whole: str, text_type: str
) -> None:
    # These two answers per engine used to be four literals in the compiler, with
    # SQL Server's index-key width written out a second time for a different
    # decision. They are the engine's own answers now, and the shredded SQL the
    # pipeline pins byte-for-byte is those answers.
    spelling = engine(name)
    assert spelling.shredded_column_type("bigint") == whole
    assert spelling.shredded_column_type("varchar(50)") == text_type


def _shred_answers(name: str) -> Set[str]:
    """Every column declaration one engine can answer, collected by asking it."""
    spelling = engine(name)
    return {spelling.shredded_column_type(declared) for declared in _CAST_SAMPLES}


def test_the_engines_answer_only_shred_names_their_own_binder_reads() -> None:
    # The tripwire ``test_the_engines_answer_only_cast_names_this_parser_reads`` is
    # for casts: the declaration goes into the table function's clause verbatim, so
    # an engine that starts answering a spelling its own binder cannot bind fails
    # here, beside the engine that answered it.
    assert _shred_answers("mssql") == {"bigint", "nvarchar(450)"}
    assert _shred_answers("mysql") == {"BIGINT", "VARCHAR(450)"}
    assert _shred_answers("postgresql") == {"bigint", "text"}
    for name in ALL_ENGINES:
        spelling = engine(name)
        for declared in _CAST_SAMPLES:
            answer = spelling.shredded_column_type(declared)
            # ``is_known_type`` is the parse check: asking ``column_type`` alone
            # cannot tell a real binding from the generic String fallback.
            assert spelling.is_known_type(answer), (name, declared, answer)

def test_group_and_like_casts_follow_the_bridges_reasoning() -> None:
    mssql = engine("mssql")
    # GROUP BY is illegal on the LOB types, and 450 is the index-key size, not a guess.
    assert mssql.group_key_cast("ntext") == "NVARCHAR(450)"
    assert mssql.group_key_cast("nvarchar(max)") == "NVARCHAR(450)"
    assert mssql.group_key_cast("nvarchar(200)") is None
    assert mssql.group_key_cast("int") is None
    # Only cast a column that is not already character data: a CAST around an indexed
    # varchar is the same index-killing move as binding the wrong parameter type.
    assert mssql.like_cast("int") == "NVARCHAR(4000)"
    assert mssql.like_cast("nvarchar(200)") is None
    assert mssql.like_cast("varchar") is None
    assert mssql.like_cast(None) is None
    for name in ("postgresql", "mysql"):
        assert engine(name).group_key_cast("text") is None
        assert engine(name).like_cast("int") is None
