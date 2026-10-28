"""
Project management API endpoints

These endpoints require admin authentication when ADMIN_PASSWORD is set.
"""

from typing import List
from fastapi import APIRouter, HTTPException, Depends
from ..models.project import Project, ProjectCreate, ProjectUpdate
from ..services.graph_schema_cache import GRAPH_SCHEMA_CACHE
from ..services.project_service import ProjectService
from .auth import verify_admin_token

router = APIRouter(prefix="/api/project", tags=["projects"])

# Dependency to get project service
def get_project_service() -> ProjectService:
    return ProjectService()


@router.post("/create", response_model=Project)
async def create_project(
    project_data: ProjectCreate,
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_admin_token)
):
    """Create a new project"""
    try:
        # Check if project with same name already exists
        existing = await service.get_project_by_name(project_data.name)
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Project with name '{project_data.name}' already exists"
            )
        
        project = await service.create_project(project_data)
        return project
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list", response_model=List[Project])
async def list_projects(
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_admin_token)
):
    """List all projects"""
    try:
        projects = await service.list_projects()
        return projects
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{project_id}", response_model=Project)
async def get_project(
    project_id: str,
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_admin_token)
):
    """Get a project by ID"""
    try:
        project = await service.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update", response_model=Project)
async def update_project(
    project_id: str,
    update_data: ProjectUpdate,
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_admin_token)
):
    """Update a project"""
    try:
        project = await service.update_project(project_id, update_data)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete")
async def delete_project(
    project_id: str,
    service: ProjectService = Depends(get_project_service),
    _: str | None = Depends(verify_admin_token)
):
    """Delete a project"""
    try:
        success = await service.delete_project(project_id)
        if not success:
            raise HTTPException(status_code=404, detail="Project not found")
        # The cache keys by ``project_id``; an entry for a deleted project survives
        # until TTL (``graph_schema_cache.DEFAULT_TTL_SECONDS``) otherwise, and a
        # later re-creation with the same id would silently answer from the stale
        # schema. Invalidate on the only path that frees the name.
        GRAPH_SCHEMA_CACHE.invalidate_project(project_id)
        return {"message": "Project deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))