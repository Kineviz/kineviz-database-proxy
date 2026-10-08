# -*- coding: utf-8 -*-
"""
DatabaseProxy - High-level interface for Kineviz Database Proxy
"""

import asyncio
import uvicorn
import json
import uuid
import os
from typing import Dict, Any, Optional, List
from datetime import datetime
from pathlib import Path

from .main import app
from .models.project import Project, DatabaseConfig, DatabaseType, AuthType, OAuthConfig
from .services.project_service import ProjectService


class DatabaseProxy:
    """
    High-level interface for Kineviz Database Proxy
    
    This class provides a simplified Python API for configuring and running
    the Kineviz Database Proxy with Service Account authentication.
    """
    
    def __init__(self, config_dir: str = "config"):
        """
        Initialize DatabaseProxy
        
        Args:
            config_dir: Directory to store configuration files
        """
        self.project_service = ProjectService(config_dir)
        self.projects: Dict[str, Project] = {}
        self._app = app
        self._load_existing_projects()
    
    def _load_existing_projects(self) -> None:
        """Load existing projects from storage"""
        try:
            # This would load from the project service if needed
            pass
        except Exception as e:
            print(f"Warning: Could not load existing projects: {e}")
    
    def add_project(
        self,
        project_name: Optional[str] = None,
        database_type: str = "spanner",
        project_id: Optional[str] = None,
        instance_id: Optional[str] = None,
        database_id: Optional[str] = None,
        credentials: Optional[str] = None,
        graph_name: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Add a database project configuration
        
        Args:
            project_name: Name of the project (uses env var PROJECT_NAME if not provided)
            database_type: Database type ("spanner", "neo4j", "postgresql", etc.)
            project_id: Database project ID (for Spanner: GCP Project ID, uses env var SPANNER_PROJECT_ID if not provided)
            instance_id: Database instance ID (for Spanner: uses env var SPANNER_INSTANCE_ID if not provided)
            database_id: Database ID (for Spanner: uses env var SPANNER_DATABASE_ID if not provided)
            credentials: Authentication credentials - either:
                        - Path to credential file (e.g., "/path/to/service-account.json")
                        - Credential JSON string (e.g., '{"type": "service_account", ...}')
                        Uses env var SPANNER_CREDENTIALS_PATH if not provided
                        Or SPANNER_CREDENTIALS_JSON for JSON string
            graph_name: Optional graph name (uses env var SPANNER_GRAPH_NAME if not provided)
            **kwargs: Additional database-specific configuration parameters
        
        Environment Variables:
            PROJECT_NAME: Default project name
            SPANNER_PROJECT_ID: Default GCP project ID
            SPANNER_INSTANCE_ID: Default Spanner instance ID
            SPANNER_DATABASE_ID: Default Spanner database ID
            SPANNER_CREDENTIALS_PATH: Default path to service account JSON file
            SPANNER_CREDENTIALS_JSON: Default service account JSON string
            SPANNER_GRAPH_NAME: Default graph name
        
        Returns:
            Internal Project ID
        
        Example 1 (Spanner with file path):
            proxy.add_project(
                project_name="MySpannerProject",
                database_type="spanner",
                project_id="your-gcp-project-id",
                instance_id="your-spanner-instance-id",
                database_id="your-database-id",
                credentials="/path/to/your/service-account.json"
            )
            
        Example 2 (Spanner with JSON string):
            proxy.add_project(
                project_name="MySpannerProject",
                database_type="spanner",
                project_id="your-gcp-project-id",
                instance_id="your-spanner-instance-id",
                database_id="your-database-id",
                credentials='{"type": "service_account"}'
            )

        Example 3 (Spanner with Google Cloud ADC):
            proxy.add_project(
                project_name="MySpannerProject",
                database_type="spanner",
                project_id="your-gcp-project-id",
                instance_id="your-spanner-instance-id",
                database_id="your-database-id",
                credentials='{"type": "google_ADC"}'
            )
            
        Example 4 (Using environment variables):
            # Set environment variables first
            # os.environ['PROJECT_NAME'] = 'MySpannerProject'
            # os.environ['SPANNER_PROJECT_ID'] = 'your-gcp-project-id'
            # os.environ['SPANNER_INSTANCE_ID'] = 'your-spanner-instance-id'
            # os.environ['SPANNER_DATABASE_ID'] = 'your-database-id'
            # os.environ['SPANNER_CREDENTIALS_PATH'] = '/path/to/service-account.json'
            # os.environ['SPANNER_CREDENTIALS_JSON'] = '{"type": "google_ADC"}'
            # os.environ['SPANNER_GRAPH_NAME'] = 'graph'
            proxy.add_project()  # No parameters needed, all from environment variables
            
        Example 5 (Future: Neo4j):
            proxy.add_project(
                project_name="MyNeo4jProject",
                database_type="neo4j",
                credentials="neo4j://username:password@localhost:7687",
                graph_name="graph"
            )
        """
        # Use environment variables as defaults
        project_name = project_name or os.getenv('PROJECT_NAME')
        project_id = project_id or os.getenv('SPANNER_PROJECT_ID')
        instance_id = instance_id or os.getenv('SPANNER_INSTANCE_ID')
        database_id = database_id or os.getenv('SPANNER_DATABASE_ID')
        credentials = credentials or os.getenv('SPANNER_CREDENTIALS_PATH') 
        graph_name = graph_name or os.getenv('SPANNER_GRAPH_NAME')
        
        # If still no credentials from file path, try JSON string from environment
        if credentials is None:
            credentials_json_str = os.getenv('SPANNER_CREDENTIALS_JSON')
            if credentials_json_str:
                try:
                    # Convert JSON string to dict object
                    credentials = json.loads(credentials_json_str)
                except (TypeError, json.JSONDecodeError):
                    # If parsing fails, keep as string for later processing
                    credentials = credentials_json_str

        # Validate required parameters
        if not project_name:
            raise ValueError("project_name is required (either as parameter or PROJECT_NAME environment variable)")
        if not project_id:
            raise ValueError("project_id is required (either as parameter or SPANNER_PROJECT_ID environment variable)")
        
        # Handle different database types
        if database_type.lower() == "spanner":
            return self._add_spanner_project(
                project_name=project_name,
                project_id=project_id,
                instance_id=instance_id,
                database_id=database_id,
                credentials=credentials,
                graph_name=graph_name,
                **kwargs
            )
        else:
            # Future database types can be added here
            raise NotImplementedError(f"Database type '{database_type}' is not yet supported. Currently supported: spanner")
    
    def _add_spanner_project(
        self,
        project_name: str,
        project_id: Optional[str],
        instance_id: Optional[str],
        database_id: Optional[str],
        credentials: Optional[str],
        graph_name: Optional[str] = None,
        **kwargs
    ) -> str:
        """Internal method to add Spanner project configuration"""
        
        # Validate required Spanner parameters
        if not project_id:
            raise ValueError("project_id is required for Spanner projects")
        if not instance_id:
            raise ValueError("instance_id is required for Spanner projects (use SPANNER_INSTANCE_ID environment variable or parameter)")
        if not database_id:
            raise ValueError("database_id is required for Spanner projects (use SPANNER_DATABASE_ID environment variable or parameter)")
        if not credentials:
            raise ValueError("credentials is required for Spanner projects (use SPANNER_CREDENTIALS_PATH environment variable or parameter)")

        
        # Validate required parameters for Spanner
        if not all([project_id, instance_id, database_id, credentials]):
            raise ValueError("For Spanner projects, project_id, instance_id, database_id, and credentials are required")
        
        # Determine if credentials is a file path, JSON string, or dict object
        service_account_data = None
        credentials_source = None
        
        # Check if credentials is already a dict object
        if isinstance(credentials, dict):
            service_account_data = credentials
            credentials_source = "dict_object"
            print(f"✓ Using Service Account dict object")
        else:
            # Try to parse as JSON string first
            try:
                service_account_data = json.loads(credentials)
                credentials_source = "json_string"
                print(f"✓ Using Service Account JSON string")
            except (json.JSONDecodeError, TypeError):
                # Not JSON, treat as file path
                credentials_path = Path(credentials)
                if not credentials_path.exists():
                    raise FileNotFoundError(f"Service account file not found: {credentials}")
                
                try:
                    with open(credentials_path, 'r') as f:
                        service_account_data = json.load(f)
                    credentials_source = "file_path"
                    print(f"✓ Using Service Account file: {credentials_path.absolute()}")
                except json.JSONDecodeError:
                    raise ValueError(f"Invalid JSON in service account file: {credentials}")
        
        # Validate required fields based on credential type
        if service_account_data.get('type') == 'google_ADC':
            # ADC mode doesn't require private_key and client_email
            required_fields = ['type']
        else:
            # Service account mode requires all fields
            required_fields = ['type', 'project_id', 'private_key', 'client_email']
        
        for field in required_fields:
            if field not in service_account_data:
                raise ValueError(f"Missing required field in service account JSON: {field}")
        
        # Create OAuth config with service account data
        oauth_config = OAuthConfig(**service_account_data)
        
        # Determine auth_type based on credential type
        credential_type = service_account_data.get('type', 'service_account') 
        
        # Create database configuration
        database_config = DatabaseConfig(
            type=DatabaseType.SPANNER,
            project_id=project_id,
            instance_id=instance_id,
            database_id=database_id,
            graph_name=graph_name,
            auth_type=credential_type,
            oauth_config=oauth_config,
            service_account_path=credentials if credentials_source == "file_path" else None
        )
        
        # Generate unique project ID
        internal_project_id = str(uuid.uuid4())
        
        # Create project
        project = Project(
            id=internal_project_id,
            name=project_name,
            database_type=DatabaseType.SPANNER,
            database_config=database_config
        )
        
        # Store project
        self.projects[internal_project_id] = project
        
        # Save to persistent storage
        try:
            # Try to create task if there's a running event loop
            loop = asyncio.get_running_loop()
            loop.create_task(self._save_project_async(project))
        except RuntimeError:
            # No running event loop, run synchronously
            asyncio.run(self._save_project_async(project))
        
        print(f"[SUCCESS] Added Spanner project: {project_name}")
        print(f"   GCP Project ID: {project_id}")
        print(f"   Instance ID: {instance_id}")
        print(f"   Database ID: {database_id}")
        print(f"   Graph Name: {graph_name or 'default'}")
        print(f"   Internal ID: {internal_project_id}")
        
        return internal_project_id
    
    def add_database(
        self,
        project_name: str,
        project_id: str,
        instance_id: str,
        database_id: str,
        credentials: str,
        graph_name: Optional[str] = None
    ) -> str:
        """
        Add a Spanner database configuration (deprecated, use add_project instead)
        
        This method is kept for backward compatibility. Please use add_project() instead.
        
        Args:
            project_name: Name of the project
            project_id: Google Cloud Project ID
            instance_id: Spanner Instance ID
            database_id: Spanner Database ID
            credentials: Service Account credentials
            graph_name: Optional graph name
        
        Returns:
            Internal Project ID
        """
        import warnings
        warnings.warn(
            "add_database() is deprecated and will be removed in a future version. "
            "Please use add_project() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        
        return self.add_project(
            project_name=project_name,
            database_type="spanner",
            project_id=project_id,
            instance_id=instance_id,
            database_id=database_id,
            credentials=credentials,
            graph_name=graph_name
        )
    
    async def _save_project_async(self, project: Project) -> None:
        """Save project to persistent storage asynchronously"""
        try:
            from .models.project import ProjectCreate
            project_create = ProjectCreate(
                name=project.name,
                database_type=project.database_type,
                database_config=project.database_config
            )
            await self.project_service.create_project(project_create)
        except Exception as e:
            print(f"Warning: Could not save project to persistent storage: {e}")
    
    def remove_project(self, project_id: str) -> bool:
        """
        Remove a project configuration
        
        Args:
            project_id: ID of the project to remove
        
        Returns:
            True if removed successfully, False if not found
        """
        if project_id in self.projects:
            project = self.projects[project_id]
            del self.projects[project_id]
            print(f"[SUCCESS] Removed project: {project.name}")
            return True
        print(f"[ERROR] Project not found: {project_id}")
        return False
    
    def remove_database(self, project_id: str) -> bool:
        """
        Remove a database configuration (deprecated, use remove_project instead)
        
        This method is kept for backward compatibility. Please use remove_project() instead.
        """
        import warnings
        warnings.warn(
            "remove_database() is deprecated and will be removed in a future version. "
            "Please use remove_project() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        return self.remove_project(project_id)
    
    def list_projects(self) -> Dict[str, Dict[str, Any]]:
        """
        List all configured projects
        
        Returns:
            Dictionary mapping project IDs to project information
        """
        return {
            project_id: {
                "name": project.name,
                "database_type": project.database_type.value,
                "project_id": project.database_config.project_id,
                "instance_id": project.database_config.instance_id,
                "database_id": project.database_config.database_id,
                "graph_name": project.database_config.graph_name,
                "auth_type": project.database_config.auth_type.value,
                "created_at": project.create_time.isoformat(),
                "api_endpoint": f"/api/projects/{project_id}"
            }
            for project_id, project in self.projects.items()
        }
    
    def list_databases(self) -> Dict[str, Dict[str, Any]]:
        """
        List all configured databases (deprecated, use list_projects instead)
        
        This method is kept for backward compatibility. Please use list_projects() instead.
        """
        import warnings
        warnings.warn(
            "list_databases() is deprecated and will be removed in a future version. "
            "Please use list_projects() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        return self.list_projects()
    
    def _find_project_by_name(self, project_name: str) -> Optional[str]:
        """
        Find project ID by project name
        
        Args:
            project_name: Name of the project to find
            
        Returns:
            Project ID if found, None otherwise
        """
        for project_id, project in self.projects.items():
            if project.name == project_name:
                return project_id
        return None
    
    def get_project_apis(self, project_identifier: Optional[str] = None) -> Dict[str, Any]:
        """
        Get API endpoints for projects
        
        Args:
            project_identifier: Project ID or project name, or None for all projects
        
        Returns:
            Dictionary with API endpoint information
        """
        if project_identifier:
            # First try to find by project_id
            project_id = project_identifier
            
            # If not found by ID, try to find by name
            if project_id not in self.projects:
                found_id = self._find_project_by_name(project_identifier)
                if found_id:
                    project_id = found_id
                else:
                    return {"error": f"Project not found by ID or name: {project_identifier}"}
            
            project = self.projects[project_id]
            return {
                "project_id": project_id,
                "name": project.name,
                "endpoints": {
                    "base": f"/api/projects/{project_id}",
                    "query": f"/api/projects/{project_id}/query",
                    "graphSchema": f"/api/projects/{project_id}/graphSchema",
                    "schema": f"/api/projects/{project_id}/schema",
                    "health": f"/api/projects/{project_id}/health"
                }
            }
        else:
            # Return all projects
            return {
                "projects": {
                    pid: {
                        "name": project.name,
                        "endpoints": {
                            "base": f"/api/projects/{pid}",
                            "query": f"/api/projects/{pid}/query", 
                            "graphSchema": f"/api/projects/{pid}/graphSchema",
                            "schema": f"/api/projects/{pid}/schema",
                            "health": f"/api/projects/{pid}/health"
                        }
                    }
                    for pid, project in self.projects.items()
                }
            }
    
    def start(
        self,
        host: str = "0.0.0.0",
        port: int = 9080,
        dev: bool = False,
        show_apis: bool = True
    ) -> None:
        """
        Start the Kineviz Database Proxy server
        Args:
            host: Host to bind to (default: "0.0.0.0")
            port: Port to bind to (default: 9080)
            show_apis: Show API endpoints information (default: True)
        """
        print("\n[START] Starting Kineviz Database Proxy...")
        print(f"   Web UI: http://{host if host != '0.0.0.0' else 'localhost'}:{port}")
        
        if show_apis and self.projects:
            print("\n[API] Available API Endpoints:")
            for project_id, project in self.projects.items():
                databaseType = project.database_type._value_ if hasattr(project.database_type, '_value_') else project.database_type
                print(f"   Project: {project.name}")
                base_url = f"http://{host if host != '0.0.0.0' else 'localhost'}:{port}/api/{databaseType}/{project.name}"
                print(f"     - API URL(GraphXR): {base_url}")
                print(f"     - Query API       : {base_url}/query")
                print(f"     - Schema API      : {base_url}/schema")

        # Configure logging
        import logging
        logging.basicConfig(
            level=logging.INFO if not dev else logging.DEBUG,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        
        # Run the server
        try:
            uvicorn.run(
                "graphxr_database_proxy.main:app",
                host=host,
                port=port,
                reload=dev,
                log_level="info" if not dev else "debug"
            )
        except KeyboardInterrupt:
            print("\n[STOP] Stopping Kineviz Database Proxy...")
            self.stop()
    
    def stop(self) -> None:
        """Stop the server"""
        print("[SUCCESS] Kineviz Database Proxy stopped")