"""Check driver imports and SQL dialect loading inside the built Docker image.

This check opens no database connections and does not install embedded engines.
Run it through stdin: docker run --rm -i --entrypoint python IMAGE - < this_file.
"""

import importlib
import subprocess
import sys

from kineviz_database_proxy.contract.capabilities import assert_capabilities_consistent
from kineviz_database_proxy.drivers.embedded.runtime import WORKER_SCRIPT
from kineviz_database_proxy.drivers.factory import DriverFactory
from kineviz_database_proxy.drivers.relational.engine import engines_for
from kineviz_database_proxy.drivers.relational.pool import EnginePool
from kineviz_database_proxy.models.project import DatabaseConfig, Project


DEPENDENCIES = {
    "spanner": ("google.cloud.spanner", "google.auth", "google_auth_oauthlib"),
    "bigquery": ("google.cloud.bigquery", "google.cloud.resourcemanager_v3"),
    "neo4j": ("neo4j",),
    "memgraph": ("neo4j",),
    "rocketgraph": ("httpx",),
    "postgresql": ("psycopg", "psycopg_binary"),
    "mysql": ("pymysql",),
    "mssql": ("pymssql",),
    "snowflake": ("snowflake.connector", "snowflake.sqlalchemy"),
    "kuzu": (),
    "ladybug": (),
    "latticedb": (),
}


def main() -> None:
    subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    pool = EnginePool()
    try:
        supported = DriverFactory.get_supported_types()
        assert {kind.value for kind in supported} == set(DEPENDENCIES)
        for kind in supported:
            name = kind.value
            for module in DEPENDENCIES[name]:
                importlib.import_module(module)
            project = Project(
                id="driver-check-" + name,
                name="driver-check-" + name,
                database_type=kind,
                database_config=DatabaseConfig(
                    type=kind,
                    host="localhost",
                    database_id="driver_check",
                    project_id="driver-check",
                    username="driver_check",
                    password="unused-offline-check",
                ),
            )
            driver = DriverFactory.create_driver(project)
            assert_capabilities_consistent(driver.graph_capabilities)
            if name in {"mssql", "postgresql", "mysql", "snowflake"}:
                engine = pool.engine(project, engines_for(name))
                assert engine.dialect.dbapi is not None
                result = "DBAPI import and SQLAlchemy engine initialization"
            elif name in {"kuzu", "ladybug", "latticedb"}:
                assert WORKER_SCRIPT.is_file()
                result = "adapter and worker present; native runtime installed on demand"
            else:
                result = "client imports and driver initialization"
            print(f"PASS {name}: {result}", flush=True)
        print(f"PASS all {len(supported)} registered drivers", flush=True)
    finally:
        pool.dispose_all()


if __name__ == "__main__":
    main()
