"""GraphXR v2 files, native YAML compatibility and both HTTP download formats."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from graphxr_database_proxy.drivers.relational.mapping import from_document
from graphxr_database_proxy.drivers.relational.mapping_io import (
    MappingFileError,
    parse,
    to_json,
    to_yaml,
)
from graphxr_database_proxy.models.graph_mapping import GraphMapping
from tests.test_relational_endpoints import client, projects, server  # noqa: F401
from tests.test_relational_mapping_schemas import wired  # noqa: F401

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "pg18.graphxr-mapping.json"


@pytest.fixture
def source():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("encode", [json.dumps, yaml.safe_dump])
def test_pg18_v2_parses_as_json_and_yaml(source, encode):
    before = deepcopy(source)
    doc = parse(encode(source))
    assert source == before
    assert doc.version == 2 and doc.schema_name == "public"
    assert [node.label for node in doc.nodes] == ["Person", "Company", "City"]
    assert doc.nodes[0].properties["first name"] == "STRING"
    assert [edge.type for edge in doc.edges] == ["WORKS_AT", "KNOWS", "LOCATED_IN"]
    assert doc.edges[0].endpoints[0].src.column == "start_key"
    assert doc.edges[0].endpoints[0].dst.label == "Company"
    assert from_document(doc).valid
    assert GraphMapping.model_validate(source) == doc


def test_v2_round_trips_both_formats_and_keeps_property_aliases(source):
    source["nodes"][0]["properties"]["displayName"] = source["nodes"][0][
        "properties"
    ].pop("name")
    doc = parse(json.dumps(source))
    doc.nodes[0].notes = "Read-only 北京 sample"
    doc.edges[0].flip = True
    assert doc.nodes[0].property_columns == {"displayName": "name"}
    assert parse(to_json(doc)) == doc
    assert parse(to_yaml(doc)) == doc
    exported = json.loads(to_json(doc))
    assert exported["source"] == {"schema": "public"}
    assert exported["nodes"][0]["properties"]["displayName"] == {
        "column": "name",
        "type": "STRING",
    }
    assert exported["relationships"][0]["from"] == source["relationships"][0]["from"]
    assert "edges" not in exported and "schema" not in exported
    assert to_json(doc) == to_json(doc)


def test_json_export_preserves_polymorphic_pairs_and_editor_decisions(source):
    doc = GraphMapping.model_validate(source)
    doc.edges[0].endpoints.append(doc.edges[0].endpoints[0].model_copy(deep=True))
    doc.edges[0].endpoints[0].row_estimate = 0
    doc.edges[0].include = False
    doc.exclude = ["Company"]
    doc.tuning = {"auto_limit": 42}
    assert parse(to_json(doc)) == doc


def test_v1_yaml_remains_valid_and_can_be_exported_as_v2_json():
    doc = parse(
        "version: 1\nschema: dbo\nnodes: [{label: Person, key: id, properties: {name: nvarchar(50)}}]"
    )
    assert parse(to_yaml(doc)) == doc
    exported = parse(to_json(doc))
    assert exported.version == 2
    assert exported.nodes == doc.nodes and exported.schema_name == "dbo"


@pytest.mark.parametrize(
    "change,path",
    [
        ({"property_columns": 7}, "nodes[0].property_columns"),
        (
            {"properties": {"id": {"column": "age", "type": "INT64"}}},
            "nodes[0].properties.id.column",
        ),
    ],
)
def test_malformed_or_conflicting_column_mapping_is_refused(source, change, path):
    source["nodes"][0].update(change)
    with pytest.raises(MappingFileError) as caught:
        parse(json.dumps(source))
    assert path in [path for path, _ in caught.value.problems]


@pytest.mark.parametrize(
    "path,value,expected",
    [
        (("source", "typo"), True, "source.typo"),
        (
            ("nodes", 0, "properties", "name", "type"),
            None,
            "nodes[0].properties.name.type",
        ),
        (
            ("nodes", 0, "properties", "name", "column"),
            "",
            "nodes[0].properties.name.column",
        ),
        (("relationships", 0, "to"), None, "edges[0].endpoints[0].dst"),
    ],
)
def test_malformed_v2_reports_a_field_path(source, path, value, expected):
    target = source
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(MappingFileError) as caught:
        parse(json.dumps(source))
    assert expected in [path for path, _ in caught.value.problems]


@pytest.mark.parametrize("field", ["model", "json", "text", "yaml"])
def test_v2_save_and_both_downloads_share_one_document(
    client, projects, server, source, field
):
    value = source if field == "model" else json.dumps(source)
    saved = client.put("/api/relational/mapped/mapping", json={field: value})
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved"] and saved.json()["valid"]
    canonical = GraphMapping.model_validate(
        projects.by_name["mapped"].database_config.graph_mapping
    )
    for format, media, suffix in (
        ("json", "application/json", "json"),
        ("yaml", "text/yaml", "yml"),
    ):
        response = client.get(f"/api/relational/mapped/mapping?format={format}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(media)
        assert f"mapped.mapping.{suffix}" in response.headers["content-disposition"]
        assert parse(response.text) == canonical


def test_ambiguous_body_is_rejected_without_saving(client, projects, server, source):
    before = deepcopy(projects.by_name["mapped"].database_config.graph_mapping)
    response = client.put(
        "/api/relational/mapped/mapping", json={"model": source, "yaml": "version: 1"}
    )
    assert response.status_code == 422
    assert projects.by_name["mapped"].database_config.graph_mapping == before


def test_json_download_without_override_returns_404(client, projects, server):
    projects.by_name["mapped"].database_config.graph_mapping = None
    response = client.get("/api/relational/mapped/mapping?format=json")
    assert (
        response.status_code == 404
        and "has no saved mapping" in response.json()["detail"]
    )


async def test_v2_native_types_alias_filters_and_samples_execute_on_all_engines(wired):
    wired.driver.config.graph_mapping = {
        "version": 2,
        "source": {"schema": "live"},
        "nodes": [
            {
                "label": "Person",
                "table": "Person",
                "key": "id",
                "properties": {
                    "id": {"column": "id", "type": "INT64"},
                    "displayName": {"column": "name", "type": "STRING"},
                },
            }
        ],
        "relationships": [],
    }
    result = await wired.driver.execute_query(
        "MATCH (p:Person) WHERE p.id = 7 AND p.displayName = 'Current' RETURN p LIMIT 5"
    )
    assert result.success, result.error
    assert result.data.data.nodes[0].id == "Person:7"
    assert result.data.data.nodes[0].properties["displayName"] == "Current"
    assert "name" not in result.data.data.nodes[0].properties
    mapping = await wired.driver.effective_mapping()
    assert mapping.nodes["Person"].key_type == "int"
    assert mapping.nodes["Person"].props == {"displayName": "varchar(30)"}
    samples = await wired.driver.get_sample_data()
    assert samples.success, samples.error
    assert samples.data["Person"] == [{"id": 7, "displayName": "Current"}]
    assert wired.access.calls == [("live", ("Person",), 1)]


async def test_v2_relationship_aliases_and_endpoint_types_execute(wired):
    from sqlalchemy import inspect, text
    from graphxr_database_proxy.drivers.relational.catalog import Catalog, SchemaTable

    with wired.access.engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE live.Link (id int PRIMARY KEY, a int, b int, role varchar(30))"
            )
        )
        connection.execute(text("INSERT INTO live.Link VALUES (1, 7, 7, 'Engineer')"))

    async def catalog(schema=None, names=None, page_size=None):
        reflected = inspect(wired.access.engine).get_multi_columns(
            schema=schema, filter_names=names
        )
        return Catalog(
            schema=schema,
            tables=[
                SchemaTable(
                    name=name,
                    schema=schema,
                    columns={col["name"]: str(col["type"]) for col in rows},
                )
                for (_, name), rows in reflected.items()
            ],
        )

    wired.access.catalog = catalog
    wired.driver.config.graph_mapping = {
        "version": 2,
        "source": {"schema": "live"},
        "nodes": [
            {
                "label": "Person",
                "table": "Person",
                "key": "id",
                "properties": {
                    "id": {"column": "id", "type": "INT64"},
                },
            }
        ],
        "relationships": [
            {
                "type": "KNOWS",
                "table": "Link",
                "key": "id",
                "from": {"label": "Person", "column": "a"},
                "to": {"label": "Person", "column": "b"},
                "properties": {"job": {"column": "role", "type": "STRING"}},
            }
        ],
    }
    result = await wired.driver.execute_query(
        "MATCH (p:Person)-[r:KNOWS]->(q:Person) WHERE r.job = 'Engineer' RETURN p, r, q LIMIT 5"
    )
    assert result.success, result.error
    edge = result.data.data.relationships[0]
    assert edge.id == "KNOWS:1"
    assert edge.properties["job"] == "Engineer"
    assert edge.startNodeId == edge.endNodeId == "Person:7"
