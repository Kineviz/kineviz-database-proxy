"""Offline tests for the relational engine seam: no DBAPI, no server, no network.

The engine modules are policy, not I/O, so every one of these runs against a
``DatabaseConfig`` stand-in and a connection double that records statements. What
they pin down are the answers the compiler and the pool lanes build on: where a
secret goes, which cast is load-bearing, when an id list stops fitting, and what a
server that cannot be served has to say about itself.
"""

from __future__ import annotations

import ast
import dataclasses
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

import pytest
import sqlalchemy as sa
from sqlalchemy import URL

from graphxr_database_proxy.drivers.dialect import is_numeric_key_type
from graphxr_database_proxy.drivers.relational import engine as engine_module
from graphxr_database_proxy.drivers.relational.engine import (
    ID_LIST_STRATEGIES,
    NUMERIC_PROPERTY_TYPES,
    PROPERTY_TYPES,
    EngineSpelling,
    ServerInfo,
    UnknownEngine,
    cast_sqlalchemy_type,
    configured_schema,
    engines_for,
    parse_declared_type,
    registered_engines,
)
from graphxr_database_proxy.drivers.relational.engines import mssql as mssql_module
from graphxr_database_proxy.drivers.relational.engines.mssql import MSSQLEngine
from graphxr_database_proxy.drivers.relational.engines import mysql as mysql_module
from graphxr_database_proxy.drivers.relational.engines import (
    postgresql as postgresql_module,
)

ALL_ENGINES = ("mssql", "postgresql", "mysql")
ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = ROOT / "src"
SEAM_MODULES = (engine_module, mssql_module, postgresql_module, mysql_module)
DBAPI_NAMES = frozenset(
    {"pymssql", "psycopg", "psycopg2", "pymysql", "MySQLdb", "mysql.connector", "pyodbc"}
)


@dataclasses.dataclass
class Config:
    """A stand-in for ``DatabaseConfig``: the fields the seam actually reads."""

    host: Optional[str] = "db.example.com"
    port: Optional[int] = None
    database_id: Optional[str] = "warehouse"
    username: Optional[str] = "reader"
    password: Optional[str] = "hunter2-not-logged"
    use_tls: bool = False
    options: Dict[str, object] = dataclasses.field(default_factory=dict)
    schema_name: Optional[str] = None

class FakeConnection:
    """Records the statements an engine chose and hands back canned rows."""

    def __init__(self, rows: Sequence[Sequence[object]] = ()) -> None:
        self.rows = list(rows)
        self.statements: List[Tuple[str, Dict[str, object]]] = []

    def execute(
        self, statement: object, parameters: Optional[Dict[str, object]] = None
    ) -> Iterator[Sequence[object]]:
        self.statements.append((str(statement), dict(parameters or {})))
        return iter(self.rows)

    @property
    def sql(self) -> str:
        return " ;; ".join(sql for sql, _ in self.statements)

def engine(name: str) -> EngineSpelling:
    return engines_for(name)

def test_registry_answers_each_database_type_and_refuses_any_other() -> None:
    assert registered_engines() == tuple(sorted(ALL_ENGINES))
    for name in ALL_ENGINES:
        assert engines_for(name).name == name
    # A name that is a real project type of this proxy but not a relational engine
    # must not reach a default: the client's tables already fall through to bolt and
    # that is the bug this registry exists to avoid repeating.
    for name in ("kuzu", "neo4j", "spanner", ""):
        with pytest.raises(UnknownEngine):
            engines_for(name)

def test_each_engine_surface_is_complete_and_sane() -> None:
    # An engine that failed to answer an abstract part of the contract could not be
    # instantiated at all, which is what makes ``engines.ENGINES`` the proof; what is
    # checked here is that the answers are in range.
    surface = ("url", "connect_args", "session_statements", "isolation", "normalise_type",
               "single_column_key_ok", "agg_cast", "probe_server", "approx_row_counts",
               "id_list_strategy", "identifier_ok", "data_api_name")
    for name in ALL_ENGINES:
        spelling = engine(name)
        assert spelling.default_port in (1433, 5432, 3306)
        assert spelling.dbapi and spelling.data_api_name()
        assert spelling.name_case_rule in ("fold-lower", "insensitive", "sensitive")
        assert isinstance(spelling.session_statements(), tuple)
        assert spelling.id_list_strategy(ServerInfo(engine=name, major=16), 0) in (
            ID_LIST_STRATEGIES
        )
        for part in surface:
            assert hasattr(spelling, part), (name, part)

