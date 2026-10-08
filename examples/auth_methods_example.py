#!/usr/bin/env python3
"""
Kineviz Database Proxy - Service Account Authentication Methods Example

This example demonstrates different ways to use Service Account authentication:
1. File Path Method - Pass the path to the JSON file
2. JSON String Method - Pass the Service Account JSON content directly
3. Environment Variable Method - Get JSON from environment variables

All methods support the same functionality. Choose the appropriate method 
based on your deployment environment and security requirements.
"""

import os
import json
from pathlib import Path
from graphxr_database_proxy import DatabaseProxy


def example_with_file_path():
    """Example 1: Using file path method"""
    
    print("🔐 Service Account Authentication - File Path Method")
    print("=" * 60)
    
    proxy = DatabaseProxy()
    
    # Using file path method
    try:
        project_id = proxy.add_project(
            project_name="File Path Project",
            database_type="spanner",
            project_id="your-gcp-project-id",
            instance_id="your-spanner-instance-id",
            database_id="your-database-id",
            credentials="./examples/service-account.json",  # File path
            graph_name="file_path_graph"
        )
        
        print(f"✅ Project created successfully: {project_id}")
        
        # Get API endpoints
        apis = proxy.get_project_apis(project_id)
        print(f"📡 API endpoints: {apis['endpoints']}")
        
        return proxy, project_id
        
    except FileNotFoundError as e:
        print(f"❌ File not found: {e}")
        print("💡 Please ensure service-account.json file exists in current directory")
        return None, None
    except Exception as e:
        print(f"❌ Configuration failed: {e}")
        return None, None


def example_with_json_string():
    """Example 2: Using JSON string method"""
    
    print("\n🔐 Service Account Authentication - JSON String Method")
    print("=" * 60)
    
    proxy = DatabaseProxy()
    
    # Service Account JSON string
    # Note: In actual use, you should get this from environment variables, 
    # config files, or secure storage
    service_account_json = '''
    {
        "type": "service_account",
        "project_id": "your-gcp-project-id",
        "private_key_id": "your-private-key-id",
        "private_key": "-----BEGIN PRIVATE KEY-----\\nYOUR_PRIVATE_KEY_CONTENT_HERE\\n-----END PRIVATE KEY-----\\n",
        "client_email": "your-service-account@your-gcp-project-id.iam.gserviceaccount.com",
        "client_id": "123456789012345678901",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/your-service-account%40your-gcp-project-id.iam.gserviceaccount.com"
    }
    '''

    ## force use the ./examples/service-account.json file for demo purpose
    service_account_json_path = Path("./examples/service-account.json")
    if service_account_json_path.exists():
        with open(service_account_json_path, 'r', encoding='utf-8') as f:
            service_account_json = f.read()
    
    try:
        project_id = proxy.add_project(
            project_name="JSON String Project",
            database_type="spanner",
            project_id="your-gcp-project-id",
            instance_id="your-spanner-instance-id",
            database_id="your-database-id",
            credentials=service_account_json,  # JSON string
            graph_name="json_string_graph"
        )
        
        print(f"✅ Project created successfully: {project_id}")
        
        # Get API endpoints
        apis = proxy.get_project_apis(project_id)
        print(f"📡 API endpoints: {apis['endpoints']}")
        
        return proxy, project_id
        
    except json.JSONDecodeError as e:
        print(f"❌ JSON format error: {e}")
        print("💡 Please check if Service Account JSON format is correct")
        return None, None
    except Exception as e:
        print(f"❌ Configuration failed: {e}")
        return None, None


def example_from_environment():
    """Example 3: Getting Service Account JSON from environment variables"""
    
    print("\n🔐 Service Account Authentication - Environment Variable Method")
    print("=" * 60)
    
    # Get Service Account JSON from environment variable
    service_account_env = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
    
    if not service_account_env:
        print("❌ Environment variable GOOGLE_SERVICE_ACCOUNT_JSON not set")
        print("💡 Please set environment variable:")
        print('   export GOOGLE_SERVICE_ACCOUNT_JSON=\'{"type": "service_account", ...}\'')
        return None, None
    
    proxy = DatabaseProxy()
    
    try:
        project_id = proxy.add_project(
            project_name="Environment Variable Project",
            database_type="spanner",
            project_id="your-gcp-project-id",
            instance_id="your-spanner-instance-id",
            database_id="your-database-id",
            credentials=service_account_env,  # From environment variable
            graph_name="env_var_graph"
        )
        
        print(f"✅ Project created successfully: {project_id}")
        
        # Get API endpoints
        apis = proxy.get_project_apis(project_id)
        print(f"📡 API endpoints: {apis['endpoints']}")
        
        return proxy, project_id
        
    except Exception as e:
        print(f"❌ Configuration failed: {e}")
        return None, None


