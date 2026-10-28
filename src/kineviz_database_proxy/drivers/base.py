"""
Base driver interface for database connections
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from ..models.project import Project ,DatabaseConfig, GraphSchema, QueryResponse, SchemaResponse, GraphSchemaResponse, SampleDataResponse
from ..services.graph_schema_cache import GRAPH_SCHEMA_CACHE, cache_key


class BaseDatabaseDriver(ABC):
    """Base class for database drivers"""

    def __init__(self, project: Project):
        self.project = project
        self.config = project.database_config
        self._connection = None
    
    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the database"""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the database"""
        pass
    
    @abstractmethod
    async def test_connection(self) -> bool:
        """Test if connection is working"""
        pass
    
    @abstractmethod
    async def execute_query(self, query: str, parameters: Dict[str, Any] = None) -> QueryResponse:
        """Execute a query"""
        pass
    
    @abstractmethod
    async def get_schema(self) -> SchemaResponse:
        """Get database schema"""
        pass
    
    @abstractmethod
    async def get_graph_schema(self) -> GraphSchemaResponse:
        """Get graph database schema"""
        pass
    
    async def get_graph_schema_cached(self, refresh: bool = False) -> GraphSchemaResponse:
        """
        ``get_graph_schema()``, served from the process-wide cache when it can be.

        Concrete for every driver on purpose: the probe is the expensive part of
        the graph endpoints on all of them, and a driver only lives for one
        request, so the cache cannot sit on the instance.

        A hit still hands the categories to the intent mixin. A ``primary-key``
        backend turns a node id into a predicate with them, and this fresh driver
        has never seen them; skipping that on a hit would make ``/expand``
        resolve nothing.

        Only a successful probe is stored. Caching a failure would hold a
        connection error over a database that has since come back.
        """
        key = cache_key(self.project)
        if not refresh:
            cached = GRAPH_SCHEMA_CACHE.get(key)
            if cached is not None:
                self._remember_categories(cached)
                return GraphSchemaResponse(success=True, data=cached)

        async with GRAPH_SCHEMA_CACHE.lock(key):
            # Whoever held the lock has just populated it; do not probe again.
            if not refresh:
                cached = GRAPH_SCHEMA_CACHE.get(key)
                if cached is not None:
                    self._remember_categories(cached)
                    return GraphSchemaResponse(success=True, data=cached)

            response = await self.get_graph_schema()
            if response.success and response.data is not None:
                GRAPH_SCHEMA_CACHE.put(key, response.data)
            return response

    def _remember_categories(self, schema: GraphSchema) -> None:
        """Hand a cached schema to the intent mixin, where the driver has one."""
        remember = getattr(self, "remember_graph_categories", None)
        if remember is None:
            return
        remember({category.name: category.model_dump() for category in schema.categories})

    @abstractmethod
    async def get_sample_data(self) -> SampleDataResponse:
        """Get sample data from database"""
        pass

    @abstractmethod
    def get_api_info(self, project_name: str) -> Dict[str, Any]:
        """Get API information for this database"""
        pass