def test_missing_dbapi_message_names_the_extra_to_install() -> None:
    assert "pymssql" in engine("mssql").missing_dbapi_message()
    assert "psycopg[binary]" in engine("postgresql").missing_dbapi_message()
    assert "PyMySQL" in engine("mysql").missing_dbapi_message()

@pytest.mark.parametrize(
    "name,expected_port", [("mssql", 1433), ("postgresql", 5432), ("mysql", 3306)]
)
def test_url_carries_host_port_database_and_user(name: str, expected_port: int) -> None:
    url = engine(name).url(Config())
    assert isinstance(url, URL)
    assert url.host == "db.example.com"
    assert url.port == expected_port
    assert url.database == "warehouse"
    assert url.username == "reader"
    assert url.drivername.startswith(name)
    # A stated port and database win over both defaults, on every engine.
    stated = engine(name).url(Config(host="10.0.0.9", port=6543, database_id="facts"))
    assert (stated.host, stated.port, stated.database) == ("10.0.0.9", 6543, "facts")

def test_a_real_database_config_drives_the_url_without_any_new_field() -> None:
    """The seam reads the pydantic ``DatabaseConfig`` as it stands today.

    ``schema_name`` lands with the model work; until then the schema is
    ``options["schema"]``, which :func:`configured_schema` already prefers over the
    engine default, so nothing here has to change when the field arrives. The spelling
    is chosen by the registry key, never by the config's own ``type``, which is what
    lets the last line below work at all.
    """
    from graphxr_database_proxy.models.project import DatabaseConfig, DatabaseType

    config = DatabaseConfig(
        type=DatabaseType.POSTGRESQL,
        host="pg.internal",
        port=5433,
        database_id="analytics",
        username="reader",
        password="sekret",
        use_tls=True,
        options={"schema": "sales", "statement_timeout_ms": 5000},
    )
    spelling = engines_for(DatabaseType.POSTGRESQL.value)
    url = spelling.url(config)
    assert (url.host, url.port, url.database) == ("pg.internal", 5433, "analytics")
    assert "sekret" not in repr(url) and "sekret" not in str(url)
    assert configured_schema(config, spelling.default_schema) == "sales"
    assert "-c statement_timeout=5000" in url.query["options"]

    shop = DatabaseConfig(type=DatabaseType.MYSQL, host="m", database_id="shop")
    assert engines_for(DatabaseType.MYSQL.value).url(shop).query["charset"] == "utf8mb4"
    # ``mssql`` is not a ``DatabaseType`` value yet; the registry keys on the string,
    # so the engine is ready the moment the enum value lands.
    assert engines_for("mssql").url(config).drivername == "mssql+pymssql"

def test_password_never_appears_in_a_rendering_of_the_url() -> None:
    secret = "hunter2-not-logged"
    for name in ALL_ENGINES:
        spelling = engine(name)
        url = spelling.url(Config(password=secret), password=secret)
        assert url.password == secret
        assert secret not in repr(url)
        assert secret not in str(url)
        assert secret not in str(url.query)
        # The connect args are what a pool lane logs on failure.
        assert secret not in repr(spelling.connect_args(Config(password=secret)))
        assert "password" not in spelling.connect_args(Config(password=secret))

def test_our_own_source_never_asks_for_the_unmasked_url() -> None:
    for module in (engine_module, mssql_module, postgresql_module, mysql_module):
        assert "hide_password=False" not in open(module.__file__, encoding="utf-8").read()

