"""
Authentication module

Provides two types of authentication:

1. Admin Password Authentication (for management UI):
   - Configured via ADMIN_PASSWORD environment variable
   - Uses session tokens after login
   - Protects project management, settings, and Google API endpoints

2. API Key Authentication (for external API access):
   - Configured via API_KEY env var or settings file
   - Protects database query endpoints
   - Used by external applications like GraphXR
"""

import os
import secrets
import time
from typing import Optional, Dict
from fastapi import HTTPException, Security, status, Depends
from fastapi.security import APIKeyHeader

from ..services.settings_service import SettingsService

# =============================================================================
# Admin Password Authentication (for management UI)
# =============================================================================

# Session storage (in-memory, cleared on restart)
# In production, consider using Redis or JWT tokens
_admin_sessions: Dict[str, float] = {}  # token -> expiry_timestamp
_SESSION_DURATION_SECONDS = 24 * 60 * 60  # 24 hours

# Admin token header
ADMIN_TOKEN_HEADER = APIKeyHeader(name="X-Admin-Token", auto_error=False)


def is_admin_auth_enabled() -> bool:
    """Check if admin password authentication is enabled."""
    return bool(os.environ.get("ADMIN_PASSWORD"))


def verify_admin_password(password: str) -> bool:
    """
    Verify admin password using constant-time comparison.
    
    Returns True if password matches, False otherwise.
    """
    configured_password = os.environ.get("ADMIN_PASSWORD")
    if not configured_password:
        return False
    return secrets.compare_digest(password, configured_password)


def create_admin_session() -> str:
    """
    Create a new admin session and return the session token.
    """
    token = f"admin_{secrets.token_urlsafe(32)}"
    expiry = time.time() + _SESSION_DURATION_SECONDS
    _admin_sessions[token] = expiry
    
    # Clean up expired sessions
    _cleanup_expired_sessions()
    
    return token


def _cleanup_expired_sessions() -> None:
    """Remove expired sessions from storage."""
    now = time.time()
    expired = [token for token, expiry in _admin_sessions.items() if expiry < now]
    for token in expired:
        del _admin_sessions[token]


def invalidate_admin_session(token: str) -> None:
    """Invalidate an admin session (logout)."""
    _admin_sessions.pop(token, None)


async def verify_admin_token(token: str = Security(ADMIN_TOKEN_HEADER)) -> str | None:
    """
    Verify admin session token for management endpoints.
    
    If ADMIN_PASSWORD is not set, authentication is disabled and all requests
    are allowed through (for local development).
    
    Args:
        token: The admin token from the X-Admin-Token header
        
    Returns:
        The validated token, or None if admin auth is disabled
        
    Raises:
        HTTPException: 401 Unauthorized if token is missing or invalid
    """
    # If no admin password configured, authentication is disabled
    if not is_admin_auth_enabled():
        return None
    
    # Admin password is configured - require valid session token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
            headers={"WWW-Authenticate": "AdminToken"}
        )
    
    # Check if token exists and is not expired
    expiry = _admin_sessions.get(token)
    if not expiry or expiry < time.time():
        # Clean up if expired
        _admin_sessions.pop(token, None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired admin session",
            headers={"WWW-Authenticate": "AdminToken"}
        )
    
    return token


# =============================================================================
# API Key Authentication (for external API access)
# =============================================================================

# Define the API key header - auto_error=False allows us to handle missing keys ourselves
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# Settings cache to avoid reading JSON file on every request
_settings_cache: dict = {
    "api_key": None,
    "api_key_enabled": False,
    "last_loaded": 0.0,
    "file_mtime": 0.0
}
_CACHE_TTL_SECONDS = 5.0  # Re-check file every 5 seconds