def compare_methods():
    """Compare pros and cons of different authentication methods"""
    
    print("\n📊 Service Account Authentication Method Comparison")
    print("=" * 60)
    
    comparison = {
        "File Path Method": {
            "pros": [
                "Simple and easy to use, suitable for development",
                "Files can be managed and version controlled independently",
                "Easy to switch between different environments"
            ],
            "cons": [
                "Need to manage file security",
                "Must ensure file exists during deployment",
                "Files might be accidentally committed to version control"
            ],
            "use_cases": [
                "Local development",
                "Testing environments",
                "Environments with file system access"
            ]
        },
        "JSON String Method": {
            "pros": [
                "No dependency on file system",
                "Better suited for containerized deployments",
                "Can be dynamically retrieved from various sources"
            ],
            "cons": [
                "Need careful handling of string escaping",
                "Code might contain sensitive information",
                "Credentials might be exposed during debugging"
            ],
            "use_cases": [
                "Production environment deployment",
                "Containerized applications",
                "CI/CD pipelines",
                "Retrieving credentials from secret management services"
            ]
        }
    }
    
    for method, details in comparison.items():
        print(f"\n🔸 {method}")
        print(f"   Pros:")
        for pro in details["pros"]:
            print(f"     ✅ {pro}")
        print(f"   Cons:")
        for con in details["cons"]:
            print(f"     ⚠️  {con}")
        print(f"   Use Cases:")
        for use_case in details["use_cases"]:
            print(f"     💡 {use_case}")


def security_best_practices():
    """Security best practices"""
    
    print("\n🔒 Security Best Practices")
    print("=" * 60)
    
    practices = [
        "🔐 Never commit Service Account keys to version control systems",
        "🔄 Regularly rotate Service Account keys",
        "🎯 Follow principle of least privilege, grant only necessary permissions",
        "📝 Use environment variables or secret management services for sensitive information",
        "🔍 Monitor Service Account usage",
        "🚫 Do not output complete Service Account information in logs",
        "🔒 Use dedicated Service Accounts for production environments",
        "🛡️  Consider using Workload Identity (GKE) or similar keyless authentication methods"
    ]
    
    for i, practice in enumerate(practices, 1):
        print(f"{i:2d}. {practice}")


def main():
    """Main function - show all examples"""
    
    print("🚀 Kineviz Database Proxy - Service Account Authentication Complete Example")
    print("=" * 80)
    
    # Example 1: File path method
    proxy1, project_id1 = example_with_file_path()
    
    # Example 2: JSON string method  
    proxy2, project_id2 = example_with_json_string()
    
    # Example 3: Environment variable method
    proxy3, project_id3 = example_from_environment()
    
    # Compare different methods
    compare_methods()
    
    # Security best practices
    security_best_practices()
    
    # Summary of results
    print(f"\n📋 Example Execution Results Summary")
    print("=" * 60)
    
    success_count = 0
    if project_id1:
        print(f"✅ File path method: Success (Project ID: {project_id1})")
        success_count += 1
    else:
        print(f"❌ File path method: Failed")
    
    if project_id2:
        print(f"✅ JSON string method: Success (Project ID: {project_id2})")
        success_count += 1
    else:
        print(f"❌ JSON string method: Failed")
    
    if project_id3:
        print(f"✅ Environment variable method: Success (Project ID: {project_id3})")
        success_count += 1
    else:
        print(f"❌ Environment variable method: Failed")
    
    print(f"\n🎯 Successfully configured {success_count}/3 authentication methods")
    
    if success_count > 0:
        print(f"\n🚀 To start the server, uncomment the code below:")
        print(f"# Choose one successfully configured proxy instance to start server")
        print(f"# if proxy1: proxy1.start(port=3002)")
        print(f"# if proxy2: proxy2.start(port=3003)")  
        print(f"# if proxy3: proxy3.start(port=3004)")
    
    print(f"\n💡 More examples can be found at:")
    print(f"   - examples/quick_start.py")
    print(f"   - examples/python_api_example.py")
    print(f"   - examples/api_test.py")


if __name__ == "__main__":
    main()