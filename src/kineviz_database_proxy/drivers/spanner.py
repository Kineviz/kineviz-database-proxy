# -*- coding: utf-8 -*-
"""
Google Cloud Spanner driver
"""

from hashlib import new
import os
import time
from typing import Any, Dict, List, Optional

from google.cloud import spanner
from google.cloud.spanner_v1 import Client, data_types
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from google.cloud import spanner
import google.oauth2.credentials
import re

from .base import BaseDatabaseDriver
from .graph_support import SpannerGraphIntents
from ..models.project import (
    Project, GraphSchema, GraphSchemaMap, QueryData, QueryResponse, 
    SchemaResponse, GraphSchemaResponse, SampleDataResponse, AuthType, 
    Category, Relationship, GraphData, Node, RelationshipData
)
from ..common.util import get_default_oauth_config, exists_oauth_config
from ..services.project_service import ProjectService

import json


class SpannerDriver(SpannerGraphIntents, BaseDatabaseDriver):
    """Google Cloud Spanner driver"""

    def __init__(self, project: Project):
        super().__init__(project)
        self.client: Optional[Client] = None
        self.instance = None
        self.database = None
    
    async def connect(self) -> None:
        """Establish connection to Spanner"""
        try:
            print(f"[INFO] Connecting to Spanner with auth type: {self.config.auth_type}")
            
            # Initialize client based on auth type
            if self.config.auth_type == AuthType.OAUTH2:
                print("[INFO] Using OAuth2 authentication")
                self.client = await self._get_oauth_client()
            elif self.config.auth_type == AuthType.SERVICE_ACCOUNT:
                print("[INFO] Using Service Account authentication")
                self.client = await self._get_service_account_client()
            elif self.config.auth_type == AuthType.GOOGLE_ADC:
                print("[INFO] Using Google Application Default Credentials (ADC) authentication")
                self.client = await self._get_adc_client()
            else:
                raise ValueError(f"Unsupported auth type: {self.config.auth_type}")
            
            print(f"[INFO] Project: {self.config.project_id}")
            print(f"[INFO] Instance: {self.config.instance_id}")
            print(f"[INFO] Database: {self.config.database_id}")
            
            # Get instance and database
            self.instance = self.client.instance(self.config.instance_id)
            self.database = self.instance.database(self.config.database_id)
            
            print("[OK] Spanner connection established")
            
        except Exception as e:
            print(f"[ERROR] Failed to connect to Spanner: {str(e)}")
            raise ConnectionError(f"Failed to connect to Spanner: {str(e)}")

    async def _refresh_oauth_token(self, credentials: google.oauth2.credentials.Credentials) -> google.oauth2.credentials.Credentials:
        """Refresh OAuth token"""
        try:
            print("[INFO] Refreshing OAuth token...")
            request = Request()
            credentials.refresh(request)
            
            # Update the project with new token information
            current_time = time.time()
            project_service = ProjectService()
            await project_service.update_project_token(
                project_id=self.project.id,
                token=credentials.token,
                last_refreshed=current_time,
                expires_in=getattr(credentials, 'expires_in', 3600)
            )
            
            # Update local config
            self.project.database_config.oauth_config.token = credentials.token
            self.project.database_config.oauth_config.last_refreshed = current_time
            self.config.oauth_config = self.project.database_config.oauth_config
            
            print("[OK] OAuth token refreshed successfully")
            return credentials
            
        except Exception as e:
            print(f"[ERROR] Failed to refresh OAuth token: {e}")
            raise e

    def get_token_status(self) -> Dict[str, Any]:
        """Get current token status information"""
        if not self.config.oauth_config:
            return {"status": "no_oauth_config"}
        
        oauth_info = self.config.oauth_config
        current_time = time.time()
        
        status = {
            "has_token": bool(oauth_info.token),
            "has_refresh_token": bool(oauth_info.refresh_token),
            "expires_in": oauth_info.expires_in,
            "last_refreshed": oauth_info.last_refreshed,
            "current_time": current_time
        }
        
        if oauth_info.last_refreshed and oauth_info.expires_in:
            time_since_refresh = current_time - oauth_info.last_refreshed
            time_until_expiry = oauth_info.expires_in - time_since_refresh
            status.update({
                "time_since_refresh": time_since_refresh,
                "time_until_expiry": time_until_expiry,
                "is_expired": time_until_expiry <= 0,
                "expires_soon": time_until_expiry <= 300  # 5 minutes
            })
        
        return status

    async def _get_oauth_client(self) -> Client:
        """Get Spanner client using OAuth2"""
        if not self.config.oauth_config or exists_oauth_config() is False:
            raise ValueError("OAuth config is required for OAuth2 authentication")
        
        if not self.config.oauth_config.client_id or not self.config.oauth_config.client_secret:
            default_oauth = get_default_oauth_config()
            #empty dict check
            if default_oauth == {}:
                raise ValueError("OAuth configuration file not found and client_id/client_secret not provided. Please ensure config/default.google.localhost.oauth.json exists or provide complete OAuth configuration.")
            self.config.oauth_config.client_id = default_oauth.get("client_id")
            self.config.oauth_config.client_secret = default_oauth.get("client_secret")

        oauth_info = self.config.oauth_config
        
        # print(f"[OAuth] Client ID: {oauth_info.client_id[:10]}..." if oauth_info.client_id else "None")
        # print(f"[OAuth] Token available: {'Yes' if oauth_info.token else 'No'}")
        # print(f"[OAuth] Refresh token available: {'Yes' if oauth_info.refresh_token else 'No'}")
        
        # Create credentials with proper scopes for Spanner
        credentials = google.oauth2.credentials.Credentials(
            token=oauth_info.token,
            refresh_token=oauth_info.refresh_token or None,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=oauth_info.client_id,
            client_secret=oauth_info.client_secret,
            scopes=[
                "https://www.googleapis.com/auth/cloudplatformprojects.readonly",
                "https://www.googleapis.com/auth/spanner.admin",
                "https://www.googleapis.com/auth/spanner.data",
                "https://www.googleapis.com/auth/userinfo.profile",
                "https://www.googleapis.com/auth/userinfo.email",
            ]
        )

        # Initialize last_refreshed if not set
        if oauth_info.last_refreshed is None:
            oauth_info.last_refreshed = time.time()
            # Save the initialized timestamp
            project_service = ProjectService()
            await project_service.update_project_token(
                project_id=self.project.id,
                token=oauth_info.token,
                last_refreshed=oauth_info.last_refreshed,
                expires_in=oauth_info.expires_in
            )

        # Check if token needs refresh (expires in 5 minutes or less)
        needs_refresh = False
        if oauth_info.expires_in and oauth_info.last_refreshed:
            time_since_refresh = time.time() - oauth_info.last_refreshed
            expires_soon = time_since_refresh >= (oauth_info.expires_in - 300)  # 5 minutes buffer
            needs_refresh = expires_soon
            
            if needs_refresh:
                print(f"[INFO] Token expires soon (refreshed {time_since_refresh:.0f}s ago, expires in {oauth_info.expires_in}s)")

        if needs_refresh and oauth_info.refresh_token:
            print("[INFO] Refreshing OAuth token...")
            credentials = await self._refresh_oauth_token(credentials)
        elif needs_refresh and not oauth_info.refresh_token:
            print("[WARN] Token expires soon but no refresh token available")

        return spanner.Client(project=self.config.project_id, credentials=credentials)
    
    async def _get_service_account_client(self) -> Client:
        """Get Spanner client using service account"""
        if not self.config.oauth_config:
            raise ValueError("OAuth config with service account data is required for service account authentication")
        
        # Check if we have service account fields in oauth_config
        sa_config = self.config.oauth_config
        if sa_config.type == "service_account" and sa_config.private_key and sa_config.client_email:
            # Create credentials from service account info in oauth_config
            service_account_info = {
                "type": sa_config.type,
                "project_id": sa_config.project_id or self.config.project_id,
                "private_key_id": sa_config.private_key_id,
                "private_key": sa_config.private_key,
                "client_email": sa_config.client_email,
                "client_id": sa_config.client_id,
                "auth_uri": sa_config.auth_uri or "https://accounts.google.com/o/oauth2/auth",
                "token_uri": sa_config.token_uri or "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": sa_config.auth_provider_x509_cert_url or "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url": sa_config.client_x509_cert_url
            }
            credentials = service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=[
                    "https://www.googleapis.com/auth/cloudplatformprojects.readonly",
                    "https://www.googleapis.com/auth/spanner.admin", 
                    "https://www.googleapis.com/auth/spanner.data"
                ]
            )
        else:
            raise ValueError("Service account information is incomplete in oauth_config")
        
        return spanner.Client(project=self.config.project_id, credentials=credentials)
    
    async def _get_adc_client(self) -> Client:
        """Get Spanner client using Application Default Credentials (ADC)"""
        # Use default ADC credentials
        credentials, project_id = google.auth.default(
            scopes=[
                "https://www.googleapis.com/auth/spanner.admin", 
                "https://www.googleapis.com/auth/spanner.data"
            ]
        )
        return spanner.Client(project=self.config.project_id, credentials=credentials)

    async def disconnect(self) -> None:
        """Close connection to Spanner"""
        # Spanner client doesn't need explicit disconnection
        self.client = None
        self.instance = None
        self.database = None
    
    async def test_connection(self) -> bool:
        """Test Spanner connection"""
        try:
            if not self.database:
                await self.connect()
            
            # Test database existence first
            if not self.database.exists():
                print(f"Database {self.config.database_id} does not exist")
                return False
            
            # Simple test query using snapshot for read-only operation
            with self.database.snapshot() as snapshot:
                results = snapshot.execute_sql("SELECT 1 as test_value")
                list(results)  # Consume results
            return True
        except Exception as e:
            print(f"Connection test failed: {e}")
            return False
    
    async def execute_query(self, query: str, parameters: Dict[str, Any] = None) -> QueryResponse:
        """Execute a Spanner query"""
        start_time = time.time()
        
        try:
            if not self.database:
                await self.connect()
            
            query = query.strip().rstrip(';')  # Clean up query
            # Check if it's a graph query or SQL query
            # Skip MATCH (n) RETURN n.propName LIMIT 25
            # Skip MATCH (n) RETURN count(*) LIMIT 23
            # Skip MATCH (n) RETURN SAFE_TO_JSON(*) LIMIT 23
            test_query = re.sub(r'SAFE_TO_JSON|TO_JSON', '', query, flags=re.IGNORECASE).strip()
            isGraphQuery = (re.search(r'MATCH', test_query, re.IGNORECASE) and 
                           not re.search(r'RETURN\s*[a-z0-9]+\.', test_query, re.IGNORECASE) and 
                           not re.search(r'RETURN\s*[a-z0-9]+\(.+\)', test_query, re.IGNORECASE))
            if isGraphQuery and self.config.graph_name:
                # MATCH (n)-[r:HasOyster]-(m) RETURN * LIMIT 25, split to three parts: MATCH, RETURN, LIMIT
                
                # Replace keywords with flags and split
                command_with_flags = query
                command_with_flags = re.sub(r'MATCH(\s+[a-z]+=)?', '_flag_', command_with_flags, flags=re.IGNORECASE)
                command_with_flags = re.sub(r'RETURN', '_flag_', command_with_flags, flags=re.IGNORECASE)
                command_with_flags = re.sub(r'LIMIT', '_flag_', command_with_flags, flags=re.IGNORECASE)
                command_parts = [part.strip() for part in command_with_flags.split('_flag_')]
                graph_namespace = command_parts[0] if command_parts[0] else f"GRAPH {self.config.graph_name}"
                limit = command_parts[3] if len(command_parts) > 3 and command_parts[3] else ""
                match_command = command_parts[1] if len(command_parts) > 1 else ""
                
                query = f"""
                {graph_namespace}
                MATCH __p={match_command}
                RETURN SAFE_TO_JSON(__p) as thePath  {f"LIMIT {limit}" if limit else "" }"""

                print(f"[DEBUG] Transformed graph query:\n{query}")
                query = query.strip().rstrip(';')  # Clean up query again

                # Auto append the graph namespace
                if self.config.graph_name and not re.search(r'^GRAPH', query, re.IGNORECASE) and re.search(r'^MATCH', query, re.IGNORECASE):
                    query = f"""
                    GRAPH {self.config.graph_name}
                    {query}
                    """

                 # Property Graph query
                results = self._execute_graph_query(query, parameters)
            else:
                # SQL query
                results = self._execute_sql_query(query, parameters)
            
            execution_time = time.time() - start_time
            
            return QueryResponse(
                success=True,
                data=results,
                execution_time=execution_time
            )
        
        except Exception as e:
            execution_time = time.time() - start_time
            return QueryResponse(
                success=False,
                error=str(e),
                execution_time=execution_time
            )
    
    def _execute_graph_query(self, query: str, parameters: Dict[str, Any] = None) -> QueryData:
        """Execute a Property Graph query"""
        with self.database.snapshot() as snapshot:
            results = snapshot.execute_sql(query, params=parameters or {})
            
            # Parse graph data similar to the Node.js implementation
            graph = {
                "nodes": {},
                "relationships": {}
            }
            
            if not results:
                return QueryData(
                    type="GRAPH",
                    data=GraphData(
                        nodes=[],
                        relationships=[]
                    )
                )
            
            for row in results:
                # Get the first value from the row (thePath data)
                the_path_data = row[0] if row and row[0] else row
                
     
                if not the_path_data:
                    continue

                if isinstance(the_path_data, list):
                    the_path_data = the_path_data[0]

                if isinstance(the_path_data, data_types.JsonObject) and hasattr(the_path_data, '_array_value'):
                    the_path_data = the_path_data._array_value

                if isinstance(the_path_data, str):
                    try:
                        the_path_data = json.loads(the_path_data)
                    except json.JSONDecodeError as err:
                        print(f"Invalid parseGraph thePathData: {err}")
                        the_path_data = []                
                        
                if not isinstance(the_path_data, list):
                    the_path_data=[the_path_data]
        
                for node_or_edge in the_path_data:
                    # Support schema-less graph where properties is directly in the nodeOrEdge object
                    # Use the last label as the main label for compatibility with schema-less graph
                    is_dynamic_label = node_or_edge.get("properties", {}).get("label")
                    labels = node_or_edge.get("labels", [])
                    last_label = is_dynamic_label or (labels[-1] if labels else "")

                    properties = node_or_edge.get("properties", {})
                    ## if schema-less, properties has properties field, merge it into properties, then remove properties field
                    if properties and isinstance(properties, dict) and "properties" in properties:
                        merged_properties = properties.get("properties", {})
                        properties.update(merged_properties)
                        del properties["properties"]

                    if node_or_edge.get("kind") == "node":
                        graph["nodes"][node_or_edge.get("identifier")] = Node(
                            id=node_or_edge.get("identifier"),
                            labels=[last_label] if last_label else [],
                            properties=properties
                        )
                    elif node_or_edge.get("kind") == "edge":
                        graph["relationships"][node_or_edge.get("identifier")] = RelationshipData(
                            id=node_or_edge.get("identifier"),
                            type=last_label,
                            startNodeId=node_or_edge.get("source_node_identifier"),
                            endNodeId=node_or_edge.get("destination_node_identifier"),
                            properties=properties
                        )
            
            return QueryData(
                type="GRAPH",
                data=GraphData(
                    nodes=list(graph["nodes"].values()),
                    relationships=list(graph["relationships"].values())
                )
            )

    def _execute_sql_query(self, query: str, parameters: Dict[str, Any] = None) -> QueryData:
        """Execute a SQL query"""
        with self.database.snapshot() as snapshot:
            results = snapshot.execute_sql(query, params=parameters or {})
            rows = []
            for row in results:
                row_dict = {}
                for i, field in enumerate(results.fields):
                    val = row[i]
                    # Convert Array to string
                    if isinstance(val, list):
                        val = ", ".join(str(item) for item in val)
                    row_dict[field.name] = val
                rows.append(row_dict)
            return QueryData(
                type="TABLE",
                data=rows
            )

    def _is_schema_less(self, meta_json) -> bool:
        """Check if the graph schema is schema-less (has dynamic properties)"""
        if not meta_json or (not meta_json.get("nodeTables") and not meta_json.get("edgeTables")):
            return False
        
        node_tables = meta_json.get("nodeTables", [])
        edge_tables = meta_json.get("edgeTables", [])
        
        has_dynamic_node_table = any(
            table.get("dynamicLabelExpr") or table.get("dynamicPropertyExpr") 
            for table in node_tables
        )
        
        has_dynamic_edge_table = any(
            table.get("dynamicLabelExpr") or table.get("dynamicPropertyExpr") 
            for table in edge_tables
        )
        
        return has_dynamic_node_table or has_dynamic_edge_table
    

    def _getSchemaForSchemaLessGraphs(self, definMata:GraphSchemaMap) -> GraphSchema:
        """Get schema for schema-less graphs"""
        try:
            # First query to get labels for nodes and relationships
            meta_query = f"""
            GRAPH {self.config.graph_name}
            MATCH (n) 
            RETURN DISTINCT ARRAY_TO_STRING(LABELS(n),",") AS name,"" as startCategory, "" as endCategory , "category" as type 
            LIMIT 1000
            UNION ALL
            MATCH (n)-[r]->(m)
            RETURN DISTINCT ARRAY_TO_STRING(LABELS(r),",") AS name ,  ARRAY_TO_STRING(LABELS(n),",") as startCategory, ARRAY_TO_STRING(LABELS(m),",") as endCategory ,"relationship" as type
            LIMIT 1000
            """
            
            rows = self._execute_sql_query(meta_query)
            
            meta = {
                "categories": {},
                "relationships": {}
            }
            
            # Process labels
            for row in rows.data:
                names = row["name"].split(",")
                row_type = row["type"]
                names = [name.strip() for name in names if name.strip()]
                defineCategoriesOrRels = definMata.categories if row_type == "category" else definMata.relationships
                ## find the category or relationship from names
                defineCategoryOrRel = next((cat for cat in defineCategoriesOrRels.values() if cat.name in names), None)

                for name in names:
                    if row_type == "category" and name not in meta["categories"]:
                        meta["categories"][name] = {
                            "name": name,
                            "props": [],
                            "keys": defineCategoryOrRel.keys if defineCategoryOrRel else [],
                            "keysTypes": defineCategoryOrRel.keysTypes if defineCategoryOrRel else {},
                            "propsTypes": {}
                        }
                    elif row_type == "relationship" and name not in meta["relationships"]:
                        start_categories = [cat.strip() for cat in row["startCategory"].split(",") if cat.strip()]
                        end_categories = [cat.strip() for cat in row["endCategory"].split(",") if cat.strip()]
                        common_categories = [cat for cat in start_categories if cat in end_categories]
                        
                        start_category = next((cat for cat in start_categories if cat not in common_categories), 
                                            start_categories[0] if start_categories else "")
                        end_category = next((cat for cat in end_categories if cat not in common_categories), 
                                          end_categories[0] if end_categories else "")
                        
                        meta["relationships"][name] = {
                            "name": name,
                            "props": [],
                            "keys": defineCategoryOrRel.keys if defineCategoryOrRel else [],
                            "keysTypes": defineCategoryOrRel.keysTypes if defineCategoryOrRel else {},
                            "propsTypes": {},
                            "startCategory": start_category,
                            "endCategory": end_category
                        }
            
            # Second query to get properties
            categories = list(meta["categories"].values())
            relationships = list(meta["relationships"].values())
            
            prop_queries = []
            
            # Add category property queries
            for category in categories:
                prop_queries.append(f"""
                MATCH (n:`{category["name"]}`) 
                RETURN SAFE_TO_JSON(n) as props, SAFE_TO_JSON(n) as startN, SAFE_TO_JSON(n) as endN 
                LIMIT 1
                """)
            
            # Add relationship property queries
            for relationship in relationships:
                prop_queries.append(f"""
                MATCH (n:`{relationship["startCategory"]}`)-[r:`{relationship["name"]}`]->(m:`{relationship["endCategory"]}`) 
                RETURN SAFE_TO_JSON(r) as props, SAFE_TO_JSON(n) as startN, SAFE_TO_JSON(m) as endN 
                LIMIT 1
                """)
            
            if prop_queries:
                prop_query = f"""
                GRAPH {self.config.graph_name}
                {" UNION ALL ".join(prop_queries)}
                """

            prop_rows = self._execute_sql_query(prop_query)

            # Process property results
            for row in prop_rows.data:
                props_data = row["props"]
                if isinstance(props_data, str):
                    data = json.loads(props_data)
                else:
                    # Handle JsonObject from Spanner
                    data = props_data
                
                if data.get("kind") == "node":
                    # Remove element_definition_name from categories if it exists
                    if data.get("element_definition_name") in meta["categories"]:
                        del meta["categories"][data["element_definition_name"]]
                    
                    # Get the category name (last label that's not element_definition_name)
                    labels = data.get("labels", [])
                    category = next((label for label in reversed(labels) 
                                    if label != data.get("element_definition_name")), None)
                    
                    if category and category in meta["categories"]:
                        properties = data.get("properties", {})
                        ## if schema-less, properties has properties field, merge it into properties, then remove properties field
                        if properties and isinstance(properties, dict) and "properties" in properties:
                            merged_properties = properties.get("properties", {})
                            properties.update(merged_properties)
                            del properties["properties"]

                        props_type_map = {}
                        for key, value in properties.items():
                            props_type_map[key] = type(value).__name__.upper()
                        
                        meta["categories"][category]["props"] = list(properties.keys())
                        meta["categories"][category]["propsTypes"] = props_type_map
                
                elif data.get("kind") == "edge":
                    start_data_raw = row["startN"]
                    end_data_raw = row["endN"]
                    
                    if isinstance(start_data_raw, str):
                        start_data = json.loads(start_data_raw)
                    else:
                        start_data = start_data_raw
                        
                    if isinstance(end_data_raw, str):
                        end_data = json.loads(end_data_raw)
                    else:
                        end_data = end_data_raw
                    
                    # Remove element_definition_name from relationships if it exists
                    if data.get("element_definition_name") in meta["relationships"]:
                        del meta["relationships"][data["element_definition_name"]]
                    
                    # Get relationship and category names
                    relationship_labels = data.get("labels", [])
                    relationship = next((label for label in reversed(relationship_labels) 
                                        if label != data.get("element_definition_name")), None)
                    
                    start_labels = start_data.get("labels", [])
                    start_category = next((label for label in reversed(start_labels) 
                                            if label != start_data.get("element_definition_name")), None)
                    
                    end_labels = end_data.get("labels", [])
                    end_category = next((label for label in reversed(end_labels) 
                                        if label != end_data.get("element_definition_name")), None)
                    
                    if relationship and relationship in meta["relationships"]:
                        properties = data.get("properties", {})
                        if properties and isinstance(properties, dict) and "properties" in properties:
                            merged_properties = properties.get("properties", {})
                            properties.update(merged_properties)
                            del properties["properties"]

                        props_type_map = {}
                        for key, value in properties.items():
                            props_type_map[key] = type(value).__name__.upper()
                        
                        meta["relationships"][relationship]["startCategory"] = start_category or ""
                        meta["relationships"][relationship]["endCategory"] = end_category or ""
                        meta["relationships"][relationship]["props"] = list(properties.keys())
                        meta["relationships"][relationship]["propsTypes"] = props_type_map
            
            return GraphSchema(
                categories=[Category(**cat) for cat in meta["categories"].values()],
                relationships=[Relationship(**rel) for rel in meta["relationships"].values()]
            )

        except Exception as e:
            print(f"Error getting schema for schema-less graphs: {e}")
            return GraphSchema(
                categories=[],
                relationships=[]
            )

    async def get_graph_schema(self) -> GraphSchemaResponse:
        """Get Spanner Property Graph schema"""
        try:
            start_time = time.time()
            if not self.database:
                await self.connect()
            
            # Get Property Graph information
            graph_schema_query = f"""
                SELECT
                PG.PROPERTY_GRAPH_NAME as graphDB, PG.PROPERTY_GRAPH_METADATA_JSON as metaJSON
                FROM
                INFORMATION_SCHEMA.PROPERTY_GRAPHS as PG
                WHERE PG.PROPERTY_GRAPH_NAME = "{self.config.graph_name}"
            """
            schema_results = self._execute_sql_query(graph_schema_query)
            meta = {
                "categories": {},
                "relationships": {}
            }
            if not schema_results or not schema_results.data or len(schema_results.data) == 0:
                return GraphSchemaResponse(
                    success=False,
                    error="No schema results found",
                    execution_time= time.time() - start_time
                )

            meta_json = schema_results.data[0].get("metaJSON")

            if not meta_json:
                return GraphSchemaResponse(
                    success=False,
                    error="No metadata found",
                    execution_time= time.time() - start_time
                )

            # Build property declarations map
            property_declarations_map = {}
            for prop_decl in meta_json.get("propertyDeclarations", []):
                property_declarations_map[prop_decl.get("name")] = prop_decl.get("type")
            
            # Build node table label map
            node_table_label_map = {}
            
            # Process nodes as categories
            for node_table in meta_json.get("nodeTables", []):
                category_name = node_table.get("labelNames", [])[0] if node_table.get("labelNames") else None
                if not category_name:
                    continue
                    
                props_types = {}
                for prop_def in node_table.get("propertyDefinitions", []):
                    prop_name = prop_def.get("propertyDeclarationName")
                    prop_type = (property_declarations_map.get(prop_name) or 
                                property_declarations_map.get(prop_def.get("valueExpressionSql")) or 
                                "string")
                    props_types[prop_name] = prop_type
                keys = node_table.get("keyColumns", [])
                keys_types = {key: props_types.get(key, "string") for key in keys}
                
                table_name = (node_table.get("baseTableName") or 
                            node_table.get("name") or 
                            category_name)
                node_table_label_map[table_name] = category_name
                
                meta["categories"][category_name] = {
                    "name": category_name,
                    "props": list(props_types.keys()),
                    "propsTypes": props_types or {},
                    "keys":  keys,
                    "keysTypes": keys_types
                }
            
            # Process edges as relationships
            for edge_table in meta_json.get("edgeTables", []):
                relationship_name = edge_table.get("labelNames", [])[0] if edge_table.get("labelNames") else None
                if not relationship_name:
                    continue
                    
                props_types = {}
                for prop_def in edge_table.get("propertyDefinitions", []):
                    prop_name = prop_def.get("propertyDeclarationName")
                    prop_type = (property_declarations_map.get(prop_name) or 
                                property_declarations_map.get(prop_def.get("valueExpressionSql")) or 
                                "string")
                    props_types[prop_name] = prop_type
                keys = edge_table.get("keyColumns", [])
                keys_types = {key: props_types.get(key, "string") for key in keys}

                source_node_table = edge_table.get("sourceNodeTable", {}).get("nodeTableName")
                dest_node_table = edge_table.get("destinationNodeTable", {}).get("nodeTableName")
                
                meta["relationships"][relationship_name] = {
                    "name": relationship_name,
                    "propsTypes": props_types or {},
                    "props": list(props_types.keys()),
                    "keys": keys,
                    "keysTypes": keys_types,
                    "startCategory": node_table_label_map.get(source_node_table, source_node_table),
                    "endCategory": node_table_label_map.get(dest_node_table, dest_node_table)
                }
            
            if self._is_schema_less(meta_json):
                # Convert dict to GraphSchemaMap before calling the method
                schema_map = GraphSchemaMap(
                    categories={name: Category(**cat_data) for name, cat_data in meta["categories"].items()},
                    relationships={name: Relationship(**rel_data) for name, rel_data in meta["relationships"].items()}
                )
                meta = self._getSchemaForSchemaLessGraphs(schema_map)
                return GraphSchemaResponse(
                    success=True,
                    data=meta,
                    execution_time= time.time() - start_time
                )

            return GraphSchemaResponse(
                success=True, 
                data=GraphSchema(
                    categories=[Category(**cat) for cat in meta["categories"].values()],
                    relationships=[Relationship(**rel) for rel in meta["relationships"].values()]
                ),
                execution_time= time.time() - start_time
            )
        
        except Exception as e:
            return GraphSchemaResponse(success=False, error=str(e))

    async def get_schema(self) -> SchemaResponse:
        """Get Spanner database schema"""
        try:
            if not self.database:
                await self.connect()
            
            # Get table information
            schema_query = """
                SELECT
                    table_name as tableName,
                    column_name as columnName,
                    spanner_type as spannerType
                FROM
                    INFORMATION_SCHEMA.COLUMNS
                WHERE
                    table_schema NOT IN ('INFORMATION_SCHEMA', 'SPANNER_SYS')
            """
            
            results = self._execute_sql_query(schema_query)

            schema = {}
            for row in results.data:
                table_name = row["tableName"]
                column_name = row["columnName"]
                column_type = row["spannerType"]

                if table_name not in schema:
                    schema[table_name] = {}

                schema[table_name][column_name] = column_type

            return SchemaResponse(success=True, data=schema)

        except Exception as e:
            return SchemaResponse(success=False, error=str(e))
        
    async def get_sample_data(self) -> SampleDataResponse:
        """Get sample data from Spanner database"""
        try:
            limit = 10  # Number of rows to sample from each table/graph
            if not self.database:
                await self.connect()
            
            sample_data = {
            }
            
            # Get list of tables
            tables_query = """
            SELECT
                table_name
            FROM
                INFORMATION_SCHEMA.TABLES
            WHERE
                table_type = 'BASE TABLE'
            """
            
            table_results = self._execute_sql_query(tables_query)

            def get_table_sample(table_name: str):
                """Get sample data for a single table"""
                try:
                    sample_query = f"SELECT * FROM `{table_name}` LIMIT {limit}"
                    sample_rows = self._execute_sql_query(sample_query)
                    return table_name, sample_rows.data
                    
                except Exception as table_error:
                    # Return empty list for tables that can't be queried
                    return table_name, []
                
            # Extract table names
            table_names = [row['table_name'] for row in table_results.data]

            # Execute queries for each table
            for table_name in table_names:
                table_name, sample_rows = get_table_sample(table_name)
                sample_data[table_name] = sample_rows
            
            return SampleDataResponse(success=True, data=sample_data)
        
        except Exception as e:
            return SampleDataResponse(success=False, error=str(e))

    def get_api_info(self, project_name: str) -> Dict[str, Any]:
        """Get API information for Spanner"""
        base_url = f"/api/spanner/{project_name}"
        return {
            "type": "spanner",
            "api_urls": {
                "info": base_url,
                "query": f"{base_url}/query",
                "schema": f"{base_url}/schema",
                "graphSchema": f"{base_url}/graphSchema",
                "sampleData": f"{base_url}/sampleData",
                "tokenStatus": f"{base_url}/token-status",
                "test": f"{base_url}/test"
            },
            "version": "1.0",
            "features": {
                "property_graph": True,
                "sql": True,
                "schema": True,
                "graph_schema": True,
                "sample_data": True,
                "token_management": True,
                "transactions": True
            }
        }