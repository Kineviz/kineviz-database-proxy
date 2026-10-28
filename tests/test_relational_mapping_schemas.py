"""Schema-qualified mappings must read and query the same physical tables."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from kineviz_database_proxy.contract.intents import ExpandRequest
from kineviz_database_proxy.drivers.factory import DriverFactory
from kineviz_database_proxy.drivers.relational.catalog import Catalog, SchemaTable
from kineviz_database_proxy.drivers.relational.engine import ServerInfo
from kineviz_database_proxy.drivers.relational.mapping import from_document
from kineviz_database_proxy.drivers.relational.schemas import RelSchema
from kineviz_database_proxy.models.graph_mapping import GraphMapping
from kineviz_database_proxy.models.project import DatabaseType
from tests.test_relational_pool import project


BACKENDS = (DatabaseType.MSSQL, DatabaseType.POSTGRESQL, DatabaseType.MYSQL)


def node_document(label, schema=None, key="id", properties=None):
    return {
        "label": label,
        "table": "Person",
        "schema": schema,
        "key": key,
        "properties": properties or {"name": "varchar(30)"},
    }


class SchemaAccess:
    """Execute generated SELECTs against two attached SQLite schemas, offline."""

    schema = "live"

    def __init__(self):
        self.calls = []
        self.engine = create_engine(
            "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        with self.engine.begin() as conn:
            for schema, key, name in (("live", 7, "Current"), ("archive", "A:9", "Archived")):
                conn.execute(text("ATTACH DATABASE ':memory:' AS " + schema))
                declared = "int" if schema == "live" else "varchar(30)"
                conn.execute(text(
                    f"CREATE TABLE {schema}.Person "
                    f"(id {declared} PRIMARY KEY, name varchar(30))"
                ))
                conn.execute(
                    text(f"INSERT INTO {schema}.Person VALUES (:id, :name)"),
                    {"id": key, "name": name},
                )

    async def catalog(self, schema=None, names=None, page_size=None):
        schema = schema or self.schema
        self.calls.append((schema, tuple(names), page_size))
        declared = "int" if schema == "live" else "varchar(30)"
        return Catalog(
            schema=schema,
            tables=[
                SchemaTable(
                    name=name, schema=schema,
                    columns={"id": declared, "name": "varchar(30)"},
                )
                for name in names
            ],
        )

    async def probe(self):
        return ServerInfo(engine="mssql", version="16.0", major=16, minor=0, build=0)

    async def rows(self, statement, parameters=None):
        with self.engine.connect() as conn:
            result = conn.execute(statement, parameters or {}).mappings()
            return [dict(row) for row in result]


@pytest.fixture(params=BACKENDS)
def wired(request):
    document = {
        "nodes": [
            node_document("Current"),
            node_document("Archived", "archive"),
        ]
    }
    target = project(graph_mapping=document, type=request.param, schema_name="live")
    target.database_type = request.param
    driver = DriverFactory.create_driver(target)
    access = SchemaAccess()
    driver._access = access
    try:
        yield SimpleNamespace(driver=driver, access=access)
    finally:
        access.engine.dispose()


async def test_keys_use_the_types_from_their_own_schema(wired):
    mapping = await wired.driver.effective_mapping()
    assert mapping.valid, mapping.problems
    assert mapping.nodes["Current"].schema == "live"
    assert mapping.nodes["Current"].key_type == "int"
    assert mapping.nodes["Archived"].schema == "archive"
    assert mapping.nodes["Archived"].key_type == "varchar(30)"
    assert set((schema, names) for schema, names, _ in wired.access.calls) == {
        ("live", ("Person",)),
        ("archive", ("Person",)),
    }


async def test_graph_ids_round_trip_through_sql_in_each_schema(wired):
    for label, key in (("Current", 7), ("Archived", "A:9")):
        literal = str(key) if isinstance(key, int) else repr(key)
        reply = await wired.driver.execute_query(
            f"MATCH (n:{label}) WHERE n.id = {literal} RETURN n LIMIT 5"
        )
        assert reply.success, reply.error
        assert [node.id for node in reply.data.data.nodes] == [f"{label}:{key}"]
        assert reply.data.data.nodes[0].properties["name"] == (
            "Current" if label == "Current" else "Archived"
        )


async def test_schema_read_keeps_same_named_tables_separate_and_reuses_reflection(wired):
    reply = await wired.driver.get_schema()
    assert reply.success, reply.error
    assert reply.data == {
        "live.Person": {"id": "int", "name": "varchar(30)"},
        "archive.Person": {"id": "varchar(30)", "name": "varchar(30)"},
    }
    assert len(wired.access.calls) == 2


async def test_key_reads_page_within_each_schema_and_include_edge_keys(wired):
    mapping = await wired.driver.effective_mapping()
    current, archived = mapping.nodes.values()
    nodes = {
        f"Node{index}": replace(
            current if index % 2 else archived,
            label=f"Node{index}", table=f"Table{index}", key_type="STRING",
        )
        for index in range(7)
    }
    edge = RelSchema(type="LINK", table="Link", schema="archive", key="id", key_type="STRING")
    wired.driver.spelling.names_page_size = 2
    wired.access.calls.clear()
    resolved = await wired.driver.with_key_types(
        replace(mapping, nodes=nodes, edges={"LINK": edge})
    )
    assert resolved.edges["LINK"].key_type == "varchar(30)"
    assert all(
        node.key_type == ("int" if node.schema == "live" else "varchar(30)")
        for node in resolved.nodes.values()
    )
    assert all(
        len(names) <= 2 and size == len(names)
        for _, names, size in wired.access.calls
    )
    asked = {name for _, names, _ in wired.access.calls for name in names}
    assert asked == {"Link", *(f"Table{i}" for i in range(7))}


async def test_mapping_document_schema_overrides_connection_default(wired):
    document = wired.driver.override_document().model_dump(by_alias=True)
    document["schema"] = "archive"
    document["nodes"] = document["nodes"][:1]
    wired.driver.config.graph_mapping = document
    wired.driver._document = None
    mapping = await wired.driver.effective_mapping()
    assert mapping.nodes["Current"].schema == "archive"
    assert mapping.nodes["Current"].key_type == "varchar(30)"


async def test_unique_table_name_keeps_existing_schema_response_key(wired):
    document = wired.driver.override_document().model_dump(by_alias=True)
    document["nodes"] = document["nodes"][:1]
    wired.driver.config.graph_mapping = document
    wired.driver._document = None
    reply = await wired.driver.get_schema()
    assert reply.success, reply.error
    assert reply.data == {"Person": {"id": "int", "name": "varchar(30)"}}


def test_catalog_defaults_do_not_cross_schema_boundaries():
    catalog = GraphMapping.model_validate({
        "nodes": [
            node_document("LivePerson", "live"),
            node_document("OldPerson", "archive", "record_id", {"description": "text"}),
        ]
    })
    override = GraphMapping.model_validate({
        "nodes": [
            {"label": "Current", "table": "Person", "schema": "live"},
            {"label": "Archived", "table": "Person", "schema": "archive"},
        ]
    })
    mapping = from_document(override, catalog)
    assert mapping.valid, mapping.problems
    assert set(mapping.nodes) == {"Current", "Archived"}
    assert mapping.nodes["Current"].key == "id"
    assert mapping.nodes["Current"].props == {"name": "varchar(30)"}
    assert mapping.nodes["Archived"].key == "record_id"
    assert mapping.nodes["Archived"].props == {"description": "text"}


async def test_implicit_and_explicit_connection_schema_are_the_same_table(wired):
    document = wired.driver.override_document().model_dump(by_alias=True)
    document["nodes"][1]["schema"] = "live"
    wired.driver.config.graph_mapping = document
    wired.driver._document = None
    # No discovery can repair mapping one physical table as two labels.
    async def draft(schema=None):
        return None

    wired.driver.draft_document = draft
    mapping = await wired.driver.effective_mapping()
    assert not mapping.valid
    assert any(
        path == "nodes[1].table" and "already mapped" in message
        for path, message in mapping.problems
    )


async def test_a_failed_catalog_read_can_be_retried(wired):
    original = wired.access.catalog

    async def unavailable(**kwargs):
        raise ConnectionError("catalog unavailable")

    wired.access.catalog = unavailable
    mapping = await wired.driver.effective_mapping()
    assert mapping.nodes["Archived"].key_type == "STRING"
    wired.access.catalog = original
    reply = await wired.driver.get_schema()
    assert reply.success, reply.error
    assert reply.data["archive.Person"]["id"] == "varchar(30)"
    assert len(wired.access.calls) == 2


async def test_expand_across_schemas_round_trips_both_ends(wired):
    with wired.access.engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE live.Link "
            "(id int PRIMARY KEY, current_id int, archive_id varchar(30))"
        ))
        conn.execute(text("INSERT INTO live.Link VALUES (11, 7, 'A:9')"))
    document = wired.driver.override_document().model_dump(by_alias=True)
    document["edges"] = [{
        "type": "ARCHIVED_AS", "table": "Link", "key": "id",
        "endpoints": [{
            "src": {"label": "Current", "column": "current_id", "column_type": "int"},
            "dst": {"label": "Archived", "column": "archive_id", "column_type": "varchar(30)"},
        }],
    }]
    wired.driver.config.graph_mapping = document
    wired.driver._document = None
    for seed in ("Current:7", "Archived:A:9"):
        result = await wired.driver.expand(
            ExpandRequest(nodeIds=[seed], direction="all", limit=10)
        )
        assert {node.id for node in result.data.nodes} == {"Current:7", "Archived:A:9"}
        assert len(result.data.relationships) == 1
        edge = result.data.relationships[0]
        assert edge.type == "ARCHIVED_AS"
        assert edge.startNodeId == "Current:7"
        assert edge.endNodeId == "Archived:A:9"
