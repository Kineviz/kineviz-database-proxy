"""Integration tests for RocketGraphDriver (URL building, api info, schema/sample_data stubs)."""
import pytest

from kineviz_database_proxy.drivers.rocketgraph import RocketGraphDriver
from kineviz_database_proxy.models.project import (
    AuthType,
    DatabaseConfig,
    DatabaseType,
    OAuthConfig,
    Project,
)


def _make_project(**cfg_kwargs):
    base = dict(
        type=DatabaseType.ROCKETGRAPH,
        host="example.com",
        port=4368,
        graph_name="social",
        auth_type=AuthType.BEARER_TOKEN,
        oauth_config=OAuthConfig(token="tok"),
    )
    base.update(cfg_kwargs)
    return Project(
        id="p", name="proj", database_type=DatabaseType.ROCKETGRAPH,
        database_config=DatabaseConfig(**base),
    )


def test_default_standalone_base_url():
    project = _make_project()
    driver = RocketGraphDriver(project)
    assert driver._base_url == "http://example.com:4368/api/v1"


def test_plugin_mode_base_url():
    project = _make_project(deployment_mode="plugin", port=8080)
    driver = RocketGraphDriver(project)
    assert driver._base_url == "http://example.com:8080/api/xgt/v1"


def test_tls_uses_https():
    project = _make_project(use_tls=True)
    driver = RocketGraphDriver(project)
    assert driver._base_url.startswith("https://")


def test_custom_api_base_path():
    project = _make_project(api_base_path="/custom/path")
    driver = RocketGraphDriver(project)
    assert driver._base_url == "http://example.com:4368/custom/path"


def test_get_api_info_shape():
    project = _make_project()
    driver = RocketGraphDriver(project)
    info = driver.get_api_info("proj")
    assert info["type"] == "rocketgraph"
    assert info["api_urls"]["info"] == "/api/rocketgraph/proj"
    assert info["api_urls"]["query"] == "/api/rocketgraph/proj/query"
    assert info["api_urls"]["graphSchema"] == "/api/rocketgraph/proj/graphSchema"


@pytest.mark.asyncio
async def test_get_schema_returns_not_implemented():
    project = _make_project()
    driver = RocketGraphDriver(project)
    result = await driver.get_schema()
    assert result.success is False
    assert "not implemented" in result.error.lower()


@pytest.mark.asyncio
async def test_get_sample_data_returns_not_implemented():
    project = _make_project()
    driver = RocketGraphDriver(project)
    result = await driver.get_sample_data()
    assert result.success is False