def _get_cached_api_key() -> Optional[str]:
    """
    Get API key from settings with caching.
    
    Caches settings and only reloads when:
    - Cache TTL has expired AND
    - Settings file has been modified
    """
    global _settings_cache
    
    now = time.time()
    
    # Check if cache needs refresh
    if now - _settings_cache["last_loaded"] > _CACHE_TTL_SECONDS:
        service = SettingsService()
        
        # Check if file was modified
        try:
            file_mtime = service.settings_file.stat().st_mtime
        except OSError:
            file_mtime = 0.0
        
        if file_mtime != _settings_cache["file_mtime"]:
            # Reload settings
            settings = service._load_settings()
            _settings_cache["api_key"] = settings.get("api_key") if settings.get("api_key_enabled") else None
            _settings_cache["api_key_enabled"] = settings.get("api_key_enabled", False)
            _settings_cache["file_mtime"] = file_mtime
        
        _settings_cache["last_loaded"] = now
    
    return _settings_cache["api_key"]


def _verify_key(provided_key: Optional[str], configured_key: str) -> bool:
    """
    Verify API key using constant-time comparison to prevent timing attacks.
    
    Args:
        provided_key: The key provided in the request
        configured_key: The configured API key to compare against
        
    Returns:
        True if keys match, False otherwise
    """
    if not provided_key:
        return False
    return secrets.compare_digest(provided_key, configured_key)


async def verify_api_key(api_key: str = Security(API_KEY_HEADER)) -> str | None:
    """
    Verify API key if configured.
    
    Checks for API key configuration in this order:
    1. API_KEY environment variable (takes precedence)
    2. Settings file (config/settings.json) - with caching
    
    If neither is configured, authentication is disabled and all requests
    are allowed through.
    
    Args:
        api_key: The API key from the X-API-Key header
        
    Returns:
        The validated API key, or None if auth is disabled
        
    Raises:
        HTTPException: 401 Unauthorized if key is missing or invalid
    """
    # First check environment variable (takes precedence)
    env_key = os.environ.get("API_KEY")
    if env_key:
        if not _verify_key(api_key, env_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "ApiKey"}
            )
        return api_key
    
    # Fall back to cached settings file
    configured_key = _get_cached_api_key()
    
    if configured_key:
        if not _verify_key(api_key, configured_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "ApiKey"}
            )
        return api_key
    
    # No API key configured - authentication is disabled
    return None


def invalidate_settings_cache() -> None:
    """
    Invalidate the settings cache.
    Call this after updating settings to ensure changes take effect immediately.
    """
    global _settings_cache
    _settings_cache["last_loaded"] = 0.0
    _settings_cache["file_mtime"] = 0.0


async def verify_api_key_or_admin(
    api_key: str = Security(API_KEY_HEADER),
    admin_token: str = Security(ADMIN_TOKEN_HEADER)
) -> str | None:
    """
    Verify either API key OR admin token for database endpoints.
    
    This allows:
    - External applications to use API key (X-API-Key header)
    - Management UI to use admin token (X-Admin-Token header)
    
    If neither API key nor admin auth is configured, all requests are allowed.
    
    Args:
        api_key: The API key from the X-API-Key header
        admin_token: The admin token from the X-Admin-Token header
        
    Returns:
        The validated key/token, or None if auth is disabled
        
    Raises:
        HTTPException: 401 Unauthorized if authentication fails
    """
    # Check if admin token is valid (if admin auth is enabled)
    if is_admin_auth_enabled() and admin_token:
        expiry = _admin_sessions.get(admin_token)
        if expiry and expiry >= time.time():
            # Valid admin session - allow access
            return admin_token
    
    # Check API key (environment variable first)
    env_key = os.environ.get("API_KEY")
    if env_key:
        if _verify_key(api_key, env_key):
            return api_key
        # API key is configured but not provided/invalid
        # Only fail if admin auth also failed
        if not is_admin_auth_enabled():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "ApiKey"}
            )
    
    # Check API key from settings file
    configured_key = _get_cached_api_key()
    if configured_key:
        if _verify_key(api_key, configured_key):
            return api_key
        # API key is configured but not provided/invalid
        # Only fail if admin auth also failed
        if not is_admin_auth_enabled():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
                headers={"WWW-Authenticate": "ApiKey"}
            )
    
    # If we get here, check if any auth is required
    if is_admin_auth_enabled():
        # Admin auth is enabled but no valid admin token was provided
        # And no valid API key was provided either
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required (API key or admin login)",
            headers={"WWW-Authenticate": "ApiKey, AdminToken"}
        )
    
    # No authentication configured - allow access
    return None
