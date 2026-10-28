"""
Data models for the Kineviz Database Proxy

These models define the structure of requests and responses for the API,
as documented in doc/API_Reference.md
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Literal, Union
from pydantic import BaseModel, Field
from enum import Enum


class DatabaseType(str, Enum):
    """
    Supported database types

    Values match the API endpoint paths (e.g., /api/spanner/{project_id}), and
    for a graph driver the value doubles as the statement dialect's name, so a
    value string is a wire contract rather than a display label.

    `postgresql`, `mysql`, `mssql` and `snowflake` are the relational family this round
    implements; `mongodb` is still an unimplemented placeholder.
    """
    SPANNER = "spanner"
    BIGQUERY = "bigquery"
    ROCKETGRAPH = "rocketgraph"
    NEO4J = "neo4j"
    MEMGRAPH = "memgraph"
    KUZU = "kuzu"
    LADYBUG = "ladybug"
    LATTICEDB = "latticedb"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MSSQL = "mssql"
    SNOWFLAKE = "snowflake"
    MONGODB = "mongodb"


class AuthType(str, Enum):
    """
    Authentication types supported by the proxy

    - OAUTH2: OAuth 2.0 token-based authentication
    - SERVICE_ACCOUNT: Google Cloud service account JSON key
    - USERNAME_PASSWORD: Traditional username/password authentication
    - GOOGLE_ADC: Google Application Default Credentials
    - BEARER_TOKEN: Pre-acquired static bearer token (e.g., MC session)
    """
    OAUTH2 = "oauth2"
    SERVICE_ACCOUNT = "service_account"
    USERNAME_PASSWORD = "username_password"
    GOOGLE_ADC = "google_ADC"
    BEARER_TOKEN = "bearer_token"


class OAuthConfig(BaseModel):
    """OAuth2 configuration"""
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    redirect_uri: str = "http://localhost:9080/google/spanner/callback"
    scopes: List[str] = Field(default_factory=list)
    
    # For OAuth2 token-based auth
    token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None
    last_refreshed: Optional[float] = None  # Unix timestamp of last token refresh
    token_uri: Optional[str] = None

    # For Service Account JSON key
    type: Optional[str] = None
    project_id: Optional[str] = None
    private_key_id: Optional[str] = None
    private_key: Optional[str] = None
    client_email: Optional[str] = None
    client_x509_cert_url: Optional[str] = None
    auth_uri: Optional[str] = None
    auth_provider_x509_cert_url: Optional[str] = None
    
    # Allow additional fields for flexibility
    class Config:
        extra = "allow"


class DatabaseConfig(BaseModel):
    """Database connection configuration"""
    # Common fields
    type: DatabaseType
    host: Optional[str] = None
    port: Optional[int] = None
    
    # Spanner / BigQuery specific
    # BigQuery reads database_id as the dataset and graph_name as the property
    # graph inside it; instance_id is unused there.
    project_id: Optional[str] = None
    instance_id: Optional[str] = None
    database_id: Optional[str] = None
    graph_name: Optional[str] = None

    # BigQuery specific: the dataset's processing location, e.g. "US" or "EU".
    location: Optional[str] = None
    
    # Authentication
    auth_type: AuthType = AuthType.USERNAME_PASSWORD
    username: Optional[str] = None
    password: Optional[str] = None
    oauth_config: Optional[OAuthConfig] = None
    service_account_path: Optional[str] = None  # For backward compatibility

    # RocketGraph specific
    use_tls: bool = False
    deployment_mode: Optional[str] = None  # "standalone" | "plugin"
    api_base_path: Optional[str] = None    # Custom API base path override

    # Kuzu / Ladybug / LatticeDB (embedded): a store is a local path, not a server,
    # so there is no host, port or credential here — the filesystem is the access
    # control.
    #   database_path  the store: a single file, or the directory Kuzu 0.10 and
    #                  older wrote. The header at offset 0 says which engine wrote
    #                  it and in which storage format, and that picks the engine
    #                  build; see drivers/embedded/store_probe.py.
    #   engine_version an optional pin. "0.19" means the newest 0.19.x; empty means
    #                  resolve it from the file.
    #   read_only      defaults true. The proxy is a read path, and a read-only open
    #                  is also what lets more than one process share the store.
    database_path: Optional[str] = None
    engine_version: Optional[str] = None
    read_only: bool = True

    # Neo4j / Memgraph add no fields of their own: they reuse host, port, use_tls,
    # username, password, and database_id (the Neo4j database to open; Memgraph has
    # no multi-database concept and ignores it). A host that already carries a bolt
    # URI scheme -- "neo4j+s://example.databases.neo4j.io" -- is used verbatim.

    # mssql / postgresql / mysql (relational):
    #   schema_name   the *relational* schema the tables live in -- dbo on SQL
    #                 Server, public on PostgreSQL, the database itself on MySQL.
    #                 Read it through ``EngineSpelling.configured_schema``, which
    #                 owns the precedence (this field, then ``options["schema"]``,
    #                 then the engine default), so nothing else has to know it.
    #   graph_mapping the override document from ``models/graph_mapping``, in the
    #                 JSON shape that document dumps to. It lives on the config
    #                 because ``graph_schema_cache.cache_key`` hashes the whole
    #                 config, so saving a mapping invalidates the cached graph
    #                 schema by itself and the save path needs no explicit bust.
    schema_name: Optional[str] = None
    graph_mapping: Optional[Dict[str, Any]] = None

    # Additional options
    options: Dict[str, Any] = Field(default_factory=dict)


class Project(BaseModel):
    """Project model"""
    id: str = Field(..., description="Unique project identifier")
    name: str = Field(..., description="Project name (should be unique)")
    database_type: DatabaseType = Field(..., description="Database type")
    database_config: DatabaseConfig = Field(..., description="Database configuration")
    create_time: datetime = Field(default_factory=datetime.utcnow)
    update_time: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ProjectCreate(BaseModel):
    """Project creation request"""
    name: str = Field(..., description="Project name")
    database_type: DatabaseType = Field(..., description="Database type")
    database_config: DatabaseConfig = Field(..., description="Database configuration")


class ProjectUpdate(BaseModel):
    """Project update request"""
    name: Optional[str] = None
    database_config: Optional[DatabaseConfig] = None


class APIInfo(BaseModel):
    """
    Database API information response
    
    Returns metadata about available API endpoints for a project.
    Corresponds to: GET /api/{database_type}/{project_id}
    
    Example:
        {
            "type": "spanner",
            "api_urls": {
                "info": "/api/spanner/my_project",
                "query": "/api/spanner/my_project/query",
                "graphSchema": "/api/spanner/my_project/graphSchema",
                "schema": "/api/spanner/my_project/schema"
            },
            "version": "1.0"
        }
    """
    type: DatabaseType = Field(..., description="Database type identifier")
    api_urls: Dict[str, str] = Field(..., description="Map of endpoint names to URLs")
    version: Optional[str] = Field(None, description="API version string")


class QueryRequest(BaseModel):
    """
    Database query request
    
    Request body for: POST /api/{database_type}/{project_id}/query
    
    Attributes:
        query: Cypher query string for graph databases
        parameters: Optional query parameters for parameterized queries
    
    Example:
        {
            "query": "MATCH (n)-[r]->(m) RETURN * LIMIT 1",
            "parameters": {}
        }
    """
    query: str = Field(..., description="Query string (Cypher for Spanner Graph)")
    parameters: Dict[str, Any] = Field(
        default_factory=dict, 
        description="Query parameters for parameterized queries"
    )


class Node(BaseModel):
    """
    Graph node/vertex representation
    
    Represents a single node in a graph query result.
    
    Attributes:
        id: Unique base64-encoded compound key identifier
        labels: List of node categories/labels
        properties: Node property key-value pairs
    """
    id: str = Field(..., description="Unique node identifier (base64-encoded)")
    labels: List[str] = Field(..., description="Node labels/categories")
    properties: Dict[str, Any] = Field(..., description="Node properties")


class RelationshipData(BaseModel):
    """
    Graph relationship/edge representation
    
    Represents a single relationship in a graph query result.
    
    Attributes:
        id: Unique base64-encoded compound key identifier
        type: Relationship type/name
        startNodeId: Source node identifier
        endNodeId: Target node identifier
        properties: Relationship property key-value pairs
    """
    id: str = Field(..., description="Unique relationship identifier (base64-encoded)")
    type: str = Field(..., description="Relationship type/name")
    startNodeId: str = Field(..., description="Source node ID")
    endNodeId: str = Field(..., description="Target node ID")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Relationship properties")


class GraphData(BaseModel):
    """
    Graph query result data

    Contains nodes and relationships returned by a graph query.
    Used when QueryData.type is "GRAPH".
    """
    nodes: List[Node] = Field(default_factory=list, description="Array of graph nodes")
    relationships: List[RelationshipData] = Field(
        default_factory=list,
        description="Array of graph relationships"
    )


class QueryData(BaseModel):
    """
    Query result data structure

    Container for query results, supporting both table and graph formats.

    Attributes:
        type: Result format - "TABLE" for tabular data, "GRAPH" for graph data
        data: Query results — GraphData for graph queries; for table queries
              either a 2D array (first row is the column headers) or a list
              of record dicts, depending on the driver.
        summary: Query execution metadata including version
    """
    type: Literal["TABLE", "GRAPH"] = Field(..., description="Result type indicator")
    data: Union[GraphData, List[List[Any]], List[Dict[str, Any]], None] = Field(
        None,
        description="Query results - GraphData, 2D array, or record array",
    )
    summary: Dict[str, str] = Field(
        default_factory=lambda: {"version": "4.0.1"}, 
        description="Query execution summary"
    )


class QueryResponse(BaseModel):
    """
    Database query response
    
    Response for: POST /api/{database_type}/{project_id}/query
    
    Attributes:
        success: True if query executed successfully
        data: Query results (null if error occurred)
        error: Error message (null if successful)
        execution_time: Query execution time in seconds
    
    Example:
        {
            "success": true,
            "data": {
                "type": "GRAPH",
                "data": { "nodes": [...], "relationships": [...] },
                "summary": { "version": "4.0.1" }
            },
            "error": null,
            "execution_time": 3.5043985843658447
        }
    """
    success: bool = Field(..., description="Query execution status")
    data: Optional[QueryData] = Field(None, description="Query results")
    error: Optional[str] = Field(None, description="Error message if failed")
    execution_time: Optional[float] = Field(None, description="Execution time in seconds")


class SchemaResponse(BaseModel):
    """
    Database table schema response
    
    Response for: GET /api/{database_type}/{project_id}/schema
    
    Returns the underlying database table schema with column definitions.
    
    Attributes:
        success: True if schema retrieval was successful
        data: Map of table names to column definitions (table_name -> column_name -> type)
        error: Error message if failed
    
    Example:
        {
            "success": true,
            "data": {
                "Client": {
                    "id": "STRING(36)",
                    "name": "STRING(255)",
                    "is_fraud": "BOOL"
                }
            },
            "error": null
        }
    """
    success: bool = Field(..., description="Schema retrieval status")
    data: Optional[Dict[str, Dict[str, str]]] = Field(
        None, 
        description="Table schemas: table_name -> column_name -> spanner_type"
    )
    error: Optional[str] = Field(None, description="Error message if failed")


class Category(BaseModel):
    """
    Graph node category/label definition
    
    Defines the schema for a node category including properties and keys.
    
    Attributes:
        name: Category/label name
        props: List of all property names
        keys: List of key property names (used in primary key)
        keysTypes: Data types for key properties
        propsTypes: Data types for all properties
    
    Example:
        {
            "name": "Client",
            "props": ["id", "name", "is_fraud"],
            "keys": ["id"],
            "keysTypes": { "id": "STRING" },
            "propsTypes": { 
                "id": "STRING", 
                "name": "STRING", 
                "is_fraud": "BOOL" 
            }
        }
    """
    name: str = Field(..., description="Category/label name")
    props: Optional[List[str]] = Field(None, description="List of property names")
    keys: Optional[List[str]] = Field(None, description="List of key property names")
    keysTypes: Optional[Dict[str, str]] = Field(
        None, 
        description="Data types for key properties (Spanner types)"
    )
    propsTypes: Optional[Dict[str, str]] = Field(
        None, 
        description="Data types for all properties (Spanner types)"
    )


class Relationship(BaseModel):
    """
    Graph relationship type definition
    
    Defines the schema for a relationship type including properties, keys,
    and connected node categories.
    
    Attributes:
        name: Relationship type name
        props: List of all property names
        keys: List of key property names
        keysTypes: Data types for key properties
        propsTypes: Data types for all properties
        startCategory: Source node category
        endCategory: Target node category
    
    Example:
        {
            "name": "HAS_EMAIL",
            "props": ["client_id", "email_id"],
            "keys": ["client_id", "email_id"],
            "keysTypes": { 
                "client_id": "STRING", 
                "email_id": "STRING" 
            },
            "propsTypes": { 
                "client_id": "STRING", 
                "email_id": "STRING" 
            },
            "startCategory": "Client",
            "endCategory": "Email"
        }
    """
    name: str = Field(..., description="Relationship type name")
    props: Optional[List[str]] = Field(None, description="List of property names")
    keys: Optional[List[str]] = Field(None, description="List of key property names")
    keysTypes: Optional[Dict[str, str]] = Field(
        None, 
        description="Data types for key properties (Spanner types)"
    )
    propsTypes: Optional[Dict[str, str]] = Field(
        None, 
        description="Data types for all properties (Spanner types)"
    )
    startCategory: str = Field(..., description="Source node category")
    endCategory: str = Field(..., description="Target node category")

class GraphSchema(BaseModel):
    """
    Complete graph schema definition
    
    Contains all node categories and relationship types in the graph.
    
    Attributes:
        categories: List of node category definitions
        relationships: List of relationship type definitions
    """
    categories: List[Category] = Field(
        default_factory=list, 
        description="Array of node category definitions"
    )
    relationships: List[Relationship] = Field(
        default_factory=list, 
        description="Array of relationship type definitions"
    )


class GraphSchemaMap(BaseModel):
    """
    Graph schema as dictionaries (alternative format)
    
    Same as GraphSchema but with categories and relationships as dictionaries
    keyed by name for faster lookup.
    """
    categories: Dict[str, Category] = Field(
        default_factory=dict, 
        description="Map of category name to definition"
    )
    relationships: Dict[str, Relationship] = Field(
        default_factory=dict, 
        description="Map of relationship name to definition"
    )


class GraphSchemaResponse(BaseModel):
    """
    Graph schema response
    
    Response for: GET /api/{database_type}/{project_id}/graphSchema
    
    Returns the complete graph schema including all node categories
    and relationship types.
    
    Attributes:
        success: True if schema retrieval was successful
        data: Graph schema with categories and relationships
        error: Error message if failed
    
    Example:
        {
            "success": true,
            "data": {
                "categories": [
                    {
                        "name": "Client",
                        "props": ["id", "name"],
                        "keys": ["id"],
                        "keysTypes": { "id": "STRING" },
                        "propsTypes": { "id": "STRING", "name": "STRING" }
                    }
                ],
                "relationships": [
                    {
                        "name": "HAS_EMAIL",
                        "props": ["client_id", "email_id"],
                        "startCategory": "Client",
                        "endCategory": "Email"
                    }
                ]
            },
            "error": null
        }
    """
    success: bool = Field(..., description="Schema retrieval status")
    data: GraphSchema = Field(
        default_factory=lambda: GraphSchema(categories=[], relationships=[]),
        description="Graph schema definition"
    )
    error: Optional[str] = Field(None, description="Error message if failed")


class SampleDataResponse(BaseModel):
    """
    Sample data response
    
    Used for retrieving sample data from the database.
    
    Attributes:
        success: True if data retrieval was successful
        data: Sample data records
        error: Error message if failed
    """
    success: bool = Field(..., description="Data retrieval status")
    data: Optional[Dict[str, Any]] = Field(None, description="Sample data")
    error: Optional[str] = Field(None, description="Error message if failed")