def test_options_are_the_escape_hatch_for_both_url_and_connect_args() -> None:
    config = Config(
        options={
            "url_query": {"ApplicationIntent": "ReadOnly", "tds_version": "8.0"},
            "connect_args": {"conn_properties": {"ENCRYPTION": "require"}},
        }
    )
    url = engine("mssql").url(config)
    assert url.query["ApplicationIntent"] == "ReadOnly"
    assert url.query["tds_version"] == "8.0"
    args = engine("mssql").connect_args(config)
    assert args["conn_properties"] == {"ENCRYPTION": "require"}
    # An option the engine does not know about still reaches the DBAPI untouched.
    assert engine("mysql").url(config).query["ApplicationIntent"] == "ReadOnly"

def test_tls_means_sslmode_on_postgres_and_a_dict_on_mysql_and_nothing_on_mssql() -> None:
    mssql_url = engine("mssql").url(Config(use_tls=True))
    # pymssql negotiates TLS through FreeTDS; inventing a keyword here would be a
    # checkbox that does nothing, so the absence is the tested behaviour.
    assert "encrypt" not in mssql_url.query
    assert "ssl" not in engine("mssql").connect_args(Config(use_tls=True))

    pg_url = engine("postgresql").url(Config(use_tls=True))
    assert pg_url.query["sslmode"] == "require"
    stated = engine("postgresql").url(Config(use_tls=True, options={"sslmode": "verify-full"}))
    assert stated.query["sslmode"] == "verify-full"

    mysql_args = engine("mysql").connect_args(Config(use_tls=True))
    assert isinstance(mysql_args["ssl"], dict)
    verified = engine("mysql").connect_args(
        Config(use_tls=True, options={"ssl_ca": "/etc/ssl/certs-ca.pem"})
    )
    assert verified["ssl"]["ca"] == "/etc/ssl/certs-ca.pem"
    assert verified["ssl"]["verify_cert"] is True

def test_mssql_named_instance_replaces_the_port() -> None:
    url = engine("mssql").url(Config(port=1433, options={"instance": "SQLEXPRESS"}))
    assert url.host == "db.example.com\\SQLEXPRESS"
    assert url.port is None

def test_mysql_charset_is_the_four_byte_one_by_default() -> None:
    assert engine("mysql").url(Config()).query["charset"] == "utf8mb4"
    assert engine("mysql").connect_args(Config())["charset"] == "utf8mb4"
    narrow = engine("mysql").connect_args(Config(options={"charset": "latin1"}))
    assert narrow["charset"] == "latin1"

def test_schema_name_field_beats_the_option_beats_the_engine_default() -> None:
    assert configured_schema(Config(), "dbo") == "dbo"
    assert configured_schema(Config(options={"schema": "sales"}), "dbo") == "sales"
    assert configured_schema(Config(schema_name="hr", options={"schema": "sales"}), "dbo") == "hr"
    assert engine("mssql").default_schema == "dbo"
    assert engine("postgresql").default_schema == "public"
    # On MySQL the database *is* the namespace, so there is no default to invent.
    assert engine("mysql").default_schema is None

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
    assert isinstance(spelling.column_type(None), sa.types.String)
    assert isinstance(spelling.column_type("bigint"), sa.types.Integer)
    # SQL Server compares nvarchar against varchar differently, so they must not bind
    # the same way; the other two engines have one text type and get one answer.
    mssql = engine("mssql")
    assert repr(mssql.column_type("varchar(50)")) != repr(mssql.column_type("nvarchar(50)"))

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
    rendered = str(
        sa.select(sa.cast(column, numeric)).compile(dialect=sa.dialects.mssql.dialect())
    )
    assert "CAST(n AS DECIMAL(38, 10))" in rendered, rendered
    # The policy name is not a dialect curiosity: the same parse renders on the other
    # two engines too, so a cast read off one engine's table cannot be nonsense there.
    for dialect in (sa.dialects.postgresql.dialect(), sa.dialects.mysql.dialect()):
        assert "DECIMAL(38, 10)" in str(sa.select(sa.cast(column, numeric)).compile(dialect=dialect))
    # An uncast avg is exactly what SQLAlchemy 2.1.3 emits, which is the hole the
    # policy fills.
    bare = str(sa.select(sa.func.avg(column)).compile(dialect=sa.dialects.mssql.dialect()))
    assert "avg(n)" in bare and "CAST" not in bare
    assert cast_sqlalchemy_type("NVARCHAR(450)") is None

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

