"""
Admin authentication API endpoints

Provides login/logout functionality for the management UI.
"""

from fastapi import APIRouter, HTTPException, Security
from pydantic import BaseModel
from .auth import (
    is_admin_auth_enabled,
    verify_admin_password,
    create_admin_session,
    invalidate_admin_session,
    ADMIN_TOKEN_HEADER,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    success: bool
    token: str | None = None
    message: str | None = None


class AuthStatusResponse(BaseModel):
    admin_auth_enabled: bool
    authenticated: bool


@router.get("/status", response_model=AuthStatusResponse)
async def get_auth_status(token: str = Security(ADMIN_TOKEN_HEADER)):
    """
    Check authentication status.
    
    Returns whether admin auth is enabled and if the current session is valid.
    """
    from .auth import _admin_sessions
    import time
    
    admin_auth_enabled = is_admin_auth_enabled()
    
    # Check if token is valid
    authenticated = False
    if not admin_auth_enabled:
        # No auth required, consider authenticated
        authenticated = True
    elif token:
        expiry = _admin_sessions.get(token)
        if expiry and expiry >= time.time():
            authenticated = True
    
    return AuthStatusResponse(
        admin_auth_enabled=admin_auth_enabled,
        authenticated=authenticated
    )


@router.post("/login", response_model=LoginResponse)
async def admin_login(request: LoginRequest):
    """
    Login with admin password.
    
    Returns a session token if successful.
    """
    if not is_admin_auth_enabled():
        return LoginResponse(
            success=True,
            message="Admin authentication is disabled"
        )
    
    if not verify_admin_password(request.password):
        raise HTTPException(
            status_code=401,
            detail="Invalid password"
        )
    
    token = create_admin_session()
    return LoginResponse(
        success=True,
        token=token,
        message="Login successful"
    )


@router.post("/logout")
async def admin_logout(token: str = Security(ADMIN_TOKEN_HEADER)):
    """
    Logout and invalidate the session token.
    """
    if token:
        invalidate_admin_session(token)
    return {"success": True, "message": "Logged out successfully"}
