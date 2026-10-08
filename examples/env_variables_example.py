#!/usr/bin/env python3
"""
Kineviz Database Proxy - Environment Variables Example

This example demonstrates how to use environment variables to configure
the DatabaseProxy for Spanner projects. This approach is especially useful
for production deployments and containerized environments.

Environment Variables Supported:
- PROJECT_NAME: Default project name
- SPANNER_PROJECT_ID: Default GCP project ID
- SPANNER_INSTANCE_ID: Default Spanner instance ID  
- SPANNER_DATABASE_ID: Default Spanner database ID
- SPANNER_CREDENTIALS_PATH: Default path to service account JSON file
- SPANNER_GRAPH_NAME: Default graph name
"""

import os
from pathlib import Path
from graphxr_database_proxy import DatabaseProxy


def example_with_environment_variables():
    """Example: Using environment variables for configuration"""
    
    print("🌍 Environment Variables Configuration Example")
    print("=" * 60)
    
    # Set environment variables programmatically for this example
    # In real usage, these would be set in your deployment environment
    
    print("🔧 Setting environment variables:")
    os.environ['PROJECT_NAME'] = 'Environment Project'
    os.environ['SPANNER_PROJECT_ID'] = 'your-gcp-project-id'
    os.environ['SPANNER_INSTANCE_ID'] = 'your-spanner-instance-id'
    os.environ['SPANNER_DATABASE_ID'] = 'your-database-id'
    os.environ['SPANNER_CREDENTIALS_PATH'] = './examples/service-account.json'
    os.environ['SPANNER_GRAPH_NAME'] = 'env_graph'
    
    # Display the environment variables
    env_vars = [
        'PROJECT_NAME',
        'SPANNER_PROJECT_ID',
        'SPANNER_INSTANCE_ID', 
        'SPANNER_DATABASE_ID',
        'SPANNER_CREDENTIALS_PATH',
        'SPANNER_GRAPH_NAME'
    ]
    
    for var in env_vars:
        value = os.getenv(var, 'Not set')
        print(f"   {var}: {value}")
    
    print("\n🚀 Creating project using environment variables:")
    
    proxy = DatabaseProxy()
    
    try:
        # No parameters needed! All will be read from environment variables
        project_id = proxy.add_project()
        
        print(f"✅ Project created successfully: {project_id}")
        
        # Get API endpoints
        apis = proxy.get_project_apis(project_id)
        print(f"📡 API endpoints: {apis['endpoints']}")
        
        return proxy, project_id
        
    except Exception as e:
        print(f"❌ Configuration failed: {e}")
        return None, None


def example_mixed_parameters_and_env():
    """Example: Mixing parameters and environment variables"""
    
    print("\n🔀 Mixed Parameters and Environment Variables Example")
    print("=" * 60)
    
    # Set some environment variables
    os.environ['SPANNER_PROJECT_ID'] = 'your-gcp-project-id'
    os.environ['SPANNER_INSTANCE_ID'] = 'env-instance-id'
    os.environ['SPANNER_DATABASE_ID'] = 'env-database-id'
    os.environ['SPANNER_CREDENTIALS_PATH'] = './examples/service-account.json'
    
    proxy = DatabaseProxy()
    
    try:
        # Override some values with parameters, use env vars for others
        project_id = proxy.add_project(
            project_name="Mixed Config Project",  # Override environment
            # project_id, instance_id, database_id, credentials will come from environment variables
            graph_name="mixed_graph"  # Override environment
        )
        
        print(f"✅ Project created successfully: {project_id}")
        
        # Get API endpoints
        apis = proxy.get_project_apis(project_id)
        print(f"📡 API endpoints: {apis['endpoints']}")
        
        return proxy, project_id
        
    except Exception as e:
        print(f"❌ Configuration failed: {e}")
        return None, None


def example_production_deployment():
    """Example: Production deployment configuration"""
    
    print("\n🏭 Production Deployment Example")
    print("=" * 60)
    
    print("In a production environment, you would set environment variables like:")
    print()
    
    # Show examples for different deployment methods
    deployment_examples = {
        "Docker": [
            "# In Dockerfile or docker-compose.yml",
            "ENV PROJECT_NAME=MyProductionProject",
            "ENV SPANNER_PROJECT_ID=your-gcp-project-id",
            "ENV SPANNER_INSTANCE_ID=prod-spanner-instance",
            "ENV SPANNER_DATABASE_ID=prod-database",
            "ENV SPANNER_CREDENTIALS_PATH=/secrets/service-account.json",
            "ENV SPANNER_GRAPH_NAME=prod_graph"
        ],
        "Kubernetes": [
            "# In deployment.yaml",
            "env:",
            "- name: PROJECT_NAME",
            "  value: MyProductionProject",
            "- name: SPANNER_PROJECT_ID",
            "  value: your-gcp-project-id",
            "- name: SPANNER_INSTANCE_ID",
            "  value: prod-spanner-instance",
            "- name: SPANNER_DATABASE_ID",
            "  value: prod-database",
            "- name: SPANNER_CREDENTIALS_PATH",
            "  valueFrom:",
            "    secretKeyRef:",
            "      name: spanner-credentials",
            "      key: service-account.json",
            "- name: SPANNER_GRAPH_NAME",
            "  value: prod_graph"
        ],
        "Shell/Bash": [
            "# In shell script or .bashrc",
            "export PROJECT_NAME=MyProductionProject",
            "export SPANNER_PROJECT_ID=your-gcp-project-id",
            "export SPANNER_INSTANCE_ID=prod-spanner-instance",
            "export SPANNER_DATABASE_ID=prod-database",
            "export SPANNER_CREDENTIALS_PATH=/path/to/service-account.json",
            "export SPANNER_GRAPH_NAME=prod_graph"
        ],
        "Python Code": [
            "# In Python application",
            "import os",
            "os.environ['PROJECT_NAME'] = 'MyProductionProject'",
            "os.environ['SPANNER_PROJECT_ID'] = 'your-gcp-project-id'",
            "os.environ['SPANNER_INSTANCE_ID'] = 'prod-spanner-instance'",
            "os.environ['SPANNER_DATABASE_ID'] = 'prod-database'",
            "os.environ['SPANNER_CREDENTIALS_PATH'] = '/path/to/service-account.json'",
            "os.environ['SPANNER_GRAPH_NAME'] = 'prod_graph'"
        ]
    }
    
    for method, lines in deployment_examples.items():
        print(f"📋 {method}:")
        for line in lines:
            print(f"   {line}")
        print()
    
    print("Then in your application code:")
    print("   proxy = DatabaseProxy()")
    print("   project_id = proxy.add_project()  # No parameters needed!")
    print("   proxy.start()")


