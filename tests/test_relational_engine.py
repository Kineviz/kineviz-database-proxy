"""Offline tests for the relational engine seam: no DBAPI, no server, no network.

The engine modules are policy, not I/O, so every one of these runs against a
``DatabaseConfig`` stand-in and a connection double that records statements. What
they pin down are the answers the compiler and the pool lanes build on: where a
secret goes, when an id list stops fitting, and what a server that cannot be served
has to say about itself.

The seam is three subjects and the file followed: this one is the registry and the
connection (URL, connect args, session statements, isolation, and the guards that
none of it imports a DBAPI), ``test_relational_engine_types.py`` is the type
vocabulary and every cast an engine may answer with, and
``test_relational_engine_probe.py`` is the server probe, the row-count reads, the id
lists and the name rules. The stand-ins live here and the other two import them.
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
from sqlalchemy import URL

from kineviz_database_proxy.drivers.relational.engine import (
    ID_LIST_STRATEGIES,
    EngineSpelling,
    ServerInfo,
    UnknownEngine,
    configured_schema,
    engines_for,
    registered_engines,
)

ALL_ENGINES = ("mssql", "postgresql", "mysql")
ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = ROOT / "src"
SEAM_PACKAGE = SRC_ROOT / "kineviz_database_proxy" / "drivers" / "relational"
# Walked as source paths, not module objects: `engine.py` re-exports every name and
# defines nothing since its split, so an AST sweep of it alone reads an empty body. The
# list comes from the directory, so splitting the seam again cannot quietly drop a module
# out of the two guards below.
SEAM_FILES = sorted(SEAM_PACKAGE.glob("engine*.py")) + sorted(
    (SEAM_PACKAGE / "engines").glob("*.py")
)
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
    assert registered_engines() == tuple(sorted((*ALL_ENGINES, "snowflake")))
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
               "id_list_strategy", "json_document_type", "identifier_ok", "data_api_name")
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
    from kineviz_database_proxy.models.project import DatabaseConfig, DatabaseType

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
    for path in SEAM_FILES:
        assert "hide_password=False" not in path.read_text(encoding="utf-8"), path.name

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
    assert engine("postgresql").read_only_statement() == "SET TRANSACTION READ ONLY"
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

def test_engine_spellings_load_when_optional_dbapis_are_unavailable() -> None:
    """Installing an optional driver must not invalidate the offline suite."""
    code = textwrap.dedent('''
        import importlib.abc
        import sys

        class NoDbapi(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split('.')[0] in {
                    'pymssql', 'pyodbc', 'psycopg', 'psycopg2', 'pymysql', 'mysql', 'snowflake'
                }:
                    raise ModuleNotFoundError(fullname)

        sys.meta_path.insert(0, NoDbapi())
        from kineviz_database_proxy.drivers.relational.engine import engines_for
        for name in ('mssql', 'postgresql', 'mysql', 'snowflake'):
            assert engines_for(name).name == name
    ''')
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr

def test_no_module_here_creates_an_engine_or_imports_a_dbapi() -> None:
    """Checked on the syntax tree, so the prose in a docstring cannot break it."""
    for path in SEAM_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))
        }
        assert "create_engine" not in called, path.name
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not (imported & DBAPI_NAMES), (path.name, imported)

def test_importing_the_seam_needs_no_dbapi() -> None:
    script = textwrap.dedent(
        """
        import sys

        BLOCKED = {"pymssql", "psycopg", "psycopg2", "pymysql", "MySQLdb", "pyodbc", "snowflake"}

        class Guard:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BLOCKED:
                    raise AssertionError("the seam imported the optional DBAPI " + name)
                return None

        sys.meta_path.insert(0, Guard())

        from kineviz_database_proxy.drivers.relational import engine
        from kineviz_database_proxy.drivers.relational.engines import ENGINES

        class C:
            host = "db.internal"; port = None; database_id = "d"; username = "u"
            password = "sekret"; use_tls = True; options = {}; schema_name = None

        assert [s.name for s in ENGINES] == ["mssql", "postgresql", "mysql", "snowflake"]
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
