"""
Google Application Default Credentials (ADC) Example

This example demonstrates how to configure and start the Kineviz Database Proxy
using Google Application Default Credentials (ADC) for authentication.

# Quick Start

1. Set the ADC by logging into GCP with gcloud

```
gcloud auth application-default login
```

2. Clone the repository and install the dependencies

```bash
git clone git@github.com:Kineviz/kineviz-database-proxy.git
cd kineviz-database-proxy
uv venv
source .venv/bin/activate
uv pip install kineviz-database-proxy google-auth google-cloud-resource-manager
export PROJECT_ID="your-gcp-project-id"
export INSTANCE_ID="your-spanner-instance-id"
export DATABASE_ID="your-spanner-database-id" # e.g. "retail"
export PROPERTY_GRAPH_NAME="your-property-graph-name" # e.g. "ECommerceGraph"
python examples/google_adc.py
```

3. Connect to the Kineviz Database Proxy from GraphXR

Copy the "API URL(GraphXR)" from the output of google_adc.py and paste it into the proxy url  below:
Create GraphXR project
- Database Type = `Database Proxy`
- Kineviz Database Proxy URL = `http://localhost:3003/api/spanner/kineviz-public`

4. Run a query

In the Query panel run the query:

```
GRAPH ECommerceGraph
MATCH (n)-[r]-(m)
RETURN n, r, m;
```

# More information

Configure ADC using one of these methods:
    - Local development: 
        - Install gcloud CLI: https://cloud.google.com/sdk/docs/install
        - Run `gcloud auth application-default login`
    - GCE/GKE: VM instance will use attached service account automatically
        - Ensure the VM with the scopes("--scopes=cloud-platform") has access to Spanner
    - Environment variable: Set GOOGLE_APPLICATION_CREDENTIALS to service account JSON path

Installation:
    uv pip install kineviz-database-proxy google-auth google-cloud-resource-manager

Usage:
    1. Set environment variables or update defaults in the script:
       - PROJECT_ID (required)
       - INSTANCE_ID (required)
       - DATABASE_ID (required)
       - PROPERTY_GRAPH_NAME (optional)
    2. Run: python examples/google_adc.py
    3. Connect GraphXR to the URL shown in the console output

For more information:
    - ADC documentation: https://cloud.google.com/docs/authentication/production
    - GraphXR documentation: https://kineviz.com/docs
"""

import os
import socket
from graphxr_database_proxy import DatabaseProxy


def find_available_port(start=3002, max_attempts=100):
    """Find an available port starting from the given port."""
    for port in range(start, start + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('', port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"Could not find available port in range {start}-{start + max_attempts}")


# Configuration - Set via environment variables or update defaults below
PROJECT_ID = os.getenv("PROJECT_ID")           # GCP project ID (required)
INSTANCE_ID = os.getenv("INSTANCE_ID")            # Your Spanner instance ID
DATABASE_ID = os.getenv("DATABASE_ID")                  # Your Spanner database ID
PROPERTY_GRAPH_NAME = os.getenv("PROPERTY_GRAPH_NAME")  # Your property graph name (optional)

# Required environment variables
REQUIRED_ENV_VARS = ["PROJECT_ID", "INSTANCE_ID", "DATABASE_ID", "PROPERTY_GRAPH_NAME"]


def check_required_env_vars():
    """Check if all required environment variables are set."""
    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    if missing:
        print(f"\n[ERROR] Missing required environment variables: {', '.join(missing)}")
        print("\nPlease set them before running:")
        for var in missing:
            print(f"  export {var}=\"your-value\"")
        print()
        return False
    return True


def quick_start_with_google_adc():
    """
    Start the Kineviz Database Proxy using Google ADC authentication.
    
    This function:
    1. Uses configuration from environment variables
    2. Configures the proxy with ADC credentials
    3. Starts the server on an available port (starting from 3002)
    
    Returns:
        None
    """
    # Create proxy instance
    proxy = DatabaseProxy()

    # Use project_id as the project name

    # Add project with ADC credentials
    proxy.add_project(
        project_name=PROJECT_ID,      # Project name for the proxy
        database_type="spanner",        # Database type
        project_id=PROJECT_ID,          # GCP project ID
        instance_id=INSTANCE_ID,        # Spanner instance ID
        database_id=DATABASE_ID,        # Spanner database ID
        graph_name=PROPERTY_GRAPH_NAME, # Property graph name (optional)
        credentials={                   # ADC credentials configuration
            "type": "google_ADC"
        }
    )

    # Start the proxy server on an available port
    port = find_available_port()
    print(f"\n Kineviz Database Proxy Server Starting...")
    print(f"\n Server URL: http://localhost:{port}")
    print(f" GraphXR API: http://localhost:{port}/api/spanner/{PROJECT_ID}")
    print(f"\n Copy the GraphXR API URL above into GraphXR using 'Database Proxy' connection type.\n")
    
    proxy.start(port=port)


if __name__ == "__main__":
    if not check_required_env_vars():
        exit(1)
    try:
        quick_start_with_google_adc()
    except Exception as e:
        print(f"\n Error: {e}")
        print("\nTroubleshooting:")
        print("  1. Ensure ADC is configured: gcloud auth application-default login")
        print("  2. Verify your credentials have Spanner permissions")
        print("  3. Check that INSTANCE_ID and DATABASE_ID are correct")
        print("  4. See: https://cloud.google.com/docs/authentication/production\n")