def test_mssql_id_list_ladder_is_engine_and_compatibility_level_together() -> None:
    mssql = engine("mssql")
    server_2017 = ServerInfo(engine="mssql", major=14, minor=0, compat_level=140)
    server_2016_at_120 = ServerInfo(engine="mssql", major=13, minor=0, compat_level=120)
    server_2012 = ServerInfo(engine="mssql", major=11, minor=0, compat_level=100)

    assert mssql.id_list_strategy(server_2017, 2000) == "inline"
    assert mssql.id_list_strategy(server_2017, 2001) == "bulk"
    assert mssql.bulk_id_form(server_2017) == "openjson"
    # A modern engine with a database restored at an old compatibility level cannot
    # resolve OPENJSON, and 2 100 parameters is a hard ceiling, so the list chunks.
    assert mssql.id_list_strategy(server_2016_at_120, 5000) == "fanout"
    assert mssql.id_list_strategy(server_2012, 5000) == "fanout"
    assert mssql.bulk_id_form(server_2012) == "placeholders"
    assert mssql.fanout_chunk_size(server_2012) < 2100

def test_mysql_id_list_ladder_is_a_version_number() -> None:
    mysql = engine("mysql")
    old = ServerInfo(engine="mysql", major=8, minor=0, build=3)
    new = ServerInfo(engine="mysql", major=8, minor=0, build=35)
    assert mysql.id_list_strategy(old, 5000) == "fanout"
    assert mysql.id_list_strategy(new, 5000) == "bulk"
    assert mysql.bulk_id_form(new) == "json_table"
    assert mysql.id_list_strategy(new, 2000) == "inline"
    # MariaDB spells its versions differently and is not claimed.
    mariadb = ServerInfo(engine="mysql", major=11, minor=4, build=2, mariadb=True)
    assert mariadb.supports_json_table is False
    assert mariadb.supported is False
    assert "MariaDB" in mariadb.incompatibility

def test_postgres_has_no_parameter_ceiling() -> None:
    postgres = engine("postgresql")
    info = ServerInfo(engine="postgresql", major=16, minor=4)
    assert postgres.id_list_strategy(info, 100) == "inline"
    assert postgres.id_list_strategy(info, 50_000) == "bulk"
    assert postgres.bulk_id_form(info) == "any"
    # One array parameter carries any selection, so there is nothing to fan out.
    assert postgres.fanout_chunk_size(info) == 0

def test_mssql_session_statements_include_arithabort() -> None:
    statements = engine("mssql").session_statements()
    assert "SET ARITHABORT ON" in statements
    # Without QUOTED_IDENTIFIER every bracketed identifier we emit is a syntax error.
    assert "SET QUOTED_IDENTIFIER ON" in statements
    assert not any("ISOLATION" in statement for statement in statements)

def test_postgres_session_statements_make_the_transaction_read_only() -> None:
    assert engine("postgresql").session_statements() == ("SET default_transaction_read_only = on",)

def test_mysql_session_setup_rides_on_the_connect_args() -> None:
    mysql = engine("mysql")
    # PyMySQL sends these itself: ``charset`` is SET NAMES and ``init_command`` is the
    # guard, so a second copy in session_statements would only drift.
    assert mysql.session_statements() == ()
    args = mysql.connect_args(Config())
    assert args["init_command"] == f"SET SESSION max_execution_time = {mysql.timeout_ms(Config())}"
    assert mysql.timeout_hint(Config()) == "/*+ MAX_EXECUTION_TIME(120000) */"
    assert engine("mssql").timeout_hint(Config()) == ""
    assert engine("postgresql").timeout_hint(Config()) == ""
    assert mysql.timeout_hint(Config(options={"max_execution_time_ms": 0})) == ""
    assert mysql.read_only_statement() == "START TRANSACTION READ ONLY"
    assert engine("postgresql").read_only_statement() is None
    assert engine("mssql").read_only_statement() is None

