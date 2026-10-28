"""Tests for RocketGraph SchemaMapper."""
import pytest
from kineviz_database_proxy.drivers.rocketgraph import SchemaMapper
from kineviz_database_proxy.models.project import GraphSchema, Category, Relationship


def test_maps_simple_node_type():
    rg_schema = {
        "graph_name": "social",
        "graph_schema": {
            "node_types": [
                {
                    "type": "Person",
                    "key": "id",
                    "properties": [
                        {"name": "id", "type": "TEXT"},
                        {"name": "name", "type": "TEXT"},
                        {"name": "age", "type": "INT"},
                    ],
                }
            ],
            "edge_types": [],
        },
    }

    result = SchemaMapper.map(rg_schema)

    assert isinstance(result, GraphSchema)
    assert len(result.categories) == 1
    cat = result.categories[0]
    assert cat.name == "Person"
    assert cat.keys == ["id"]
    assert set(cat.props) == {"id", "name", "age"}
    assert cat.keysTypes == {"id": "TEXT"}
    assert cat.propsTypes == {"id": "TEXT", "name": "TEXT", "age": "INT"}


def test_maps_edge_type_with_source_target():
    rg_schema = {
        "graph_schema": {
            "node_types": [],
            "edge_types": [
                {
                    "type": "KNOWS",
                    "source": "Person",
                    "target": "Person",
                    "source_key": "id",
                    "target_key": "id",
                    "properties": [{"name": "since", "type": "DATE"}],
                }
            ],
        }
    }

    result = SchemaMapper.map(rg_schema)

    assert len(result.relationships) == 1
    rel = result.relationships[0]
    assert rel.name == "KNOWS"
    assert rel.startCategory == "Person"
    assert rel.endCategory == "Person"
    assert rel.props == ["since"]
    assert rel.propsTypes == {"since": "DATE"}
    assert "id" in rel.keys


def test_maps_edge_with_distinct_source_target_keys():
    rg_schema = {
        "graph_schema": {
            "node_types": [],
            "edge_types": [
                {
                    "type": "OWNS",
                    "source": "Person",
                    "target": "Car",
                    "source_key": "person_id",
                    "target_key": "vin",
                    "properties": [],
                }
            ],
        }
    }

    result = SchemaMapper.map(rg_schema)

    rel = result.relationships[0]
    assert set(rel.keys) == {"person_id", "vin"}


def test_handles_missing_properties_field():
    rg_schema = {
        "graph_schema": {
            "node_types": [{"type": "Foo", "key": "id"}],
            "edge_types": [],
        }
    }

    result = SchemaMapper.map(rg_schema)

    cat = result.categories[0]
    assert cat.name == "Foo"
    assert cat.props == []
    assert cat.propsTypes == {}


def test_handles_empty_schema():
    rg_schema = {"graph_schema": {"node_types": [], "edge_types": []}}
    result = SchemaMapper.map(rg_schema)
    assert result.categories == []
    assert result.relationships == []


def test_handles_top_level_node_types_format():
    """Some responses may put node_types/edge_types at top level."""
    rg_schema = {
        "node_types": [{"type": "X", "key": "id", "properties": [{"name": "id", "type": "TEXT"}]}],
        "edge_types": [],
    }
    result = SchemaMapper.map(rg_schema)
    assert len(result.categories) == 1
    assert result.categories[0].name == "X"


def test_skips_properties_without_name():
    """Properties missing the 'name' field are skipped rather than crashing."""
    rg_schema = {
        "graph_schema": {
            "node_types": [
                {
                    "type": "Foo",
                    "key": "id",
                    "properties": [
                        {"name": "id", "type": "TEXT"},
                        {"type": "TEXT"},  # missing name — skip
                    ],
                }
            ],
            "edge_types": [],
        }
    }

    result = SchemaMapper.map(rg_schema)
    cat = result.categories[0]
    assert cat.props == ["id"]
    assert cat.propsTypes == {"id": "TEXT"}
