#!/usr/bin/env python3
"""
Kineviz Database Proxy - Complete Python API Example

This example demonstrates how to use the DatabaseProxy class directly via Python code:
1. Create Spanner database projects (Service Account authentication only)
2. Start services
3. Output corresponding project API endpoints

Requirements:
- Must have a valid Google Cloud Service Account JSON file
- Ensure Service Account has Spanner access permissions
"""

import os
from pathlib import Path
from kineviz_database_proxy import DatabaseProxy


def main():
    """Main function - complete usage example"""
    
    print("🔧 Kineviz Database Proxy - Python API Example")
    print("=" * 50)
    
    # 1. Create DatabaseProxy instance
    print("1. Creating DatabaseProxy instance...")
    proxy = DatabaseProxy(config_dir="./config")
    
    # 2. Prepare Service Account configuration
    # Supports two methods: file path or JSON string
    
    # Method 1: Using file path
    service_account_path = "./service-account.json"
    
    # Method 2: Using JSON string (example)
    service_account_json = '''
    {
        "type": "service_account",
        "project_id": "your-gcp-project-id",
        "private_key_id": "your-private-key-id",
        "private_key": "-----BEGIN PRIVATE KEY-----\\nYOUR_PRIVATE_KEY_HERE\\n-----END PRIVATE KEY-----\\n",
        "client_email": "your-service-account@your-gcp-project-id.iam.gserviceaccount.com",
        "client_id": "your-client-id",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/your-service-account%40your-gcp-project-id.iam.gserviceaccount.com"
    }
    '''
    
    # Choose which method to use
    use_json_string = False  # Set to True to use JSON string method
    
    if use_json_string:
        print("💡 Using Service Account JSON string")
        credentials = service_account_json
    else:
        print("💡 Using Service Account file path")
        # Check if Service Account file exists
        if not Path(service_account_path).exists():
            print(f"❌ Service Account file not found: {service_account_path}")
            print("\n📋 Please follow these steps to prepare Service Account:")
            print("   1. Visit Google Cloud Console")
            print("   2. Create or select a project")
            print("   3. Enable Cloud Spanner API")
            print("   4. Create Service Account")
            print("   5. Download Service Account JSON key file")
            print("   6. Rename file to 'service-account.json' and place in current directory")
            print("\n💡 Or set use_json_string = True to use JSON string method")
            return
        
        credentials = service_account_path
    
    # 3. Add database configuration
    print("2. Adding Spanner database configuration...")
    
    try:
        project_id = proxy.add_project(
            project_name="My Spanner Project",
            database_type="spanner",                     # Explicitly specify database type
            project_id="your-gcp-project-id",           # Replace with your GCP project ID
            instance_id="your-spanner-instance-id",     # Replace with your Spanner instance ID
            database_id="your-database-id",             # Replace with your Spanner database ID
            credentials=credentials,                     # Service Account file path or JSON string
            graph_name="my_graph"                       # Optional graph name
        )
        
        print(f"✅ Database configuration added successfully, project ID: {project_id}")
        
    except Exception as e:
        print(f"❌ Failed to add database configuration: {e}")
        return
    
    # 4. Optional: Add more databases
    print("\n3. Optional: Add more databases...")
    print("   (Current example only adds one database)")
    
    # Example: Add second database
    # try:
    #     project_id_2 = proxy.add_project(
    #         project_name="Second Spanner Project",
    #         database_type="spanner",
    #         project_id="your-second-project-id",
    #         instance_id="your-second-instance-id",
    #         database_id="your-second-database-id",
    #         credentials=credentials  # Or use different credentials
    #     )
    #     print(f"✅ Second database configuration added successfully, project ID: {project_id_2}")
    # except Exception as e:
    #     print(f"❌ Failed to add second database configuration: {e}")
    
    # 5. List all configured projects
    print("\n4. Listing all configured projects...")
    projects = proxy.list_projects()
    
    if projects:
        print(f"📋 Configured {len(projects)} projects:")
        for db_id, db_info in projects.items():
            print(f"   - Name: {db_info['name']}")
            print(f"     Database Type: {db_info['database_type']}")
            print(f"     Project ID: {db_info['project_id']}")
            print(f"     Instance ID: {db_info['instance_id']}")
            print(f"     Database ID: {db_info['database_id']}")
            print(f"     Graph Name: {db_info['graph_name'] or 'default'}")
            print(f"     Internal ID: {db_id}")
            print()
    else:
        print("   No configured projects")
        return
    
    # 6. Get project API endpoints
    print("5. Getting project API endpoints...")
    
    # Get API endpoints for all projects
    all_apis = proxy.get_project_apis()
    print("📡 API endpoints for all projects:")
    print("=" * 30)
    
    for pid, project_info in all_apis.get("projects", {}).items():
        print(f"Project: {project_info['name']}")
        for endpoint_name, endpoint_url in project_info["endpoints"].items():
            print(f"  {endpoint_name}: {endpoint_url}")
        print()
    
    # Get API endpoints for specific project by ID
    specific_api = proxy.get_project_apis(project_id)
    print(f"📡 API endpoints for project '{specific_api['name']}' (by ID):")
    print("=" * 30)
    for endpoint_name, endpoint_url in specific_api["endpoints"].items():
        print(f"  {endpoint_name}: {endpoint_url}")
    print()
    
    # NEW: Get API endpoints for specific project by name
    project_name = specific_api['name']
    api_by_name = proxy.get_project_apis(project_name)
    print(f"📡 API endpoints for project '{api_by_name['name']}' (by name):")
    print("=" * 30)
    for endpoint_name, endpoint_url in api_by_name["endpoints"].items():
        print(f"  {endpoint_name}: {endpoint_url}")
    
    # 7. Start server
    print("\n6. Starting Kineviz Database Proxy server...")
    print("=" * 50)
    print("🚀 Server is about to start...")
    print("   - Web UI will be available in browser")
    print("   - API documentation accessible via /docs")
    print("   - Use Ctrl+C to stop server")
    print()
    
    try:
        # Start server, show API endpoints
        proxy.start(
            host="0.0.0.0",
            port=3002,
            dev=False,      # Set to True to enable development mode (hot reload)
            show_apis=True  # Show API endpoint information
        )
    except KeyboardInterrupt:
        print("\n⏹️  User stopped the server")
    except Exception as e:
        print(f"\n❌ Server startup failed: {e}")
    finally:
        proxy.stop()


