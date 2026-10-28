"""Snowflake tables served as a graph through the relational mapping pipeline."""

from .graph_support import SnowflakeGraphIntents
from .relational.driver import RelationalDriver


class SnowflakeDriver(SnowflakeGraphIntents, RelationalDriver):
    database_type = "snowflake"
    engine_name = "snowflake"