def test_isolation_defaults_are_three_different_answers() -> None:
    # SQL Server: an analyst must not block someone editing records.
    assert engine("mssql").isolation(Config()) == "READ UNCOMMITTED"
    # PostgreSQL and MySQL: MVCC readers already never block writers, so the same
    # choice would buy dirty reads for nothing.
    assert engine("postgresql").isolation(Config()) is None
    assert engine("mysql").isolation(Config()) is None

def test_isolation_requests_are_honoured_or_refused_by_name() -> None:
    for name, asked in (
        ("mssql", "snapshot"),
        ("postgresql", "repeatable read"),
        ("mysql", "read uncommitted"),
    ):
        assert engine(name).isolation(Config(options={"isolation_level": asked})) == asked.upper()
    for name in ALL_ENGINES:
        with pytest.raises(ValueError):
            engine(name).isolation(Config(options={"isolation_level": "LINEARIZABLE"}))

def test_postgres_refuses_read_uncommitted_instead_of_answering_a_lie() -> None:
    with pytest.raises(ValueError) as refused:
        engine("postgresql").isolation(Config(options={"isolation_level": "READ_UNCOMMITTED"}))
    assert "READ COMMITTED" in str(refused.value)
    # MySQL genuinely has it, so the same request is not an error there.
    assert engine("mysql").isolation(Config(options={"isolation_level": "READ_UNCOMMITTED"}))

def test_mssql_probe_is_one_statement_and_reads_both_version_numbers() -> None:
    connection = FakeConnection([(14, 0, 3421, "14.0.3421", "Developer Edition", 140, 0)])
    info = engine("mssql").probe_server(connection)
    assert len(connection.statements) == 1
    assert "SERVERPROPERTY" in connection.sql and "compatibility_level" in connection.sql
    assert (info.major, info.build, info.compat_level) == (14, 3421, 140)
    assert info.release_name == "2017"
    assert info.supports_openjson and info.supported and info.engine == "mssql"

def test_mssql_probe_names_an_azure_server_and_a_read_only_database() -> None:
    connection = FakeConnection([(16, 1, 0, "16.0", "Azure Database", 150, 1)])
    info = engine("mssql").probe_server(connection)
    assert info.release_name == "Azure SQL"
    assert info.read_only_server is True

def test_old_mssql_server_is_refused_with_its_own_reason() -> None:
    info = ServerInfo(engine="mssql", major=10, minor=50, release_name="2008 / 2008 R2")
    assert info.supported is False
    assert info.supports_offset_fetch is False
    assert "2012" in info.incompatibility

def test_postgres_probe_reads_the_version_number_and_the_recovery_flag() -> None:
    connection = FakeConnection([("16.4", 160400, "PostgreSQL 16.4 on x86_64", False, True)])
    info = engine("postgresql").probe_server(connection)
    assert (info.major, info.minor) == (16, 4)
    assert info.release_name == "16.4"
    assert info.read_only_server is True
    assert info.supported
    assert "current_setting" in connection.sql and "pg_is_in_recovery()" in connection.sql

def test_mysql_probe_tells_mariadb_apart() -> None:
    mysql = engine("mysql")
    connection = FakeConnection([("8.0.35-0ubuntu0.22.04.1", "(MySQL Community)", 0, 0)])
    info = mysql.probe_server(connection)
    assert (info.major, info.minor, info.build) == (8, 0, 35)
    assert info.release_name == "8.0.35"
    assert info.supports_json_table and info.supported

    connection = FakeConnection([("11.4.2-MariaDB-1:11.4.2", "mariadb.org binary", 1, 0)])
    info = mysql.probe_server(connection)
    assert info.mariadb is True
    assert info.names_case_insensitive is True
    assert info.supports_json_table is False

def test_probe_without_a_row_says_so() -> None:
    with pytest.raises(ValueError):
        engine("mssql").probe_server(FakeConnection([]))

