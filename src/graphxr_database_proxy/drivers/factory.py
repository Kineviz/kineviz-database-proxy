"""
Driver factory for creating database drivers
"""

from typing import Dict, Type
from .base import BaseDatabaseDriver
from .bigquery import BigQueryDriver
from .spanner import SpannerDriver
from .rocketgraph import RocketGraphDriver
from .neo4j import Neo4jDriver
from .memgraph import MemgraphDriver
from .kuzu import KuzuDriver
from .ladybug import LadybugDriver
from .latticedb import LatticeDbDriver
from .mssql import MssqlDriver
from .mysql import MysqlDriver
from .postgresql import PostgresqlDriver
from ..models.project import Project, DatabaseConfig, DatabaseType


class DriverFactory:
    """Factory for creating database drivers"""

    _drivers: Dict[DatabaseType, Type[BaseDatabaseDriver]] = {
        DatabaseType.SPANNER: SpannerDriver,
        DatabaseType.BIGQUERY: BigQueryDriver,
        DatabaseType.ROCKETGRAPH: RocketGraphDriver,
        DatabaseType.NEO4J: Neo4jDriver,
        DatabaseType.MEMGRAPH: MemgraphDriver,
        DatabaseType.KUZU: KuzuDriver,
        DatabaseType.LADYBUG: LadybugDriver,
        DatabaseType.LATTICEDB: LatticeDbDriver,
        DatabaseType.MSSQL: MssqlDriver,
        DatabaseType.POSTGRESQL: PostgresqlDriver,
        DatabaseType.MYSQL: MysqlDriver,
    }
    
    @classmethod
    def create_driver(cls, project: Project) -> BaseDatabaseDriver:
        """Create a driver instance for the given database type"""
        config = project.database_config
        driver_class = cls._drivers.get(config.type)
        if not driver_class:
            raise ValueError(f"Unsupported database type: {config.type}")

        return driver_class(project)
    
    @classmethod
    def register_driver(cls, db_type: DatabaseType, driver_class: Type[BaseDatabaseDriver]) -> None:
        """Register a new driver type"""
        cls._drivers[db_type] = driver_class
    
    @classmethod
    def get_supported_types(cls) -> list[DatabaseType]:
        """Get list of supported database types"""
        return list(cls._drivers.keys())