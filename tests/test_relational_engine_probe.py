"""The engine seam's server surface: what a probe learns, what a list fits, what a name may be.

Split out of ``test_relational_engine.py``, which keeps the registry and the
connection half and defines ``FakeConnection`` and the engine lookup imported here.
Three statements are pinned: the version probe is one round trip and reads the flags
that decide what this proxy can promise, an id list changes shape at the engine's own
ceiling and never at a guessed one, and a table that statistics have not counted is
absent rather than zero.
"""

from __future__ import annotations

import pytest

from graphxr_database_proxy.drivers.relational.engine import ServerInfo, engines_for
from graphxr_database_proxy.drivers.relational.engines.mssql import MSSQLEngine
from tests.test_relational_engine import ALL_ENGINES, FakeConnection, engine

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
