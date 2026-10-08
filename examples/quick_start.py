#!/usr/bin/env python3
"""
Kineviz Database Proxy - Quick Start Example

This is the simplest example showing how to quickly start Kineviz Database Proxy.
Just modify the configuration parameters to use.

Supports two Service Account authentication methods:
1. File Path: Pass the path to the JSON file
2. JSON String: Pass the Service Account JSON content directly
"""

from graphxr_database_proxy import DatabaseProxy


def quick_start_with_service_account_file():
    """Quick start example using file path"""
    
    print("🚀 Kineviz Database Proxy - Using File Path")
    print("=" * 50)
    
    # Create proxy instance
    proxy = DatabaseProxy()
    
    # Method 1: Using file path
    proxy.add_project(
        project_name="MySpannerProject",                    # Project name
        database_type="spanner",                            # Database type
        project_id="your-gcp-project-id",                  # Your GCP project ID
        instance_id="your-spanner-instance-id",            # Your Spanner instance ID  
        database_id="your-database-id",                    # Your Spanner database ID
        credentials="/path/to/your/service-account.json"   # Service Account JSON file path
    )
    
    # Start server
    proxy.start(port=3002)


def quick_start_with_service_account_json():
    """Quick start example using JSON string"""
    
    print("🚀 Kineviz Database Proxy - Using JSON String")
    print("=" * 50)
    
    # Create proxy instance
    proxy = DatabaseProxy()
    
    # Method 2: Using JSON string (replace with your actual Service Account JSON)
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
    
    proxy.add_project(
        project_name="MySpannerProject",                    # Project name
        database_type="spanner",                            # Database type
        project_id="your-gcp-project-id",                  # Your GCP project ID
        instance_id="your-spanner-instance-id",            # Your Spanner instance ID  
        database_id="your-database-id",                    # Your Spanner database ID
        credentials=service_account_json                    # Service Account JSON string
    )
    
    # Start server
    proxy.start(port=3002)


def quick_start_with_google_ADC_json():
    """Quick start example using JSON string"""
    
    print("🚀 Kineviz Database Proxy - Using JSON String")
    print("=" * 50)
    
    # Create proxy instance
    proxy = DatabaseProxy()
    
    # Method 2: Using JSON string (replace with your actual Service Account JSON)
    service_account_json = '''
    {
        "type": "google_ADC"
    }
    '''
    
    proxy.add_project(
        project_name="spanner_adc",                    # Project name
        database_type="spanner",                            # Database type
        project_id="your-gcp-project-id",                  # Your GCP project ID
        instance_id="your-spanner-instance-id",            # Your Spanner instance ID  
        database_id="your-database-id",                    # Your Spanner database ID
        credentials=service_account_json                    # Service Account JSON string
    )
    
    # Start server
    proxy.start(port=3002)



def quick_start():
    """Default quick start example - using file path"""
    quick_start_with_service_account_file()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "json":
        # Use JSON string method
        quick_start_with_service_account_json()
    elif len(sys.argv) > 1 and sys.argv[1] == "adc":
        # Use Google ADC JSON string method
        quick_start_with_google_ADC_json()
    else:
        # Default to file path method
        quick_start_with_service_account_file()
    
    print("\n💡 Usage tips:")
    print("  - Use file path (default): python quick_start.py")
    print("  - Use JSON string: python quick_start.py json")