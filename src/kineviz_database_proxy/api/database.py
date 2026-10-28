"""
Database API endpoints
"""

from datetime import datetime
from typing import Any, Dict
from fastapi import APIRouter, HTTPException, Depends, Path, Body, Query
from ..models.project import (
    APIInfo,
    DatabaseConfig,
    DatabaseType,
    GraphSchemaResponse,
    Project,
    QueryRequest,
    QueryResponse,
    SampleDataResponse,
    SchemaResponse,
)
from ..services.project_service import ProjectService
from ..drivers.factory import DriverFactory
from .auth import verify_api_key_or_admin

router = APIRouter(prefix="/api", tags=["database"])

def get_project_service() -> ProjectService:
    return ProjectService()


@router.get("/{database_type}/{project_name}", response_model=APIInfo)
async def get_database_info(
    database_type: DatabaseType = Path(..., description="Database type"),
    project_name: str = Path(..., description="Project name"),
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_api_key_or_admin)
):
    """Get database API information"""
    try:
        # Find project by name
        project = await service.get_project_by_name(project_name)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project.database_type != database_type:
            raise HTTPException(
                status_code=400, 
                detail=f"Project database type {project.database_type} does not match requested type {database_type}"
            )
        
        # Create driver and get API info
        driver = DriverFactory.create_driver(project)
        api_info = driver.get_api_info(project.name)
        
        return APIInfo(
            type=database_type,
            api_urls=api_info["api_urls"],
            version=api_info.get("version")
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{database_type}/{project_name}/query", response_model=QueryResponse)
async def execute_query(
    database_type: DatabaseType = Path(..., description="Database type"),
    project_name: str = Path(..., description="Project name"),
    query_request: QueryRequest = ...,
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_api_key_or_admin)
):
    """Execute a database query"""
    try:
        # Find project by name
        project = await service.get_project_by_name(project_name)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project.database_type != database_type:
            raise HTTPException(
                status_code=400,
                detail=f"Project database type {project.database_type} does not match requested type {database_type}"
            )
        
        # Create driver and execute query
        driver = DriverFactory.create_driver(project)
        await driver.connect()
        
        try:
            result = await driver.execute_query(
                query_request.query,
                query_request.parameters
            )
            return result
        finally:
            await driver.disconnect()
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{database_type}/{project_name}/schema", response_model=SchemaResponse)
async def get_schema(
    database_type: DatabaseType = Path(..., description="Database type"),
    project_name: str = Path(..., description="Project name"),
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_api_key_or_admin)
):
    """Get database schema"""
    try:
        # Find project by name
        project = await service.get_project_by_name(project_name)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project.database_type != database_type:
            raise HTTPException(
                status_code=400,
                detail=f"Project database type {project.database_type} does not match requested type {database_type}"
            )
        
        # Create driver and get schema
        driver = DriverFactory.create_driver(project)
        await driver.connect()
        
        try:
            result = await driver.get_schema()
            return result
        finally:
            await driver.disconnect()
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{database_type}/{project_name}/token-status")
async def get_token_status(
    database_type: DatabaseType = Path(..., description="Database type"),
    project_name: str = Path(..., description="Project name"),
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_api_key_or_admin)
):
    """Get OAuth token status information"""
    try:
        # Find project by name
        project = await service.get_project_by_name(project_name)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project.database_type != database_type:
            raise HTTPException(
                status_code=400,
                detail=f"Project database type {project.database_type} does not match requested type {database_type}"
            )
        
        # Create driver and get token status
        driver = DriverFactory.create_driver(project)
        if hasattr(driver, 'get_token_status'):
            token_status = driver.get_token_status()
            return {
                "success": True,
                "data": token_status
            }
        else:
            return {
                "success": False,
                "error": "Token status not available for this database type"
            }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{database_type}/test_config")
