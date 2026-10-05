"""
The returning half of ``<Label>:<key>``: a seed re-selected as its column's own type.

``b31aa1d`` pinned the *outgoing* half — a key leaves as ``<Label>:<key>`` and a numeric
one is set-equal with graphxr-dev's table of 29 names. This file pins the *returning*
half, which is the one that fails silently: when a seed comes back and the pipeline has
to re-select the row, the bind must carry the **column's** type, so a ``DATE`` primary
key compares as a date, a numeric key stays numeric, and a key that arrives as quoted
text for an integer column is read back as an integer before it is sent.

The assertion is the bind's SQLAlchemy type and the literal the SQL receives, not the
coerced value alone. ``tests/test_relational_ids.py`` already proves ``ids.coerce_value``
turns ``"2024-01-31"`` into a ``date``; the value being right is half the duty. The other
half is that the *parameter* rides to the server typed as that column, because — as
``expression.py`` puts it — pyodbc sends an untyped string as ``nvarchar`` and SQL Server
then converts the column instead of the value, so an index lookup becomes a full scan with
nothing in the output to say so. A bind of the wrong type and a bind of the right value
render the *same text* here, so only the type distinguishes them; that is what this file
reads.

Nothing opens a socket. Every statement is built through the driver's own front door
(``query`` -> ``plan`` -> ``compile``) and rendered against a real SQLAlchemy dialect
object, which needs no DBAPI.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import mssql, mysql, postgresql
from sqlalchemy.sql import visitors

from graphxr_database_proxy.drivers.relational import compile, mapping, plan, query
from graphxr_database_proxy.drivers.relational.compile import compile_statement
from graphxr_database_proxy.drivers.relational.engine import EngineSpelling
from graphxr_database_proxy.drivers.relational.engines.mssql import MSSQLEngine
from graphxr_database_proxy.drivers.relational.engines.mysql import MySQLEngine
from graphxr_database_proxy.drivers.relational.engines.postgresql import PostgreSQLEngine
from graphxr_database_proxy.drivers.relational.schemas import NodeSchema
from graphxr_database_proxy.models.graph_mapping import GraphMapping

#: The three engines, each with the declared type names *it* spells for the four key
#: shapes a seed can arrive as. ``timestamp`` is the one the engines genuinely disagree
#: about (``datetime2`` / ``timestamp`` / ``datetime``), which is why it is per-engine
#: rather than a single string.
ENGINES: List[Dict[str, Any]] = [
    {
        "name": "mssql",
        "spelling": MSSQLEngine(),
        "dialect": mssql.dialect(),
        "numeric": "bigint",
        "text": "nvarchar(50)",
        "date": "date",
        "timestamp": "datetime2",
    },
    {
        "name": "postgresql",
        "spelling": PostgreSQLEngine(),
        "dialect": postgresql.dialect(),
        "numeric": "int8",
        "text": "varchar",
        "date": "date",
        "timestamp": "timestamp",
    },
    {
        "name": "mysql",
        "spelling": MySQLEngine(),
        "dialect": mysql.dialect(),
        "numeric": "bigint",
        "text": "varchar",
        "date": "date",
        "timestamp": "datetime",
    },
]

ENGINE_IDS = [engine["name"] for engine in ENGINES]
by_engine = pytest.mark.parametrize("engine", ENGINES, ids=ENGINE_IDS)


def _seed_nodes(key_type: str) -> Dict[str, NodeSchema]:
    """One label keyed by a single column of ``key_type``, plus a text column beside it."""
    return {
        "Seed": NodeSchema(
            label="Seed",
            table="T_SEED",
            key="k",
            key_type=key_type,
            props={"k": key_type, "name": "varchar(50)"},
        )
    }


def seed_lookup(spelling: EngineSpelling, key_type: str, literals: str) -> Any:
    """
    The returning path's own shape: ``MATCH (p:Seed) WHERE p.k IN [<seeds>] RETURN p``.

    That ``IN`` on the key column is exactly what ``/expand`` builds from a set of seed
    ids, so the binds this compiles are the binds the row lookup sends.
    """
    nodes = _seed_nodes(key_type)
    prepared = query.prepare(
        "MATCH (p:Seed) WHERE p.k IN [%s] RETURN p" % literals, default_limit=0
    )
    (branch,) = plan.concrete_plans(prepared.plan, nodes, {})
    return compile_statement(branch, nodes, {}, spelling=spelling, spec=prepared.spec)


def key_binds(compiled: Any) -> List[sa.BindParameter]:
    """The bind parameters the statement carries — the types that cross the wire."""
    return [
        element
        for element in visitors.iterate(compiled.select)
        if isinstance(element, sa.BindParameter)
    ]


def where_literal(compiled: Any, dialect: Any) -> str:
    """The WHERE clause with every bind inlined — the SQL as the server would read it."""
    text = str(compiled.select.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
    return text.split("WHERE", 1)[1].strip().replace("\n", " ")


# -- the date key ----------------------------------------------------------------------


@by_engine
def test_a_date_key_binds_as_the_date_column_and_not_as_quoted_text(engine: Dict[str, Any]) -> None:
    """
    A ``DATE`` primary key re-selected from a seed arrives as quoted text (it is not
    numeric, so ``render_key_literal`` quoted it) and must leave as a date.

    The bind carries ``sa.Date`` and the value is a real ``date``: a text bind against a
    date column is a type error on mssql and postgresql, and on mysql it silently
    compares wrong.
    """
    compiled = seed_lookup(engine["spelling"], engine["date"], "'2024-01-31'")
    (bind,) = key_binds(compiled)
    assert isinstance(bind.type, sa.Date), bind.type
    assert compiled.parameters[bind.key] == dt.date(2024, 1, 31)


# -- the timestamp key -----------------------------------------------------------------


@by_engine
def test_a_timestamp_key_binds_as_the_timestamp_column_for_each_engine_spelling(
    engine: Dict[str, Any]
) -> None:
    """
    ``datetime2`` / ``timestamp`` / ``datetime``: the engines spell a moment three ways,
    and each has to bind as its own, not as the quoted text the seed carries.
    """
    compiled = seed_lookup(engine["spelling"], engine["timestamp"], "'2024-01-31T12:34:56'")
    (bind,) = key_binds(compiled)
    assert isinstance(bind.type, sa.DateTime), (engine["timestamp"], bind.type)
    assert compiled.parameters[bind.key] == dt.datetime(2024, 1, 31, 12, 34, 56)


# -- the numeric key arriving as text --------------------------------------------------


@by_engine
def test_a_numeric_key_that_arrives_as_text_binds_as_the_integer_column(engine: Dict[str, Any]) -> None:
    """
    A JavaScript client can render an integer key it held as a float or a string, so a
    ``bigint`` key reaches the predicate as quoted ``'1001'``. The column's type, not the
    text, decides the bind: it must go out numeric, or SQL Server compares the column to
    an ``nvarchar`` and loses the index and the match.

    The value is an ``int`` and the rendered literal is bare ``1001`` — no quotes — which
    is the visible proof it does not compare as text.
    """
    compiled = seed_lookup(engine["spelling"], engine["numeric"], "'1001', '2002'")
    binds = key_binds(compiled)
    assert len(binds) == 2
    assert all(isinstance(bind.type, sa.Integer) for bind in binds), [bind.type for bind in binds]
    assert sorted(compiled.parameters[bind.key] for bind in binds) == [1001, 2002]
    assert all(isinstance(compiled.parameters[bind.key], int) for bind in binds)
    literal = where_literal(compiled, engine["dialect"])
    assert "'1001'" not in literal and "1001" in literal, literal


# -- the string key --------------------------------------------------------------------


@by_engine
def test_a_string_key_binds_as_the_character_column_it_declares(engine: Dict[str, Any]) -> None:
    """
    A ``varchar``/``nvarchar`` key stays text and is bound as a character type. On mssql
    the declared ``nvarchar`` must ride as ``NVARCHAR`` (the ``N'...'`` literal below),
    not collapse to plain varchar — that distinction is the whole reason
    ``mssql._COLUMN_TYPES`` keeps both spellings.
    """
    compiled = seed_lookup(engine["spelling"], engine["text"], "'avery'")
    (bind,) = key_binds(compiled)
    assert isinstance(bind.type, sa.String), bind.type
    assert compiled.parameters[bind.key] == "avery"
    literal = where_literal(compiled, engine["dialect"])
    if engine["name"] == "mssql":
        assert "N'avery'" in literal, literal


# -- a composite key must never reach the bind ------------------------------------------


def test_a_composite_key_is_refused_before_any_bind_is_built() -> None:
    """
    A key that spans two columns cannot be addressed by one ``<Label>:<key>`` seed, so
    the returning path has no single column to bind it against. ``mapping`` refuses it
    rather than quietly matching on one column and returning plausible wrong rows; this
    pins that the refusal is what stops the composite key, so the bind path never has to
    guess which half to send.
    """
    document = GraphMapping.model_validate(
        {
            "nodes": [
                {
                    "label": "Person",
                    "table": "T_PERSON",
                    "key": ["a", "b"],
                    "properties": {"a": "bigint", "b": "bigint"},
                }
            ],
            "edges": [],
        }
    )
    loaded = mapping.from_document(document, None)
    assert not loaded.valid, "a composite key loaded — the bind path would be handed an ambiguous key"
    reason = " ".join(message for _path, message in loaded.problems)
    assert "composite" in reason.lower(), reason


# -- proof-of-catch: the type the parser cannot read --------------------------

#: A type every engine's ``_COLUMN_TYPES`` map is missing, so ``column_type`` falls back to
#: a bare ``String`` — the silent degradation the guard now refuses. It is legal at the
#: mapping on postgresql (``geometry`` is not in ``postgresql.UNKEYABLE_TYPES``), which is
#: what makes it a reachable hole rather than a hypothetical one.
_UNREADABLE_KEY_TYPE = "geometry"


@by_engine
def test_a_key_column_whose_type_the_parser_cannot_read_fails_with_a_named_reason(
    engine: Dict[str, Any]
) -> None:
    """
    The proof-of-catch. A key whose declared type the engine has no SQLAlchemy type for
    used to bind as a text ``String``, match nothing, and answer an empty ``/expand`` —
    indistinguishable from a row that was never there. It now refuses, naming the column
    and the type, so the next person adding an engine or widening reflection finds the
    gap on the first compile instead of in a customer's empty canvas.

    This is the assertion a literal-only test could never make: ``WHERE k IN ('2024-01-31')``
    renders identically whether the bind is a real ``DATE`` or the ``String`` fallback, so
    catching the fallback means catching it before the statement is ever built.
    """
    with pytest.raises(compile.CompileError) as refused:
        seed_lookup(engine["spelling"], _UNREADABLE_KEY_TYPE, "'2024-01-31'")
    message = str(refused.value)
    assert _UNREADABLE_KEY_TYPE in message and "k" in message, message