def test_a_probed_server_resolves_back_to_the_engine_that_probed_it() -> None:
    from graphxr_database_proxy.models.project import DatabaseType

    # The compiler reaches the spelling through ``engines_for(info.engine)``, so a probe
    # that answered "sql server" or "PostgreSQL" instead of the registry key would raise
    # there, far from the row that caused it. The registry key is also the enum value,
    # which is what lets ``/api/{database_type}/`` reach an engine without a mapping.
    enum_values = {member.value for member in DatabaseType}
    rows = {
        "mssql": [(15, 0, 4043, "15.0.4043", "Standard Edition", 150, 0)],
        "postgresql": [("16.4", 160400, "PostgreSQL 16.4 on x86_64", False, False)],
        "mysql": [("8.0.35", "(MySQL Community)", 0, 0)],
    }
    for name, row in rows.items():
        info = engine(name).probe_server(FakeConnection(row))
        assert engines_for(info.engine) is engine(name)
        assert name in enum_values

def test_approx_counts_use_statistics_and_never_count_rows() -> None:
    cases = {
        "mssql": ("sys.partitions",),
        "postgresql": ("pg_class", "reltuples"),
        "mysql": ("information_schema.TABLES", "TABLE_ROWS"),
    }
    for name, markers in cases.items():
        connection = FakeConnection([("Person", 1234)])
        counts = engine(name).approx_row_counts(connection, "dbo", ["Person", "Account"])
        assert counts == {"Person": 1234}
        for marker in markers:
            assert marker in connection.sql, (name, marker)
        assert "COUNT(*)" not in connection.sql.upper()
        assert "SELECT *" not in connection.sql

def test_approx_counts_are_paged_one_statement_per_page() -> None:
    # A throwaway instance: the registered spellings are process-wide singletons and
    # mutating one would leak into every later test.
    spelling = MSSQLEngine()
    spelling.names_page_size = 2
    connection = FakeConnection([("a", 1), ("b", 2), ("c", 3)])
    counts = spelling.approx_row_counts(connection, "dbo", ["a", "b", "c", "d", "e", "f"])
    assert len(connection.statements) == 3
    assert len(counts) == 3
    # One page of names never becomes a statement per name.
    assert engine("mssql").names_page_size == 500
    assert spelling.approx_row_counts(connection, "dbo", []) == {}
    assert len(connection.statements) == 3

def test_a_count_the_engine_does_not_know_is_absent_not_zero() -> None:
    # reltuples = -1 means "never counted", TABLE_ROWS NULL means "no estimate".
    assert engine("postgresql").approx_row_counts(
        FakeConnection([("t", -1), ("u", 9)]), "public", ["t", "u"]
    ) == {"u": 9}
    assert engine("mysql").approx_row_counts(
        FakeConnection([("t", None), ("u", 9)]), "shop", ["t", "u"]
    ) == {"u": 9}
    assert engine("mssql").approx_row_counts(FakeConnection(), "dbo", []) == {}

def test_postgres_counts_bind_one_array_and_mssql_binds_one_name_each() -> None:
    connection = FakeConnection([])
    engine("postgresql").approx_row_counts(connection, "public", ["a", "b", "c"])
    _sql, params = connection.statements[0]
    assert params["names"] == ["a", "b", "c"] and params["schema"] == "public"

    connection = FakeConnection([])
    engine("mssql").approx_row_counts(connection, None, ["a", "b", "c"])
    _sql, params = connection.statements[0]
    assert [params[key] for key in ("n0", "n1", "n2")] == ["a", "b", "c"]
    assert "schema" not in params
    # With no schema stated, MySQL scopes the read to the connected database.
    connection = FakeConnection([])
    engine("mysql").approx_row_counts(connection, None, ["a"])
    assert "DATABASE()" in connection.sql

@pytest.mark.parametrize("name", ALL_ENGINES)
def test_legal_and_illegal_names(name: str) -> None:
    spelling = engine(name)
    # A reserved word is not a problem, because every identifier is quoted.
    assert spelling.identifier_ok("Order") is True
    assert spelling.identifier_ok("order") is True
    assert spelling.identifier_ok("select") is True
    assert spelling.identifier_ok("person_id") is True
    # A sigil or a backtick in the middle is refused: the engines all allow one, but
    # a name that needs quoting is a name that will be read wrongly by whatever else
    # touches the mapping — and a backtick is MySQL's own delimiter.
    assert spelling.identifier_ok("amount$") is False
    assert spelling.identifier_ok("order`id") is False
    assert spelling.identifier_ok("sales.orders") is False
    assert spelling.identifier_ok("1sttable") is False
    assert spelling.identifier_ok("") is False
    assert spelling.identifier_ok("name with space") is False
    assert spelling.identifier_ok("café") is False

