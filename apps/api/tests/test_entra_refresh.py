# ruff: noqa: S105
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.common.config import Settings
from mip_providers.identity.entra import EntraIdentityProviderAuth


@pytest.fixture
def mock_settings():
    settings = MagicMock(spec=Settings)
    settings.entra_client_id = "test-client-id"
    settings.entra_tenant_id = "test-tenant"
    settings.entra_redirect_uri = "https://example.com/callback"
    settings.entra_scopes = ["openid", "profile"]
    settings.entra_client_secret = MagicMock()
    settings.entra_client_secret.get_secret_value.return_value = "secret-123"
    return settings


@pytest.fixture
def entra_auth(mock_settings):
    with patch("mip_providers.identity.entra.EntraTokenValidator"):
        auth = EntraIdentityProviderAuth(settings=mock_settings, encryption_service=MagicMock())
        yield auth


@pytest.mark.asyncio
async def test_refresh_credentials_success(entra_auth):
    """1 & 2. Test successful refresh returning a new access token and new refresh token."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-access-token-123",
            "refresh_token": "new-refresh-token-456",
            "expires_in": 3600,
            "scope": "openid email",
        }
        mock_post.return_value = mock_response

        tokens = await entra_auth.refresh_credentials("old-refresh-token-789")

        assert tokens.access_token == "new-access-token-123"
        assert tokens.refresh_token == "new-refresh-token-456"
        assert "email" in tokens.scopes


@pytest.mark.asyncio
async def test_refresh_credentials_preserves_existing_refresh_token(entra_auth):
    """3. Test refresh token omitted from provider response preserves existing refresh token."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-access-token-123",
            "expires_in": 1800,
            "scope": "openid",
        }
        mock_post.return_value = mock_response

        tokens = await entra_auth.refresh_credentials("existing-refresh-token-000")

        assert tokens.access_token == "new-access-token-123"
        assert tokens.refresh_token == "existing-refresh-token-000"


@pytest.mark.asyncio
async def test_refresh_credentials_expiry_parsing(entra_auth):
    """4. Test token expiry parsing based on expires_in."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new-access-token",
            "expires_in": 7200,
        }
        mock_post.return_value = mock_response

        before = datetime.now(UTC)
        tokens = await entra_auth.refresh_credentials("existing-refresh")
        after = datetime.now(UTC)

        assert tokens.expires_at >= before + timedelta(seconds=7190)
        assert tokens.expires_at <= after + timedelta(seconds=7210)


@pytest.mark.asyncio
async def test_refresh_credentials_invalid_grant(entra_auth):
    """5. Test HTTP 400 with invalid_grant payload."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 400
        req = httpx.Request(
            "POST", "https://login.microsoftonline.com/test-tenant/oauth2/v2.0/token"
        )
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400 Client Error: Bad Request for url: invalid_grant",
            request=req,
            response=mock_response,
        )
        mock_post.return_value = mock_response

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await entra_auth.refresh_credentials("expired-refresh-token")

        assert "400" in str(exc_info.value)


@pytest.mark.asyncio
async def test_refresh_credentials_generic_provider_failure(entra_auth):
    """6. Test HTTP 500 generic provider failure."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 500
        req = httpx.Request(
            "POST", "https://login.microsoftonline.com/test-tenant/oauth2/v2.0/token"
        )
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=req,
            response=mock_response,
        )
        mock_post.return_value = mock_response

        with pytest.raises(httpx.HTTPStatusError):
            await entra_auth.refresh_credentials("my-refresh-token")


@pytest.mark.asyncio
async def test_refresh_credentials_network_failure(entra_auth):
    """7. Test network failure / connection error."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        req = httpx.Request(
            "POST", "https://login.microsoftonline.com/test-tenant/oauth2/v2.0/token"
        )
        mock_post.side_effect = httpx.ConnectError("Connection refused", request=req)

        with pytest.raises(httpx.ConnectError):
            await entra_auth.refresh_credentials("my-refresh-token")


@pytest.mark.asyncio
async def test_refresh_credentials_payload_and_no_client_secret(entra_auth):
    """8 & 9. Test payload contains correct refresh parameters and does NOT send client_secret."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "acc",
            "refresh_token": "ref",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_response

        await entra_auth.refresh_credentials("sensitive-refresh-token-xyz")

        mock_post.assert_awaited_once()
        _, kwargs = mock_post.call_args
        data = kwargs["data"]

        assert data["client_id"] == "test-client-id"
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "sensitive-refresh-token-xyz"
        assert data["redirect_uri"] == "https://example.com/callback"
        assert "client_secret" not in data


@pytest.mark.asyncio
async def test_refresh_credentials_no_credentials_exposed_in_error(entra_auth):
    """10. Test that missing access_token error does not expose plaintext refresh token."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": "bad_response"}
        mock_post.return_value = mock_response

        secret_token = "SUPER_SECRET_REFRESH_TOKEN_999"
        with pytest.raises(ValueError) as exc_info:
            await entra_auth.refresh_credentials(secret_token)

        assert secret_token not in str(exc_info.value)


@pytest.mark.asyncio
async def test_token_endpoint_request_uses_explicit_timeout(entra_auth):
    """11. Test that token endpoint requests use an explicit bounded timeout on AsyncClient."""
    with patch("httpx.AsyncClient", autospec=True) as mock_client_cls:
        mock_client = AsyncMock()
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "acc-token",
            "expires_in": 3600,
        }
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value.__aenter__.return_value = mock_client

        await entra_auth.refresh_credentials("test-refresh-token")

        mock_client_cls.assert_called_once()
        _, kwargs = mock_client_cls.call_args
        assert "timeout" in kwargs
        timeout = kwargs["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.connect == 5.0
        assert timeout.read == 15.0