async def test_connection_with_config(
    database_type: DatabaseType = Path(..., description="Database type"),
    config: DatabaseConfig = Body(..., description="Database config to probe (not persisted)"),
    _: str | None = Depends(verify_api_key_or_admin),
):
    """Test a database connection using a config supplied in the request body.

    Unlike POST /{database_type}/{project_name}/test (which looks up a saved
    project), this endpoint is meant for the create-new-project flow where
    nothing has been persisted yet. The config is used to build a transient
    driver, run test_connection(), and discarded.
    """
    if config.type != database_type:
        raise HTTPException(
            status_code=400,
            detail=f"config.type {config.type} does not match requested type {database_type}",
        )

    transient_project = Project(
        id="__transient_test__",
        name="__transient_test__",
        database_type=database_type,
        database_config=config,
        create_time=datetime.utcnow(),
        update_time=datetime.utcnow(),
    )

    try:
        driver = DriverFactory.create_driver(transient_project)
        try:
            is_connected = await driver.test_connection()
        finally:
            try:
                await driver.disconnect()
            except Exception:
                pass
        return {
            "success": is_connected,
            "message": "Connection successful" if is_connected else "Connection failed",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{database_type}/{project_name}/test")
async def test_connection(
    database_type: DatabaseType = Path(..., description="Database type"),
    project_name: str = Path(..., description="Project name"),
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_api_key_or_admin)
):
    """Test database connection"""
    try:
        # Find project by name
        project = await service.get_project_by_name(project_name)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project.database_type != database_type:
            raise HTTPException(
                status_code=400,
                detail=f"Project database type {project.database_type} does not match requested type {database_type}"
            )
        
        # Create driver and test connection
        driver = DriverFactory.create_driver(project)
        is_connected = await driver.test_connection()
        
        return {
            "success": is_connected,
            "message": "Connection successful" if is_connected else "Connection failed"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{database_type}/{project_name}/graphSchema", response_model=GraphSchemaResponse)
async def get_graph_schema(
    database_type: DatabaseType = Path(..., description="Database type"),
    project_name: str = Path(..., description="Project name"),
    refresh: bool = Query(False, description="Re-probe the store instead of serving the cached schema"),
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_api_key_or_admin)
):
    """Get graph database schema.

    Served from a short-lived cache — see ``services/graph_schema_cache.py`` —
    because the probe is the expensive part of this endpoint on every backend and
    a schema changes about as often as someone alters a table. Pass
    ``?refresh=true`` after changing one.
    """
    try:
        # Find project by name
        project = await service.get_project_by_name(project_name)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project.database_type != database_type:
            raise HTTPException(
                status_code=400,
                detail=f"Project database type {project.database_type} does not match requested type {database_type}"
            )
        
        # Create driver and get graph schema
        driver = DriverFactory.create_driver(project)
        await driver.connect()
        
        try:
            return await driver.get_graph_schema_cached(refresh=refresh)
        finally:
            await driver.disconnect()
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{database_type}/{project_name}/sampleData", response_model=SampleDataResponse)
async def get_sample_data(
    database_type: DatabaseType = Path(..., description="Database type"),
    project_name: str = Path(..., description="Project name"),
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_api_key_or_admin)
):
    """Get sample data from database"""
    try:
        # Find project by name
        project = await service.get_project_by_name(project_name)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        if project.database_type != database_type:
            raise HTTPException(
                status_code=400,
                detail=f"Project database type {project.database_type} does not match requested type {database_type}"
            )
        
        # Create driver and get sample data
        driver = DriverFactory.create_driver(project)
        await driver.connect()
        
        try:
            result = await driver.get_sample_data()
            return result
        finally:
            await driver.disconnect()
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------------------------
# Typed graph intents
#
# These move statement generation out of the browser and into the driver, so the
# GraphXR client does not need a dialect per backend. A client talking to a proxy
# that predates them gets a 404 from /capabilities and falls back to building the
# statement itself.
#
# See ai/graph-db-adapter-refactoring-spec.md section 8.3 in graphxr-dev.
# ---------------------------------------------------------------------------