def test_length_caps_are_the_engines_own() -> None:
    assert engine("mssql").max_identifier_length == 128
    assert engine("postgresql").max_identifier_length == 63
    assert engine("mysql").max_identifier_length == 64
    for name, cap in (("mssql", 128), ("postgresql", 63), ("mysql", 64)):
        spelling = engine(name)
        assert spelling.identifier_ok("n" * cap) is True
        assert spelling.identifier_ok("n" * (cap + 1)) is False
        too_long = spelling.identifier_error("n" * (cap + 1))
        assert str(cap) in too_long, (name, too_long)
    # PostgreSQL truncates a long name on create instead of refusing it, so the
    # message has to name what the table will actually be called.
    truncated = engine("postgresql").identifier_error("n" * 70)
    assert "truncat" in truncated and "n" * 63 in truncated
    assert engine("mysql").identifier_error("1abc") == (
        "the name starts with a digit, which is legal only inside quotes"
    )

def test_mssql_names_that_mean_something_else_are_named_as_such() -> None:
    mssql = engine("mssql")
    assert mssql.identifier_ok("#work") is False
    assert "temp table" in mssql.identifier_error("#work")
    assert "temp table" in mssql.identifier_error("##work")
    assert "variable" in mssql.identifier_error("@cached")

def test_case_rules_are_stated_per_engine() -> None:
    assert engine("postgresql").name_case_rule == "fold-lower"
    assert engine("mssql").name_case_rule == "insensitive"
    assert engine("mysql").name_case_rule == "sensitive"

def test_the_optional_dbapis_are_really_absent_here() -> None:
    # The rest of this file's guarantees are only interesting if the extras are not
    # installed, which is the state a source checkout and the CI image are in.
    import importlib.util

    for name in sorted(DBAPI_NAMES - {"mysql.connector"}):
        assert importlib.util.find_spec(name) is None, name

def test_no_module_here_creates_an_engine_or_imports_a_dbapi() -> None:
    """Checked on the syntax tree, so the prose in a docstring cannot break it."""
    for module in SEAM_MODULES:
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        assert "create_engine" not in called, module.__name__
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not (imported & DBAPI_NAMES), (module.__name__, imported)

def test_importing_the_seam_needs_no_dbapi() -> None:
    script = textwrap.dedent(
        """
        import sys

        BLOCKED = {"pymssql", "psycopg", "psycopg2", "pymysql", "MySQLdb", "pyodbc"}

        class Guard:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BLOCKED:
                    raise AssertionError("the seam imported the optional DBAPI " + name)
                return None

        sys.meta_path.insert(0, Guard())

        from graphxr_database_proxy.drivers.relational import engine
        from graphxr_database_proxy.drivers.relational.engines import ENGINES

        class C:
            host = "db.internal"; port = None; database_id = "d"; username = "u"
            password = "sekret"; use_tls = True; options = {}; schema_name = None

        assert [s.name for s in ENGINES] == ["mssql", "postgresql", "mysql"]
        for spelling in ENGINES:
            url = spelling.url(C())
            assert "sekret" not in str(url) and "*" in str(url), str(url)
            assert "sekret" not in repr(spelling.connect_args(C()))
            spelling.session_statements(); spelling.isolation(C())
            spelling.probe_sql(); spelling.counts_statement("dbo", ["a", "b"])
            for declared in ("varchar(50)", "bigint", "int8", "mediumint", "widget", None):
                spelling.normalise_type(declared); spelling.column_type(declared)
        print("clean")
        """
    )
    # The parent environment is kept: the package's own import reads Path.home(), and a
    # stripped env would fail for reasons that have nothing to do with DBAPIs.
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(SRC_ROOT)},
    )
    assert "clean" in result.stdout, result.stdout + "\n" + result.stderr[-3000:]
