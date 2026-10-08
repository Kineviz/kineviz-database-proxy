"""Offline Snowflake coverage: connection policy, physical names and graph SQL."""

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from graphxr_database_proxy.api.relational import _Page, _relational_driver, _tables_answer
from graphxr_database_proxy.drivers.factory import DriverFactory
from graphxr_database_proxy.drivers.relational import ids, mapping_io, plan, query
from graphxr_database_proxy.drivers.relational.compile import compile_statements
from graphxr_database_proxy.drivers.relational.engine import UnbindableType, engines_for
from graphxr_database_proxy.drivers.relational.schemas import NodeSchema
from graphxr_database_proxy.drivers.relational.snowflake_catalog import SnowflakeInspector
from graphxr_database_proxy.drivers.snowflake import SnowflakeDriver
from graphxr_database_proxy.models.project import DatabaseConfig, DatabaseType, Project
from tests.test_relational_compile import compile_one


def config(**values):
    return DatabaseConfig(type=DatabaseType.SNOWFLAKE, **{
        "host": "myorg-myaccount", "database_id": "ANALYTICS", "username": "reader",
        "password": "test-only@:/password", **values,
    })


def project(**values):
    return Project(id="snowflake-test", name="snowflake-test", database_type=DatabaseType.SNOWFLAKE,
                   database_config=config(**values))


def test_registered_driver_and_relational_admin_surface():
    driver = DriverFactory.create_driver(project())
    assert isinstance(driver, SnowflakeDriver)
    assert isinstance(_relational_driver(project()), SnowflakeDriver)
    assert driver.spelling is engines_for("snowflake")
    assert driver.graph_capabilities.identity.nodeId == "label-key"
    assert driver.graph_dialect.name == "snowflake"
    assert driver.graph_capabilities.intents == ["expand", "pullCategory", "pullRelationship"]


def test_connection_masks_password_and_keeps_namespace_options():
    engine = engines_for("snowflake")
    connection = config(host="myorg-myaccount.snowflakecomputing.com", schema_name="Sales Data",
                        options={"warehouse": "GRAPHXR_WH", "role": "GRAPHXR_READER", "query_timeout": 42})
    url = engine.url(connection)
    assert url.drivername == "snowflake"
    assert url.host == "myorg-myaccount"
    assert url.password == connection.password
    assert connection.password not in str(url)
    args = engine.connect_args(connection)
    assert connection.password not in repr(args)
    assert args["schema"] == "Sales Data"
    assert args["database"] == "ANALYTICS"
    assert args["warehouse"] == "GRAPHXR_WH"
    assert args["role"] == "GRAPHXR_READER"
    assert args["network_timeout"] == 42
    assert args["session_parameters"]["STATEMENT_TIMEOUT_IN_SECONDS"] == 42
    assert args["session_parameters"]["QUOTED_IDENTIFIERS_IGNORE_CASE"] is False
    assert engine.url(connection, password="replacement").password == "replacement"


@pytest.mark.parametrize("host", ["", "https://myorg-myaccount", "myaccount/path", "user@myaccount"])
def test_invalid_accounts_are_actionable(host):
    with pytest.raises(ValueError, match="account"):
        engines_for("snowflake").url(config(host=host))


@pytest.mark.parametrize("declared,expected", [
    ("NUMBER(38,0)", "INT64"), ("NUMBER(18,4)", "DECIMAL"), ("DECIMAL(38,0)", "INT64"),
    ("VARCHAR(200)", "STRING"), ("BOOLEAN", "BOOL"), ("BINARY", "BYTES"),
    ("FLOAT", "DOUBLE"), ("TIMESTAMP_NTZ", "TIMESTAMP"), ("TIMESTAMP_LTZ", "TIMESTAMP"),
    ("TIMESTAMP_TZ", "TIMESTAMP"), ("VARIANT", "JSON"), ("OBJECT", "JSON"), ("ARRAY", "JSON"),
])
def test_native_types(declared, expected):
    assert engines_for("snowflake").normalise_type(declared) == expected