def check_environment_variables():
    """Check which environment variables are currently set"""
    
    print("\n🔍 Current Environment Variables Status")
    print("=" * 60)
    
    env_vars = {
        'PROJECT_NAME': 'Project name',
        'SPANNER_PROJECT_ID': 'GCP project ID',
        'SPANNER_INSTANCE_ID': 'Spanner instance ID',
        'SPANNER_DATABASE_ID': 'Spanner database ID', 
        'SPANNER_CREDENTIALS_PATH': 'Path to service account JSON',
        'SPANNER_CREDENTIALS_JSON': 'Service account JSON string',
        'SPANNER_GRAPH_NAME': 'Graph name'
    }
    
    print("Environment Variable Status:")
    for var, description in env_vars.items():
        value = os.getenv(var)
        if value:
            # Mask sensitive information
            if 'CREDENTIALS' in var and len(value) > 20:
                display_value = f"{value[:10]}...{value[-10:]}"
            else:
                display_value = value
            print(f"   ✅ {var}: {display_value}")
        else:
            print(f"   ❌ {var}: Not set")
    
    print()
    
    # Check if we have minimum required configuration
    required_for_spanner = ['SPANNER_PROJECT_ID', 'SPANNER_INSTANCE_ID', 'SPANNER_DATABASE_ID', 'SPANNER_CREDENTIALS_PATH']
    missing_required = [var for var in required_for_spanner if not os.getenv(var)]
    
    if missing_required:
        print(f"⚠️  Missing required environment variables for Spanner: {', '.join(missing_required)}")
        print("   You can still use explicit parameters in add_project()")
    else:
        print("✅ All required Spanner environment variables are set!")
        print("   You can use proxy.add_project() with no parameters at all!")


def best_practices():
    """Environment variables best practices"""
    
    print("\n💡 Environment Variables Best Practices")
    print("=" * 60)
    
    practices = [
        "🔐 Use environment variables for sensitive information (credentials, keys)",
        "🏗️  Set environment variables in your deployment/build system",
        "🔄 Use different environment variables for different environments (dev, staging, prod)",
        "📝 Document all required environment variables in your README",
        "🛡️  Never commit environment variable files to version control",
        "🔍 Use tools like python-dotenv for local development with .env files",
        "📋 Validate environment variables at application startup",
        "🚫 Avoid hardcoding sensitive values as fallbacks",
        "🔒 Use secret management systems in production (AWS Secrets Manager, etc.)",
        "📦 Consider using configuration schemas to validate environment variables"
    ]
    
    for i, practice in enumerate(practices, 1):
        print(f"{i:2d}. {practice}")


def main():
    """Main function - demonstrate environment variables usage"""
    
    print("🚀 Kineviz Database Proxy - Environment Variables Complete Example")
    print("=" * 80)
    
    # Check current environment status
    check_environment_variables()
    
    # Example 1: Pure environment variables
    proxy1, project_id1 = example_with_environment_variables()
    
    # Example 2: Mixed approach
    proxy2, project_id2 = example_mixed_parameters_and_env()
    
    # Production deployment guidance
    example_production_deployment()
    
    # Best practices
    best_practices()
    
    # Summary
    print(f"\n📋 Example Results Summary")
    print("=" * 60)
    
    success_count = 0
    if project_id1:
        print(f"✅ Environment variables method: Success (Project ID: {project_id1})")
        success_count += 1
    else:
        print(f"❌ Environment variables method: Failed")
    
    if project_id2:
        print(f"✅ Mixed configuration method: Success (Project ID: {project_id2})")
        success_count += 1
    else:
        print(f"❌ Mixed configuration method: Failed")
    
    print(f"\n🎯 Successfully configured {success_count}/2 methods")
    
    if success_count > 0:
        print(f"\n🚀 To start the server:")
        if proxy1:
            print(f"   # proxy1.start(port=4001)")
        if proxy2:
            print(f"   # proxy2.start(port=4002)")
    
    print(f"\n💡 Other examples:")
    print(f"   - examples/quick_start.py")
    print(f"   - examples/auth_methods_example.py")
    print(f"   - examples/python_api_example.py")


if __name__ == "__main__":
    main()