def example_usage_patterns():
    """Show different usage patterns"""
    
    print("\n" + "=" * 60)
    print("📖 Other Usage Pattern Examples")
    print("=" * 60)
    
    # Pattern 1: Simplest usage
    print("\n🔸 Pattern 1: Simplest Usage")
    print("-" * 30)
    print("""
from kineviz_database_proxy import DatabaseProxy

proxy = DatabaseProxy()

# Method A: Using file path
proxy.add_project(
    project_name="MySpannerProject",
    database_type="spanner",
    project_id="your-gcp-project-id",
    instance_id="your-spanner-instance-id",
    database_id="your-database-id",
    credentials="/path/to/your/service-account.json"
)

# Method B: Using JSON string
service_account_json = '''{"type": "service_account", "project_id": "...", ...}'''
proxy.add_project(
    project_name="MySpannerProject",
    database_type="spanner",
    project_id="your-gcp-project-id",
    instance_id="your-spanner-instance-id",
    database_id="your-database-id",
    credentials=service_account_json
)

proxy.start(port=3002)
    """)
    
    # Pattern 2: Configure multiple databases
    print("\n🔸 Pattern 2: Configure Multiple Databases")
    print("-" * 30)
    print("""
proxy = DatabaseProxy()

# Add production database (using file)
prod_id = proxy.add_project(
    project_name="Production Environment",
    database_type="spanner",
    project_id="prod-project-id", 
    instance_id="prod-instance",
    database_id="prod-database",
    credentials="./prod-service-account.json"
)

# Add test database (using JSON string)
test_service_account = '''{"type": "service_account", ...}'''
test_id = proxy.add_project(
    project_name="Test Environment",
    database_type="spanner",
    project_id="test-project-id",
    instance_id="test-instance", 
    database_id="test-database",
    credentials=test_service_account
)

proxy.start(port=3002)
    """)
    
    # Pattern 3: Programmatic management
    print("\n🔸 Pattern 3: Programmatic Management")
    print("-" * 30)
    print("""
proxy = DatabaseProxy()

# Dynamically add databases
environments = {
    "dev": {"file": "./dev-service-account.json"},
    "staging": {"json": '''{"type": "service_account", ...}'''},
    "prod": {"file": "./prod-service-account.json"}
}

for env, config in environments.items():
    credentials = config.get("file") or config.get("json")
    proxy.add_project(
        project_name=f"{env.upper()} Environment",
        database_type="spanner",
        project_id=f"{env}-project-id",
        instance_id=f"{env}-instance",
        database_id=f"{env}-database", 
        credentials=credentials
    )

# Get all API endpoints
apis = proxy.get_project_apis()
print("Configured API endpoints:", apis)

# Start server
proxy.start(port=3002, dev=True)
    """)


if __name__ == "__main__":
    # Run main example
    main()
    
    # Show other usage patterns
    example_usage_patterns()