def test_number_and_timestamp_keys_round_trip_without_precision_loss():
    number = "900719925474099312345678901234567890"
    assert ids.coerce_value(number, "NUMBER(38,0)") == int(number)
    assert ids.key_text(Decimal(number), "NUMBER(38,0)") == number
    assert ids.coerce_value("1.2300", "NUMBER(18,4)") == Decimal("1.2300")
    assert ids.coerce_value("2026-01-01T12:34:56", "TIMESTAMP_NTZ") == datetime(2026, 1, 1, 12, 34, 56)


def test_server_policy_and_constraint_warning():
    engine = engines_for("snowflake")
    server = engine.server_from_row(("10.4.2", "ANALYTICS", "PUBLIC", "GRAPHXR_WH", "GRAPHXR_READER"))
    assert server.supported
    assert server.release == (10, 4, 2)
    assert engine.read_only_statement() is None
    assert engine.isolation(config()) is None
    with pytest.raises(ValueError, match="isolation"):
        engine.isolation(config(options={"isolation_level": "SERIALIZABLE"}))
    for declared in ("VARIANT", "OBJECT", "ARRAY", "GEOGRAPHY"):
        assert not engine.single_column_key_ok(declared)
    assert "do not enforce" in engine.discovery_warnings[0]


class RecordingEngine:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    @contextmanager
    def connect(self):
        yield self

    def execute(self, statement, params):
        sql = str(statement)
        self.calls.append((sql, params))
        return SimpleNamespace(mappings=lambda: self.handler(sql, params))


def test_catalog_filters_and_pages_in_snowflake_preserving_case():
    def rows(sql, params):
        if "COUNT(*)" in sql:
            return [{"TOTAL": 231}]
        return [{"TABLE_NAME": "PERSON"}, {"TABLE_NAME": "Person"}]
    engine = RecordingEngine(rows)
    inspector = SnowflakeInspector(engine, "ANALYTICS")
    names, total = inspector.get_table_names_page("PUBLIC", 20, 2, "per%son")
    assert names == ["PERSON", "Person"]
    assert total == 231
    sql, params = engine.calls[-1]
    assert "LIMIT :limit OFFSET :offset" in sql
    assert params["limit"] == 2 and params["offset"] == 20
    assert params["search"] == "per%son" and "per%son" not in sql
    assert "TABLE_TYPE = 'BASE TABLE'" in sql
    assert inspector.get_table_names_page("INFORMATION_SCHEMA", 0, 20) == ([], 0)
    assert len(engine.calls) == 2


def test_columns_are_bounded_to_requested_tables_and_keep_physical_names():
    engine = RecordingEngine(lambda sql, params: [
        {"TABLE_NAME": "PERSON", "COLUMN_NAME": "ID", "DATA_TYPE": "NUMBER",
         "NUMERIC_PRECISION": 38, "NUMERIC_SCALE": 0, "CHARACTER_MAXIMUM_LENGTH": None, "IS_NULLABLE": "NO"},
        {"TABLE_NAME": "PERSON", "COLUMN_NAME": "Display Name", "DATA_TYPE": "TEXT",
         "NUMERIC_PRECISION": None, "NUMERIC_SCALE": None, "CHARACTER_MAXIMUM_LENGTH": 200, "IS_NULLABLE": "YES"},
    ])
    columns = SnowflakeInspector(engine, "ANALYTICS").get_multi_columns(["PERSON"], "PUBLIC")
    assert columns == {"PERSON": [
        {"name": "ID", "type": "DECIMAL(38,0)", "nullable": False},
        {"name": "Display Name", "type": "VARCHAR(200)", "nullable": True},
    ]}
    sql, params = engine.calls[0]
    assert "c.TABLE_NAME IN (:table0)" in sql
    assert params == {"schema": "PUBLIC", "table0": "PERSON"}


def test_constraint_columns_and_cross_database_foreign_keys_keep_identity():
    def rows(sql, params):
        if "IMPORTED" in sql:
            return [{"fk_name": "FK_COMPANY", "key_sequence": 1, "fk_column_name": "COMPANY_ID",
                     "pk_schema_name": "PUBLIC", "pk_database_name": "OTHER_DB", "pk_table_name": "COMPANY", "pk_column_name": "ID"}]
        return [{"constraint_name": "PK_PERSON", "key_sequence": 1, "column_name": "ID"}]
    engine = RecordingEngine(rows)
    inspector = SnowflakeInspector(engine, "ANALYTICS")
    assert inspector.get_multi_pk_constraint(['Person"Data'], "PUBLIC")['Person"Data']["constrained_columns"] == ["ID"]
    assert engine.calls[0][0] == 'SHOW PRIMARY KEYS IN TABLE "PUBLIC"."Person""Data"'
    assert inspector.get_multi_unique_constraints(["PERSON"], "PUBLIC")["PERSON"][0]["constrained_columns"] == ["ID"]
    fk = inspector.get_multi_foreign_keys(["PERSON"], "PUBLIC")["PERSON"][0]
    assert fk["referred_schema"] == "OTHER_DB.PUBLIC"
    assert fk["constrained_columns"] == ["COMPANY_ID"]
    assert fk["referred_columns"] == ["ID"]


