# -*- coding: utf-8 -*-
"""
Google Cloud API endpoints for listing projects and instances
"""

import os

# Import and enable proxy interceptor
try:
    from .. import proxyForDev
    print("[OK] Proxy interceptor enabled")
except ImportError:
    print("[INFO] Proxy interceptor not found, continuing without it")
    pass

# Set environment variables to reduce Google Cloud client warning logs
os.environ.setdefault('GOOGLE_CLOUD_DISABLE_GRPC_FOR_GAE', 'true')
os.environ.setdefault('GRPC_VERBOSITY', 'ERROR')

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from google.cloud import spanner
from google.cloud import resourcemanager_v3
from google.oauth2 import service_account
import google.oauth2.credentials

import requests
from ..models.google import (
    BigQueryDataset,
    BigQueryProject,
    GoogleProject,
    GraphDatabase,
    SpannerDatabase,
)
from google.api_core.exceptions import GoogleAPIError
from ..common.util import get_default_oauth_config
from ..drivers.bigquery import validate_identifier
from .auth import verify_admin_token

router = APIRouter(tags=["google"])

USERINFO_SCOPES = [
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
]

SPANNER_SCOPES = [
    "https://www.googleapis.com/auth/cloudplatformprojects.readonly",
    "https://www.googleapis.com/auth/spanner.admin",
    "https://www.googleapis.com/auth/spanner.data",
    *USERINFO_SCOPES,
]

BIGQUERY_SCOPES = [
    "https://www.googleapis.com/auth/cloudplatformprojects.readonly",
    "https://www.googleapis.com/auth/bigquery",
    *USERINFO_SCOPES,
]

#: Datasets read while listing a project. The pick-a-dataset dropdown is the
#: consumer, and a project with more than this many is asked for by name instead.
MAX_DATASETS_PER_PROJECT = 200

# Note: These endpoints require admin authentication when ADMIN_PASSWORD is set.
# They also have their own Google Cloud authentication (OAuth/service account).


def get_spanner_client(project_id, credentials):
    """Get Spanner client for given project and credentials"""
    try:
        spanner_client = spanner.Client(project=project_id, credentials=credentials)
        return spanner_client
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create Spanner client: {e}")

