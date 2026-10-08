# Kineviz Database Proxy - Python API Examples

This directory contains complete examples for using the Kineviz Database Proxy Python API.

## 📁 File Description

### Example Files

- **`quick_start.py`** - Simplest quick start example (supports two authentication methods)
- **`python_api_example.py`** - Complete feature demonstration with detailed explanations
- **`api_test.py`** - API endpoint testing example
- **`auth_methods_example.py`** - Detailed comparison of Service Account authentication methods
- **`env_variables_example.py`** - Environment variables configuration example
- **`get_project_apis_example.py`** - Enhanced get_project_apis() functionality demonstration
- **`service-account-example.json`** - Service Account JSON file template

## 🚀 Quick Start

### 1. Prepare Service Account

Kineviz Database Proxy supports two Service Account authentication methods:

#### Method A: File Path Method
1. Visit [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Enable Cloud Spanner API
4. Create Service Account and download JSON key file
5. Assign necessary permissions to Service Account

#### Method B: JSON String Method
1. Get Service Account JSON content
2. Pass JSON content as string to `credentials` parameter
3. Suitable for retrieving credentials from environment variables, config services, or secret management systems

### 2. Run Examples

#### Method 1: Quick Start
```bash
# 1. Modify configuration parameters in quick_start.py
# 2. Run example
python examples/quick_start.py
```

#### Method 2: Complete Example
```bash
# 1. Place your service account JSON file in project root directory
# 2. Modify configuration parameters in python_api_example.py
# 3. Run complete example
python examples/python_api_example.py
```

#### Method 3: API Testing
```bash
# 1. Start server in one terminal
python examples/quick_start.py

# 2. Run API tests in another terminal
python examples/api_test.py
```

#### Method 4: Authentication Method Comparison
```bash
# View detailed comparison of different authentication methods and best practices
python examples/auth_methods_example.py
```

## 📋 credentials Parameter Explanation

The `credentials` parameter supports two formats:

### Format 1: File Path
```python
# Absolute path
credentials="/home/user/service-account.json"

# Relative path  
credentials="./service-account.json"

# Windows path
credentials="C:\\path\\to\\service-account.json"
```

### Format 2: JSON String
```python
# Pass JSON string directly
credentials='''
{
    "type": "service_account",
    "project_id": "your-gcp-project-id",
    "private_key": "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n",
    "client_email": "your-service-account@your-gcp-project-id.iam.gserviceaccount.com",
    ...
}
'''

# Get from environment variable
import os
credentials = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON')
```

## 📋 Configuration Parameters Explanation

Before running examples, please modify the following parameters:

```python
proxy.add_project(
    project_name="MySpannerProject",           # Your project name
    database_type="spanner",                   # Database type
    project_id="your-gcp-project-id",         # Replace with your GCP project ID
    instance_id="your-spanner-instance-id",   # Replace with your Spanner instance ID
    database_id="your-database-id",           # Replace with your Spanner database ID
    credentials="./service-account.json",     # Service Account (file path or JSON string)
    graph_name="my_graph"                     # Optional graph name
)
```

## 🌍 Environment Variables Support

You can use environment variables to configure default values for your projects. This is especially useful for production deployments:

### Supported Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `PROJECT_NAME` | Default project name | `MySpannerProject` |
| `SPANNER_PROJECT_ID` | Default GCP project ID | `your-gcp-project-id` |
| `SPANNER_INSTANCE_ID` | Default Spanner instance ID | `your-spanner-instance-id` |
| `SPANNER_DATABASE_ID` | Default Spanner database ID | `your-database-id` |
| `SPANNER_CREDENTIALS_PATH` | Default path to service account JSON | `./service-account.json` |
| `SPANNER_GRAPH_NAME` | Default graph name | `my_graph` |

### Usage Examples

#### Example 1: Using Only Environment Variables
```bash
# Set environment variables
export PROJECT_NAME="MySpannerProject"
export SPANNER_PROJECT_ID="your-gcp-project-id"
export SPANNER_INSTANCE_ID="your-spanner-instance-id"
export SPANNER_DATABASE_ID="your-database-id"
export SPANNER_CREDENTIALS_PATH="./service-account.json"
export SPANNER_GRAPH_NAME="my_graph"
```

```python
# No parameters needed - all from environment variables
proxy = DatabaseProxy()
project_id = proxy.add_project()
```

#### Example 2: Mixed Parameters and Environment Variables
```python
# Environment variables provide defaults, parameters override them
proxy = DatabaseProxy()
project_id = proxy.add_project(
    project_name="Override Project Name",  # Overrides PROJECT_NAME
    # project_id, instance_id, database_id come from environment variables
    graph_name="override_graph"           # Overrides SPANNER_GRAPH_NAME
)
```

#### Example 3: Production Deployment
```yaml
# Docker Compose example
environment:
  - PROJECT_NAME=ProductionProject
  - SPANNER_PROJECT_ID=your-gcp-project-id
  - SPANNER_INSTANCE_ID=prod-spanner-instance
  - SPANNER_DATABASE_ID=prod-database
  - SPANNER_CREDENTIALS_PATH=/secrets/service-account.json
  - SPANNER_GRAPH_NAME=prod_graph
```

### Environment Variables Example
```bash
# Run the environment variables example
python examples/env_variables_example.py
```

## 🔧 Service Account Permission Configuration

Your Service Account needs the following permissions:

- **Cloud Spanner Database User** (`roles/spanner.databaseUser`) - Execute queries
- **Cloud Spanner Database Admin** (`roles/spanner.databaseAdmin`) - Manage databases (optional)
- **Browser** (`roles/browser`) - List project resources

### Configure permissions using gcloud commands:

```bash
# Set variables
PROJECT_ID="your-gcp-project-id"
SERVICE_ACCOUNT_EMAIL="your-service-account@your-gcp-project-id.iam.gserviceaccount.com"

# Assign Spanner Database User permission
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
    --role="roles/spanner.databaseUser"

# Assign Browser permission
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
    --role="roles/browser"
```

## 📡 API Endpoints

After starting the server, each configured project will have the following API endpoints:

- `GET /health` - Service health check
- `GET /api/projects` - List all projects
- `GET /api/projects/{project_id}/health` - Project health check
- `GET /api/projects/{project_id}/schema` - Get database schema
- `POST /api/projects/{project_id}/query` - Execute SQL query

### Query Example

```bash
# Execute simple query
curl -X POST "http://localhost:3002/api/projects/{project_id}/query" \
     -H "Content-Type: application/json" \
     -d '{
       "query": "SELECT 1 as test_column",
       "parameters": {}
     }'
```

## 🔍 Enhanced get_project_apis() Method

The `get_project_apis()` method has been enhanced to support finding projects by both `project_id` and `project_name`.

### Usage Patterns

#### Get All Projects
```python
# Get API endpoints for all projects
all_apis = proxy.get_project_apis()
print("Available projects:")
for pid, project_info in all_apis.get("projects", {}).items():
    print(f"  {project_info['name']}: {project_info['endpoints']['query']}")
```

#### Get Project by ID
```python
# Get specific project by internal ID (recommended for APIs)
project_id = "abc123-def456-ghi789"
api_info = proxy.get_project_apis(project_id)
if "error" not in api_info:
    print(f"Query endpoint: {api_info['endpoints']['query']}")
```

#### Get Project by Name
```python
# Get specific project by display name (user-friendly)
project_name = "Customer Analytics"
api_info = proxy.get_project_apis(project_name)
if "error" not in api_info:
    print(f"Project ID: {api_info['project_id']}")
    print(f"Base endpoint: {api_info['endpoints']['base']}")
```

#### Error Handling
```python
# Always check for errors
result = proxy.get_project_apis("Unknown Project")
if "error" in result:
    print(f"Project not found: {result['error']}")
else:
    print(f"Found project: {result['name']}")
```

### Response Format

#### Single Project Response
```json
{
    "project_id": "abc123-def456-ghi789",
    "name": "Customer Analytics",
    "endpoints": {
        "base": "/api/projects/abc123-def456-ghi789",
        "query": "/api/projects/abc123-def456-ghi789/query",
        "schema": "/api/projects/abc123-def456-ghi789/schema",
        "graphSchema": "/api/projects/abc123-def456-ghi789/graphSchema",
        "health": "/api/projects/abc123-def456-ghi789/health"
    }
}
```

#### All Projects Response
```json
{
    "projects": {
        "abc123-def456-ghi789": {
            "name": "Customer Analytics",
            "endpoints": { "..." }
        },
        "def456-ghi789-abc123": {
            "name": "Supply Chain",
            "endpoints": { "..." }
        }
    }
}
```

#### Error Response
```json
{
    "error": "Project not found by ID or name: Unknown Project"
}
```

### Demo Example
```bash
# Run the enhanced get_project_apis example
python examples/get_project_apis_example.py
```

## 🐛 Troubleshooting

### Common Issues

1. **Service Account file not found**
   - Ensure JSON file path is correct
   - Check file permissions

2. **Insufficient permissions**
   - Verify Service Account permission configuration
   - Ensure project ID, instance ID, database ID are correct

3. **Connection failed**
   - Check network connection
   - Verify Google Cloud APIs are enabled

4. **Startup failed**
   - Check if port is already in use
   - Check error logs for detailed information

### Debug Mode

Enable development mode for detailed logs:

```python
proxy.start(port=3002, dev=True)
```

## 📚 More Resources

- [Kineviz Database Proxy Documentation](../README.md)
- [Google Cloud Spanner Documentation](https://cloud.google.com/spanner/docs)
- [Service Account Documentation](https://cloud.google.com/iam/docs/service-accounts)