async def test_table_picker_uses_source_page_without_loading_all_names():
    class Access:
        schema = "PUBLIC"

        async def names_page(self, **kwargs):
            assert kwargs == {"schema": None, "offset": 20, "limit": 10, "search": "person"}
            return [], 32

        async def names(self, **kwargs):
            pytest.fail("Snowflake picker must not fetch all names")

    answer = await _tables_answer(SimpleNamespace(access=Access()), _Page(page=3, page_size=10, search="person"), None, None, None)
    assert answer == {"rows": [], "total": 32, "page": 3, "page_size": 10}


def dialect():
    package = pytest.importorskip("snowflake.sqlalchemy", reason="Snowflake dialect extra is optional")
    return package.dialect()


def test_graph_join_and_paging_compile_with_the_real_snowflake_dialect():
    compiled = compile_one("MATCH (p:Person)-[r:EMPLOYMENT]->(o:Organization) RETURN p, r, o LIMIT 7",
                           engines_for("snowflake"), dialect())
    assert '"T_PERSON"' in compiled.sql and '"Employment"' in compiled.sql
    assert '"person_id"' in compiled.sql
    assert " JOIN " in compiled.sql and "LIMIT" in compiled.sql
    assert compiled.kind == "graph"
    assert len(compiled.manifest.vertices) == 2


def test_large_selection_is_chunked_and_numeric_binds_remain_exact():
    nodes = {"Person": NodeSchema(label="Person", table="PERSON", schema="PUBLIC", key="ID", key_type="NUMBER(38,0)", props={"ID": "NUMBER(38,0)"})}
    values = list(range(1201))
    prepared = query.prepare(f"MATCH (p:Person) WHERE p.ID IN {values} RETURN p LIMIT 10")
    branches = plan.concrete_plans(prepared.plan, nodes, {})
    statements = compile_statements(branches[0], nodes, {}, spelling=engines_for("snowflake"), spec=prepared.spec)
    assert len(statements) == 2
    assert sum(len(statement.parameters) for statement in statements) == 1201
    for statement in statements:
        sql = str(statement.select.compile(dialect=dialect()))
        assert '"PUBLIC"."PERSON"' in sql and '"ID"' in sql
    compiled = compile_one("MATCH (p:Person) WHERE p.ID = '9007199254740993' RETURN p", engines_for("snowflake"), dialect(), nodes=nodes)
    assert 9007199254740993 in compiled.parameters.values()


def test_mapping_json_yaml_and_semi_structured_projection():
    document = mapping_io.parse('''version: 1
schema: PUBLIC
nodes:
  - label: Person
    table: PERSON
    key: ID
    properties: {ID: "NUMBER(38,0)", DATA: VARIANT}
edges: []
''')
    restored = mapping_io.parse(mapping_io.to_json(document))
    assert restored.nodes[0].table == "PERSON"
    assert restored.nodes[0].key == "ID"
    assert set(restored.nodes[0].properties) == {"ID", "DATA"}
    assert mapping_io.parse(mapping_io.to_yaml(document)) == document
    nodes = {"Person": NodeSchema(label="Person", table="PERSON", key="ID", key_type="NUMBER(38,0)", props={"ID": "NUMBER(38,0)", "DATA": "VARIANT"})}
    compiled = compile_one("MATCH (p:Person) RETURN p", engines_for("snowflake"), dialect(), nodes=nodes)
    assert '"DATA"' in compiled.sql
    with pytest.raises(UnbindableType):
        compile_one("MATCH (p:Person) WHERE p.DATA = 'x' RETURN p", engines_for("snowflake"), dialect(), nodes=nodes)
