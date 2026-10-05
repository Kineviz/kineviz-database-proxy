"""
Tests for the relational DatabaseType values.

The value string is the URL segment of /api/{database_type}/{project_name}/...
and, once the relational drivers land, the statement dialect's name too, so it
is checked as a wire contract rather than as a label.
"""

import pytest

from graphxr_database_proxy.drivers.factory import DriverFactory
from graphxr_database_proxy.models.project import (
    AuthType,
    DatabaseConfig,
    DatabaseType,
    Project,
    ProjectCreate,
)
from graphxr_database_proxy.services.project_service import ProjectService


def _make_config(**overrides) -> DatabaseConfig:
    """A SQL Server config carrying the fields a relational project is reached by."""
    base = dict(
        type=DatabaseType.MSSQL,
        host="db.internal",
        port=1433,
        username="graphxr",
        password="s3cret",
        auth_type=AuthType.USERNAME_PASSWORD,
    )
    base.update(overrides)
    return DatabaseConfig(**base)


def test_mssql_value_is_the_url_segment():
    assert DatabaseType.MSSQL.value == "mssql"
    assert DatabaseType("mssql") is DatabaseType.MSSQL
    # str Enum: the value is what a route parameter compares against.
    assert DatabaseType.MSSQL == "mssql"
    # Distinct from MySQL, which is a different engine and a different dialect.
    assert DatabaseType.MSSQL is not DatabaseType.MYSQL
    assert DatabaseType.MSSQL in list(DatabaseType)


def test_config_round_trips_through_model_dump():
    config = _make_config()
    dumped = config.model_dump()

    assert DatabaseConfig.model_validate(dumped) == config
    assert DatabaseConfig.model_validate(dumped).model_dump() == dumped


async def test_mssql_project_persists_and_reloads(tmp_path):
    # A fresh service on tmp_path: config/projects.json is live local state with
    # real credentials in it, so nothing here may point at it.
    created = await ProjectService(config_dir=str(tmp_path)).create_project(
        ProjectCreate(
            name="mssql-store",
            database_type=DatabaseType.MSSQL,
            database_config=_make_config(),
        )
    )

    reloaded = await ProjectService(config_dir=str(tmp_path)).get_project(created.id)

    assert reloaded is not None
    assert reloaded.database_type is DatabaseType.MSSQL
    assert reloaded.database_config == created.database_config
    assert (tmp_path / "projects.json").exists()


def test_mssql_driver_is_not_registered_yet():
    """
    Tripwire: lane-D2 registers the relational driver for the three SQL types.

    When that lands, this expectation flips to "create_driver returns a driver".
    """
    project = Project(
        id="mssql-tripwire",
        name="mssql-tripwire",
        database_type=DatabaseType.MSSQL,
        database_config=_make_config(),
    )

    with pytest.raises(ValueError, match="Unsupported database type"):
        DriverFactory.create_driver(project)

    assert DatabaseType.MSSQL not in DriverFactory.get_supported_types()