def get_google_credentials(auth_info, auth_type='service_account', scopes=None):
    """
    Get Google credentials based on auth_info
    If contains 'token', use OAuth2, otherwise use service account

    ``scopes`` selects which API the credentials are for; it defaults to Spanner's
    so existing callers are unaffected. BigQuery passes BIGQUERY_SCOPES.
    """
    scopes = list(scopes or SPANNER_SCOPES)
    if auth_type == 'oauth2' and auth_info.get("token"):
        credentials = google.oauth2.credentials.Credentials(
            token=auth_info.get("token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=auth_info.get("client_id"),
            client_secret=auth_info.get("client_secret"),
            scopes=scopes,
        )
        return credentials, None
    if auth_type == 'google_ADC':
        # Application Default Credentials
        credentials, project = google.auth.default(scopes=scopes)
        return credentials, project
    else:
        # Service Account method
        if auth_info:
            credentials = service_account.Credentials.from_service_account_info(
                auth_info
            )
            project = auth_info.get('project_id')
            return credentials, project
        else:
            raise HTTPException(status_code=400, detail="Missing service account info")


@router.post("/api/google/spanner/list_projects", response_model=List[GoogleProject])
async def list_google_projects(
    request: Request,
    _: str | None = Depends(verify_admin_token)
):
    """List Google Cloud projects"""
    try:
        # Get authentication info from request body json
        body = await request.json()
        auth = body.get('auth', {})
        auth_type = body.get('auth_type', 'service_account')

        default_oauth = get_default_oauth_config()
        if default_oauth is None:
            default_oauth = {}  # Use empty dict if config file doesn't exist
        newAuth = { **default_oauth, **auth }

        credentials, project_id = get_google_credentials(newAuth, auth_type)

        is_service_account = credentials and isinstance(credentials, service_account.Credentials)
        # Use Resource Manager API to list projects
        projects = []

        if (is_service_account or auth_type == 'google_ADC') and project_id:
            projects.append(GoogleProject(
                name=project_id,
                id=project_id,
                instances=[]
            ))
        else:
            client = resourcemanager_v3.ProjectsClient(credentials=credentials)
            search_request = resourcemanager_v3.SearchProjectsRequest()
            page_result = client.search_projects(request=search_request)
            for project in page_result:
                projects.append(GoogleProject(
                    name=project.display_name or project.name,
                    id=project.project_id,
                    instances=[]
                ))
                
        # Only keep projects that contain Spanner instances
        spanner_projects = []

        import concurrent.futures

        def check_project_has_spanner(project):
            """Check if a project has Spanner instances"""
            try:
                spanner_client = spanner.Client(project=project.id, credentials=credentials)
                instances = list(spanner_client.list_instances())
                if instances:
                    # Add the instances {id,name} to project.instances
                    project.instances = [{"id": inst.name.split('/')[-1], "name": inst.display_name or inst.instance_id} for inst in instances]
                    return project
                return None
            except (GoogleAPIError, Exception) as e:
                print(f"Error checking project {project.id}: {e}")
                # Skip projects that can't be accessed or have no Spanner
                return None

        # Use ThreadPoolExecutor to check projects in parallel
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # Submit all tasks
            future_to_project = {
            executor.submit(check_project_has_spanner, project): project 
            for project in projects
            }
            
            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_to_project):
                result = future.result()
                if result:
                    spanner_projects.append(result)

        return spanner_projects

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/google/spanner/list_databases", response_model=list[SpannerDatabase])
async def list_google_databases(
    request_data: Dict[str, Any],
    _: str | None = Depends(verify_admin_token)
):
    """List Google Cloud Spanner databases"""
    try:
        auth =  request_data.get('auth', {})
        project_id = auth.get('project_id')
        auth_type = request_data.get('auth_type', 'service_account')
        instance_id = auth.get('instance_id')
        default_oauth = get_default_oauth_config()
        if default_oauth is None:
            default_oauth = {}  # Use empty dict if config file doesn't exist
        newAuth = { **default_oauth, **auth }
        credentials, project_id = get_google_credentials(newAuth, auth_type)

        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found")
        
        # Use Spanner client
        spanner_client = get_spanner_client(project_id, credentials)

        result = []
        
        # List all Spanner instances
        spanner_client = get_spanner_client(project_id, credentials)
        instance = spanner_client.instance(instance_id)
        if not instance.exists():
            raise HTTPException(status_code=400, detail="Instance not found")

        # List all databases in the instance
        for database in instance.list_databases():
            database_id = database.name.split('/')[-1]
            database_name = database_id  # Spanner databases usually don't have display names
            databaseItem = {
                "id": database_id,
                "name": database_name,
                "graphDBs": []
            }

            result.append(databaseItem)

            # Check if there are Property Graph databases
            try:
                # Simple temporary query to get graph database information
                try:
                    db = spanner_client.instance(instance_id).database(database_id)
                    # Use snapshot() or create session
                    with db.snapshot() as snapshot:
                        query = "SELECT PROPERTY_GRAPH_NAME FROM INFORMATION_SCHEMA.PROPERTY_GRAPHS"
                        results = snapshot.execute_sql(query)

                        for row in results:
                            graph_id = row[0]
                            databaseItem["graphDBs"].append({
                                "id": graph_id,
                                "name": graph_id
                            })
                except:
                    # Skip if query fails
                    pass

            except Exception as graph_error:
                # If query fails, ignore graph database information
                print(f"Could not query graph databases: {graph_error}")
                pass
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

# ---------------------------------------------------------------------------
# BigQuery
#
# The same project -> container -> graph cascade the Spanner form uses, one step
# shallower: BigQuery has no instance layer, so it reads project -> dataset ->
# property graph. Enumerating property graphs costs a query per dataset, so it is
# a second call rather than part of the listing.
# ---------------------------------------------------------------------------


def _bigquery_client(project_id, credentials, location=None):
    from google.cloud import bigquery

    return bigquery.Client(project=project_id, credentials=credentials, location=location)


def _dataset_location(dataset) -> Optional[str]:
    """Where a listed dataset lives.

    ``datasets.list`` returns the location, but ``DatasetListItem`` has no
    property for it, so it is read off the raw resource the item wraps. Without
    it the form cannot prefill Location, and a query aimed at the wrong location
    fails outright rather than returning nothing -- which is what silently left
    a non-US dataset with no property graphs to choose from.
    """
    location = getattr(dataset, "location", None)
    if location:
        return location
    return (getattr(dataset, "_properties", None) or {}).get("location")


