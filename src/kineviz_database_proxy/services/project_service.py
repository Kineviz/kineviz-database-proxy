"""
Project service for managing projects and their configurations
"""

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..models.project import Project, ProjectCreate, ProjectUpdate


class ProjectService:
    """Service for managing projects"""
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.projects_file = self.config_dir / "projects.json"
        self._ensure_config_dir()
    
    def _ensure_config_dir(self) -> None:
        """Ensure config directory exists"""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        if not self.projects_file.exists():
            self._save_projects({})
    
    def _load_projects(self) -> Dict[str, Dict]:
        """Load projects from config file"""
        try:
            with open(self.projects_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def _save_projects(self, projects: Dict[str, Dict]) -> None:
        """Save projects to config file"""
        with open(self.projects_file, 'w', encoding='utf-8') as f:
            json.dump(projects, f, indent=2, ensure_ascii=False, default=str)
    
    async def create_project(self, project_data: ProjectCreate) -> Project:
        """Create a new project"""
        name = project_data.name or str(uuid.uuid4())
        project_id = str(uuid.uuid4())
        now = datetime.utcnow()

        ## name should be unique and only allow alphanumeric, underscore, hyphen
        ## Auto remove invalid characters
        name = re.sub(r'[^a-zA-Z0-9_-]', '', name)
        exist_project = await self.get_project_by_name(name)
        if exist_project:
            #update existing project
            await self.update_project(exist_project.id, ProjectUpdate(
                name=project_data.name,
                database_type=project_data.database_type,
                database_config=project_data.database_config
            ))
            return exist_project

        project = Project(
            id=project_id,
            name=name,
            database_type=project_data.database_type,
            database_config=project_data.database_config,
            create_time=now,
            update_time=now
        )
        
        projects = self._load_projects()
        projects[project_id] = project.model_dump()
        self._save_projects(projects)
        
        return project
    
    async def get_project(self, project_id: str) -> Optional[Project]:
        """Get a project by ID"""
        projects = self._load_projects()
        project_data = projects.get(project_id)
        
        if project_data:
            return Project(**project_data)
        return None
    
    async def get_project_by_name(self, name: str) -> Optional[Project]:
        """Get a project by name"""
        projects = self._load_projects()
        for project_data in projects.values():
            if project_data.get('name') == name:
                return Project(**project_data)
        return None
    
    async def list_projects(self) -> List[Project]:
        """List all projects"""
        projects = self._load_projects()
        return [Project(**project_data) for project_data in projects.values()]
    
    async def update_project(self, project_id: str, update_data: ProjectUpdate) -> Optional[Project]:
        """Update a project"""
        projects = self._load_projects()
        project_data = projects.get(project_id)
        
        if not project_data:
            return None
        
        # Update fields
        if update_data.name is not None:
            project_data['name'] = update_data.name
        if update_data.database_config is not None:
            project_data['database_config'] = update_data.database_config.model_dump()
        
        project_data['update_time'] = datetime.utcnow().isoformat()
        
        projects[project_id] = project_data
        self._save_projects(projects)
        
        return Project(**project_data)
    
    async def update_project_token(self, project_id: str, token: str, last_refreshed: float, expires_in: Optional[int] = None) -> Optional[Project]:
        """Update project OAuth token and last refreshed timestamp"""
        projects = self._load_projects()
        project_data = projects.get(project_id)
        
        if not project_data:
            return None
        
        # Update OAuth config token fields
        if 'database_config' in project_data and 'oauth_config' in project_data['database_config']:
            oauth_config = project_data['database_config']['oauth_config']
            oauth_config['token'] = token
            oauth_config['last_refreshed'] = last_refreshed
            if expires_in is not None:
                oauth_config['expires_in'] = expires_in
        
        project_data['update_time'] = datetime.utcnow().isoformat()
        
        projects[project_id] = project_data
        self._save_projects(projects)
        
        return Project(**project_data)
    
    async def delete_project(self, project_id: str) -> bool:
        """Delete a project"""
        projects = self._load_projects()
        if project_id in projects:
            del projects[project_id]
            self._save_projects(projects)
            return True
        return False