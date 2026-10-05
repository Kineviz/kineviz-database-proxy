"""
Tests for the relational DatabaseType values.

The value string is the URL segment of /api/{database_type}/{project_name}/...
and, once the relational drivers land, the statement dialect's name too, so it
is checked as a wire contract rather than as a label.
"""

import pytest

from graphxr_database_proxy.drivers.factory import DriverFactory
from graphxr_database_proxy.drivers.mssql import MssqlDriver
from graphxr_database_proxy.drivers.mysql import MysqlDriver
from graphxr_database_proxy.drivers.postgresql import PostgresqlDriver
from graphxr_database_proxy.drivers.relational.driver import RelationalDriver
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


@pytest.mark.parametrize(
    "database_type,driver_class",
    [
        (DatabaseType.MSSQL, MssqlDriver),
        (DatabaseType.POSTGRESQL, PostgresqlDriver),
        (DatabaseType.MYSQL, MysqlDriver),
    ],
)
def test_the_three_sql_types_reach_the_relational_driver(database_type, driver_class):
    """
    A registered type answers with a driver, because an enum value alone is not a connection.

    The types exist from the packaging stage and the drivers from this one; nothing else on
    the factory proves the two met, and a project that saves but cannot be opened is the
    failure a person meets first.
    """
    project = Project(
        id="relational-{}".format(database_type.value),
        name="relational-{}".format(database_type.value),
        database_type=database_type,
        database_config=_make_config(type=database_type),
    )

    made = DriverFactory.create_driver(project)

    assert isinstance(made, driver_class)
    assert isinstance(made, RelationalDriver)