from ..contract import (  # noqa: E402  (kept with the endpoints it serves)
    CapabilityReport,
    ExpandRequest,
    FulltextSearchRequest,
    PullCategoryRequest,
    PullRelationshipRequest,
)


async def _driver_for(database_type: DatabaseType, project_name: str, service: ProjectService):
    """Resolve a project to a connected driver, or raise the right HTTP error."""
    project = await service.get_project_by_name(project_name)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.database_type != database_type:
        raise HTTPException(
            status_code=400,
            detail=f"Project database type {project.database_type} does not match requested type {database_type}",
        )
    driver = DriverFactory.create_driver(project)
    await driver.connect()
    return driver


def _require_intent(driver, intent: str):
    """A driver that has not implemented an intent says so with a 501, not a 500."""
    capabilities = getattr(driver, "graph_capabilities", None)
    if capabilities is None or intent not in (capabilities.intents or []):
        raise HTTPException(
            status_code=501,
            detail=f"This database type does not implement the '{intent}' intent",
        )


@router.get("/{database_type}/{project_name}/capabilities", response_model=CapabilityReport)
async def get_capabilities(
    database_type: DatabaseType = Path(..., description="Database type"),
    project_name: str = Path(..., description="Project name"),
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_api_key_or_admin),
):
    """What this project's backend can do, and which intents it implements."""
    project = await service.get_project_by_name(project_name)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    driver = DriverFactory.create_driver(project)
    capabilities = getattr(driver, "graph_capabilities", None)
    if capabilities is None:
        # A driver from before the contract: report the type and nothing else, so
        # the client uses its legacy path instead of guessing.
        return CapabilityReport(type=str(database_type))
    return CapabilityReport(**capabilities.model_dump(by_alias=True))


@router.post("/{database_type}/{project_name}/expand", response_model=QueryResponse)
async def expand(
    database_type: DatabaseType = Path(..., description="Database type"),
    project_name: str = Path(..., description="Project name"),
    request: ExpandRequest = ...,
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_api_key_or_admin),
):
    """Expand the neighbourhood of a set of already-loaded nodes."""
    driver = await _driver_for(database_type, project_name, service)
    try:
        _require_intent(driver, "expand")
        return QueryResponse(success=True, data=await driver.expand(request))
    finally:
        await driver.disconnect()


@router.post("/{database_type}/{project_name}/pullCategory", response_model=QueryResponse)
async def pull_category(
    database_type: DatabaseType = Path(..., description="Database type"),
    project_name: str = Path(..., description="Project name"),
    request: PullCategoryRequest = ...,
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_api_key_or_admin),
):
    """Load more nodes of one category, excluding what the canvas already has."""
    driver = await _driver_for(database_type, project_name, service)
    try:
        _require_intent(driver, "pullCategory")
        return QueryResponse(success=True, data=await driver.pull_category(request))
    finally:
        await driver.disconnect()


@router.post("/{database_type}/{project_name}/pullRelationship", response_model=QueryResponse)
async def pull_relationship(
    database_type: DatabaseType = Path(..., description="Database type"),
    project_name: str = Path(..., description="Project name"),
    request: PullRelationshipRequest = ...,
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_api_key_or_admin),
):
    """Load more relationships of one type."""
    driver = await _driver_for(database_type, project_name, service)
    try:
        _require_intent(driver, "pullRelationship")
        return QueryResponse(success=True, data=await driver.pull_relationship(request))
    finally:
        await driver.disconnect()


@router.post("/{database_type}/{project_name}/search", response_model=QueryResponse)
async def fulltext_search(
    database_type: DatabaseType = Path(..., description="Database type"),
    project_name: str = Path(..., description="Project name"),
    request: FulltextSearchRequest = ...,
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_api_key_or_admin),
):
    """Full-text search, for backends whose driver declares the capability."""
    driver = await _driver_for(database_type, project_name, service)
    try:
        _require_intent(driver, "search")
        return QueryResponse(success=True, data=await driver.fulltext_search(request))
    finally:
        await driver.disconnect()