@router.post("/api/google/bigquery/list_projects", response_model=List[BigQueryProject])
async def list_bigquery_projects(
    request: Request,
    _: str | None = Depends(verify_admin_token)
):
    """Google Cloud projects that hold BigQuery datasets, with those datasets."""
    try:
        body = await request.json()
        auth = body.get('auth', {})
        auth_type = body.get('auth_type', 'service_account')

        default_oauth = get_default_oauth_config() or {}
        credentials, project_id = get_google_credentials(
            {**default_oauth, **auth}, auth_type, scopes=BIGQUERY_SCOPES
        )

        is_service_account = credentials and isinstance(credentials, service_account.Credentials)
        candidates: List[BigQueryProject] = []
        if (is_service_account or auth_type == 'google_ADC') and project_id:
            candidates.append(BigQueryProject(name=project_id, id=project_id))
        else:
            client = resourcemanager_v3.ProjectsClient(credentials=credentials)
            for project in client.search_projects(request=resourcemanager_v3.SearchProjectsRequest()):
                candidates.append(
                    BigQueryProject(name=project.display_name or project.name, id=project.project_id)
                )

        def attach_datasets(project: BigQueryProject):
            """Keep a project only when it actually holds BigQuery data."""
            try:
                client = _bigquery_client(project.id, credentials)
                datasets = list(client.list_datasets(max_results=MAX_DATASETS_PER_PROJECT))
                if not datasets:
                    return None
                project.datasets = [
                    BigQueryDataset(
                        id=dataset.dataset_id,
                        name=dataset.dataset_id,
                        location=_dataset_location(dataset),
                    )
                    for dataset in datasets
                ]
                return project
            except (GoogleAPIError, Exception) as error:
                # A project the caller cannot read is skipped, not reported: the
                # search above returns every project they can see, most of which
                # have nothing to do with BigQuery.
                print(f"Error checking BigQuery project {project.id}: {error}")
                return None

        import concurrent.futures

        found: List[BigQueryProject] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            for result in executor.map(attach_datasets, candidates):
                if result:
                    found.append(result)

        return found

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/google/bigquery/list_graphs", response_model=List[GraphDatabase])
async def list_bigquery_graphs(
    request_data: Dict[str, Any],
    _: str | None = Depends(verify_admin_token)
):
    """The property graphs declared in one dataset."""
    try:
        auth = request_data.get('auth', {})
        auth_type = request_data.get('auth_type', 'service_account')
        dataset_id = auth.get('database_id')
        location = auth.get('location') or 'US'

        if not dataset_id:
            raise HTTPException(status_code=400, detail="database_id (dataset) is required")
        # The dataset name is interpolated into the statement, so it is validated
        # rather than trusted — the same check the driver applies.
        dataset_id = validate_identifier(dataset_id, "dataset")

        default_oauth = get_default_oauth_config() or {}
        credentials, resolved_project = get_google_credentials(
            {**default_oauth, **auth}, auth_type, scopes=BIGQUERY_SCOPES
        )
        project_id = auth.get('project_id') or resolved_project
        if not project_id:
            raise HTTPException(status_code=400, detail="Project ID not found")

        client = _bigquery_client(project_id, credentials, location)
        rows = client.query(
            f"SELECT property_graph_name AS name FROM `{dataset_id}.INFORMATION_SCHEMA.PROPERTY_GRAPHS`",
            location=location,
        ).result()

        return [GraphDatabase(id=row["name"], name=row["name"]) for row in rows]

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/google/spanner/callback")
async def google_spanner_callback(request: Request):
    try:
        host = request.headers.get('host')
        print(f"Login request from host: {host}")

        if(host.startswith("localhost") == False):
            raise HTTPException(status_code=400, detail="OAuth login only allowed from localhost")

        default_oauth = get_default_oauth_config()
        if default_oauth == {}:
            raise HTTPException(status_code=500, detail="OAuth configuration file not found. Please ensure config/default.google.localhost.oauth.json exists.")
        client_id = default_oauth.get("client_id")
        client_secret = default_oauth.get("client_secret")

       # Get query parameters from the request
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        error = request.query_params.get("error")

        if error:
            raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

        if not code:
            raise HTTPException(status_code=400, detail="Authorization code not found")

        # Exchange authorization code for access token
        redirect_uri = f"http://{host}/google/spanner/callback"
        token_url = "https://oauth2.googleapis.com/token"

        token_data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri
        }

        response = requests.post(token_url, data=token_data)

        if response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Token exchange failed: {response.text}")

        token_info = response.json()
        token = token_info.get("access_token")
        id_token = token_info.get("id_token")
        refresh_token = token_info.get("refresh_token")  # Add refresh_token
        expires_in = token_info.get("expires_in")

        if not token:
            raise HTTPException(status_code=500, detail="Access token not found in token response")
        
        # get user info
        userinfo_response = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {token}"}
        )

        if userinfo_response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Userinfo request failed: {userinfo_response.text}")

        user_info = userinfo_response.json()
        email = user_info.get("email", "unknown")   
        return HTMLResponse(content=f'''
        <html>
            <head>
                <title>Google OAuth2 Callback</title>
            </head>
        <body style="font-family: Arial, sans-serif; text-align: center; margin-top: 50px;">
            <h1>Google OAuth2 Successful</h1>
            <p>You can close this window.</p>
            <p>Current Login Email: {email}</p>
            {f'<p style="color: orange;">[WARN] Note: refresh_token not obtained, need to re-login after token expires</p>' if not refresh_token else '<p style="color: green;">[OK] refresh_token obtained, supports automatic refresh</p>'}
        <script>
            // Use new token management system
            const tokenData = {{
                "access_token": "{token}",
                "refresh_token": "{refresh_token or ''}",
                "id_token": "{id_token or ''}",
                "expires_in": {expires_in or 3600},
                "email": "{email}"
            }};
            
            // Save to new format
            localStorage.setItem('g_auth_info', JSON.stringify({{
                ...tokenData,
                "expires_at": Date.now() + (tokenData.expires_in * 1000)
            }}));
            
            // Compatible with old storage method
            localStorage.setItem("g_auth_token", "{token}");
            localStorage.setItem("g_auth_refresh_token", "{refresh_token or ''}");
            localStorage.setItem("g_auth_id_token", "{id_token or ''}");
            localStorage.setItem("g_auth_expires_in", "{expires_in}");
            localStorage.setItem("g_auth_state", "{state}");
            localStorage.setItem("g_auth_email", "{email}");
            
            // Display token validity information
            console.log('[OK] Authentication successful');
            console.log('[INFO] Token validity:', tokenData.expires_in, 'seconds');
            console.log('[INFO] Refresh token:', tokenData.refresh_token ? 'available' : 'not available');
            console.log('[INFO] Expiry time:', new Date(Date.now() + tokenData.expires_in * 1000).toLocaleString());
            
            if (!tokenData.refresh_token) {{
                console.warn('[WARN] Warning: refresh_token not obtained, need to re-login after token expires');
            }}
            
            setTimeout(() => {{
                window.close();
            }}, 3000); // Extended display time for user to see warning information
        </script>
        </body>
        </html>
        ''')
    except Exception as e:
        return HTMLResponse(content=f'''
        <html>
        <body>
            <h1>Google OAuth2 Error</h1>
            <p>{str(e)}</p>
        <script>
            console.error("OAuth callback error: {str(e)}");
            window.close();
        </script>
        </body>
        </html>
        ''')


