"""Tests for RocketGraph AuthClient (login, token caching, refresh)."""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from kineviz_database_proxy.drivers.rocketgraph import AuthClient
from kineviz_database_proxy.models.project import (
    AuthType,
    DatabaseConfig,
    DatabaseType,
    OAuthConfig,
    Project,
)


def _make_project(
    auth_type=AuthType.USERNAME_PASSWORD,
    token=None,
    last_refreshed=None,
    expires_in=None,
    username="alice",
    password="secret",
):
    return Project(
        id="p-1",
        name="test-project",
        database_type=DatabaseType.ROCKETGRAPH,
        database_config=DatabaseConfig(
            type=DatabaseType.ROCKETGRAPH,
            host="example.com",
            port=4368,
            graph_name="social",
            auth_type=auth_type,
            username=username,
            password=password,
            oauth_config=OAuthConfig(
                token=token,
                last_refreshed=last_refreshed,
                expires_in=expires_in,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_bearer_token_returns_static_token():
    project = _make_project(auth_type=AuthType.BEARER_TOKEN, token="static-token-xyz")
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    token = await client.get_token()

    assert token == "static-token-xyz"


@pytest.mark.asyncio
async def test_bearer_token_does_not_call_login():
    project = _make_project(auth_type=AuthType.BEARER_TOKEN, token="static-token")
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    with patch("httpx.AsyncClient.post") as mock_post:
        await client.get_token()
        mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_username_password_login_when_no_token():
    project = _make_project(auth_type=AuthType.USERNAME_PASSWORD, token=None)
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "new-jwt-token",
        "expires_in": 3600,
    }
    mock_response.raise_for_status = MagicMock()

    mock_project_service = MagicMock()
    mock_project_service.update_project_token = AsyncMock()

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        with patch("kineviz_database_proxy.drivers.rocketgraph.ProjectService", return_value=mock_project_service):
            token = await client.get_token()

    assert token == "new-jwt-token"
    mock_project_service.update_project_token.assert_called_once()
    call_kwargs = mock_project_service.update_project_token.call_args.kwargs
    assert call_kwargs["project_id"] == "p-1"
    assert call_kwargs["token"] == "new-jwt-token"
    assert call_kwargs["expires_in"] == 3600


@pytest.mark.asyncio
async def test_cached_token_used_when_not_expired():
    """If token was refreshed recently and is not near expiry, reuse it without login."""
    now = time.time()
    project = _make_project(
        auth_type=AuthType.USERNAME_PASSWORD,
        token="cached-token",
        last_refreshed=now - 60,  # 1 minute ago
        expires_in=3600,           # 1 hour validity
    )
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    with patch("httpx.AsyncClient.post") as mock_post:
        token = await client.get_token()
        mock_post.assert_not_called()

    assert token == "cached-token"


@pytest.mark.asyncio
async def test_expired_token_triggers_relogin():
    """Token near expiry (within 5 min buffer) triggers re-login."""
    now = time.time()
    project = _make_project(
        auth_type=AuthType.USERNAME_PASSWORD,
        token="old-token",
        last_refreshed=now - 3500,  # 58 min ago
        expires_in=3600,             # expires in 100 sec → within buffer
    )
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "fresh-token", "expires_in": 3600}
    mock_response.raise_for_status = MagicMock()

    mock_project_service = MagicMock()
    mock_project_service.update_project_token = AsyncMock()

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        with patch("kineviz_database_proxy.drivers.rocketgraph.ProjectService", return_value=mock_project_service):
            token = await client.get_token()

    assert token == "fresh-token"


@pytest.mark.asyncio
async def test_username_password_calls_correct_endpoint():
    """Verify login posts to /auth/xgt/basic with username/password JSON body."""
    project = _make_project(auth_type=AuthType.USERNAME_PASSWORD, token=None)
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "tok", "expires_in": 3600}
    mock_response.raise_for_status = MagicMock()
    mock_post = AsyncMock(return_value=mock_response)

    mock_project_service = MagicMock()
    mock_project_service.update_project_token = AsyncMock()

    with patch("httpx.AsyncClient.post", new=mock_post):
        with patch("kineviz_database_proxy.drivers.rocketgraph.ProjectService", return_value=mock_project_service):
            await client.get_token()

    # First positional arg is URL
    assert mock_post.call_args.args[0].endswith("/auth/xgt/basic")
    # JSON body has correct credentials
    assert mock_post.call_args.kwargs["json"] == {"username": "alice", "password": "secret"}


@pytest.mark.asyncio
async def test_invalidate_forces_refresh():
    """After invalidate(), next get_token re-logs in even if cached token would be valid."""
    now = time.time()
    project = _make_project(
        auth_type=AuthType.USERNAME_PASSWORD,
        token="cached",
        last_refreshed=now - 60,
        expires_in=3600,
    )
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"access_token": "after-invalidate", "expires_in": 3600}
    mock_response.raise_for_status = MagicMock()

    mock_project_service = MagicMock()
    mock_project_service.update_project_token = AsyncMock()

    client.invalidate()

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_response)):
        with patch("kineviz_database_proxy.drivers.rocketgraph.ProjectService", return_value=mock_project_service):
            token = await client.get_token()

    assert token == "after-invalidate"


@pytest.mark.asyncio
async def test_bearer_token_returns_none_when_token_missing():
    """Empty oauth_config.token under BEARER_TOKEN auth → anonymous access (None)."""
    project = _make_project(auth_type=AuthType.BEARER_TOKEN, token=None)
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    token = await client.get_token()

    assert token is None


@pytest.mark.asyncio
async def test_username_password_returns_none_when_credentials_missing():
    """Empty username and password → anonymous access (None), no login attempted."""
    project = _make_project(
        auth_type=AuthType.USERNAME_PASSWORD,
        username="",
        password="",
    )
    client = AuthClient(project, base_url="http://example.com:4368/api/v1")

    with patch("httpx.AsyncClient.post") as mock_post:
        token = await client.get_token()
        mock_post.assert_not_called()

    assert token is None
