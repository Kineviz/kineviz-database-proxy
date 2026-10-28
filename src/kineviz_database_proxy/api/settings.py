"""
Settings management API endpoints

These endpoints require admin authentication when ADMIN_PASSWORD is set.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from ..services.settings_service import SettingsService
from .auth import invalidate_settings_cache, verify_admin_token

router = APIRouter(prefix="/api/settings", tags=["settings"])


# Request/Response models
class SettingsResponse(BaseModel):
    api_key: Optional[str] = None
    api_key_enabled: bool = False
    api_key_env_configured: bool = False


class SettingsUpdateRequest(BaseModel):
    api_key_enabled: bool = False


# Dependency to get settings service
def get_settings_service() -> SettingsService:
    return SettingsService()


@router.get("", response_model=SettingsResponse)
async def get_settings(_: str | None = Depends(verify_admin_token)):
    """
    Get current settings.
    
    Returns the current API key configuration including:
    - api_key: The configured API key (if any)
    - api_key_enabled: Whether API key authentication is enabled
    - api_key_env_configured: Whether API_KEY env var is set (disables UI editing)
    """
    try:
        service = get_settings_service()
        settings = service.get_settings()
        return SettingsResponse(**settings)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("", response_model=SettingsResponse)
async def update_settings(
    request: SettingsUpdateRequest,
    _: str | None = Depends(verify_admin_token)
):
    """
    Update settings.
    
    Updates the API key enabled state. Returns 403 if API_KEY env var is set,
    as the configuration cannot be changed when using environment variable.
    """
    try:
        service = get_settings_service()
        
        # Check if env var is configured - cannot override via UI
        if service.is_api_key_env_configured():
            raise HTTPException(
                status_code=403,
                detail="API Key is configured via environment variable and cannot be changed"
            )
        
        settings = service.update_enabled(api_key_enabled=request.api_key_enabled)
        invalidate_settings_cache()
        return SettingsResponse(**settings)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-key", response_model=SettingsResponse)
async def generate_api_key(_: str | None = Depends(verify_admin_token)):
    """
    Generate a new random API key and save it.
    
    Generates a new API key with the 'gxr_' prefix and saves it immediately.
    Returns the full settings response.
    """
    try:
        service = get_settings_service()
        
        # Check if env var is configured
        if service.is_api_key_env_configured():
            raise HTTPException(
                status_code=403,
                detail="API Key is configured via environment variable and cannot be changed"
            )
        
        settings = service.generate_and_save_api_key()
        invalidate_settings_cache()
        return SettingsResponse(**settings)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