@router.get("/google/spanner/login")
async def google_spanner_login(request: Request):
    """Initiate the Google OAuth2 login flow. `?service=bigquery` asks for BigQuery scopes."""
    try:
        host = request.headers.get('host')
        if(host.startswith("localhost") == False):
            raise HTTPException(status_code=400, detail="OAuth login only allowed from localhost")
        # Read from config/default.google.localhost.oauth.json
        try:
            default_oauth = get_default_oauth_config()
            if default_oauth == {}:
                raise HTTPException(status_code=500, detail="OAuth configuration file not found. Please ensure config/default.google.localhost.oauth.json exists or provide OAuth configuration through environment variables.")
            
            client_id = default_oauth.get("client_id")
            
            if not client_id:
                raise HTTPException(status_code=500, detail="client_id not found in config file")
        except HTTPException:
            raise  # Re-raise HTTPException as-is
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"OAuth configuration error: {e}")

        # Which API the consent is for. Spanner stays the default so an existing
        # link keeps asking for exactly the scopes it always did; a BigQuery
        # project asks for BigQuery's instead of adding them to everyone's consent
        # screen. The callback path is shared because it is the one registered
        # with Google as a redirect URI.
        service = request.query_params.get("service", "spanner")
        scopes = BIGQUERY_SCOPES if service == "bigquery" else SPANNER_SCOPES
        redirect_uri = f"http://{host}/google/spanner/callback"

        # auto redirect to the OAuth2 authorization URL
        auth_url = (
            f"https://accounts.google.com/o/oauth2/v2/auth?"
            f"client_id={client_id}&"
            f"response_type=code&"
            f"scope={'+'.join(scopes)}&"
            f"access_type=offline&"
            f"prompt=consent&"
            f"redirect_uri={redirect_uri}&"
            f"state={service}"
        )

        return RedirectResponse(url=auth_url)
    
    except Exception as e:
       return {"error": str(e)}


@router.post("/api/google/refresh-token")
async def refresh_google_token(
    request: Request,
    _: str | None = Depends(verify_admin_token)
):
    """Refresh Google OAuth2 access token"""
    try:
        body = await request.json()
        refresh_token = body.get("refresh_token")
        
        if not refresh_token:
            raise HTTPException(status_code=400, detail="Refresh token is required")
        
        # Get OAuth configuration
        default_oauth = get_default_oauth_config()
        client_id = default_oauth.get("client_id")
        client_secret = default_oauth.get("client_secret")
        
        if not client_id or not client_secret:
            raise HTTPException(status_code=500, detail="OAuth configuration not found")
        
        # Call Google's token refresh endpoint
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }
        
        response = requests.post(token_url, data=token_data)
        
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Token refresh failed: {response.text}")
        
        token_info = response.json()
        
        # Return new access token information
        return {
            "access_token": token_info.get("access_token"),
            "id_token": token_info.get("id_token"),
            "expires_in": token_info.get("expires_in", 3600),
            "token_type": token_info.get("token_type", "Bearer")
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token refresh error: {str(e)}")