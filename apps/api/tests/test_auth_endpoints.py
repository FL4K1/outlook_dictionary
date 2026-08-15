"""Unit tests for authentication API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.auth.events import SecurityEventType
from app.auth.tokens import RefreshTokenPair
from app.common.config import Environment, LogFormat, Settings, get_settings
from app.common.dependencies import get_db
from app.main import create_app
from mip_models.auth import DeviceSession


def _make_settings() -> Settings:
    return Settings(
        app_env=Environment.TESTING,
        app_debug=False,
        app_log_level="WARNING",
        app_log_format=LogFormat.CONSOLE,
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="test",
        postgres_password="test",
        postgres_db="test_mail_intelligence",
        jwt_access_token_expire_minutes=15,
    )


@pytest.fixture
def test_settings() -> Settings:
    return _make_settings()


@pytest.fixture
def app(test_settings: Settings):
    app = create_app(settings=test_settings)
    mock_db = MagicMock()

    async def _override_get_db():
        yield mock_db

    def _override_get_settings():
        return test_settings

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_settings] = _override_get_settings
    app._test_settings = test_settings
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestAuthRefresh:
    """Verify POST /auth/refresh behavior."""

    async def test_refresh_successful(
        self,
        client: AsyncClient,
        app,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        org_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = org_id
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["access_token"] == "fake-access-token"  # noqa: S105
        assert body["refresh_token"] == "new-refresh-token"  # noqa: S105
        assert body["token_type"] == "bearer"  # noqa: S105
        assert body["expires_in"] == app._test_settings.jwt_access_token_expire_minutes * 60

        mock_session_service.refresh_session.assert_called_once()
        mock_token_service.create_access_token.assert_called_once()
        subject = mock_token_service.create_access_token.call_args.args[0]
        assert subject.user_id == user_id
        assert subject.tenant_id == tenant_id
        assert subject.organization_id == org_id
        assert subject.session_id == session_id

    async def test_refresh_invalid_token(self, client: AsyncClient) -> None:
        from app.auth.exceptions import TokenInvalidError

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(
                side_effect=TokenInvalidError("Invalid refresh token.")
            )
            mock_session_service_cls.return_value = mock_session_service

            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": "bad-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["error"]["code"] == "TOKEN_INVALID"

    async def test_refresh_reused_token(self, client: AsyncClient) -> None:
        from app.auth.exceptions import RefreshTokenReusedError

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(side_effect=RefreshTokenReusedError())
            mock_session_service_cls.return_value = mock_session_service

            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": "replayed-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["error"]["code"] == "REFRESH_TOKEN_REUSED"

    async def test_refresh_revoked_session(self, client: AsyncClient) -> None:
        from app.auth.exceptions import RefreshTokenReusedError

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(side_effect=RefreshTokenReusedError())
            mock_session_service_cls.return_value = mock_session_service

            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": "revoked-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["error"]["code"] == "REFRESH_TOKEN_REUSED"

    async def test_refresh_absolute_timeout(self, client: AsyncClient) -> None:
        from app.auth.exceptions import SessionExpiredError

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(
                side_effect=SessionExpiredError("Absolute timeout exceeded")
            )
            mock_session_service_cls.return_value = mock_session_service

            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": "expired-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["error"]["code"] == "SESSION_EXPIRED"

    async def test_refresh_idle_timeout(self, client: AsyncClient) -> None:
        from app.auth.exceptions import SessionExpiredError

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(
                side_effect=SessionExpiredError("Idle timeout exceeded")
            )
            mock_session_service_cls.return_value = mock_session_service

            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": "idle-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["error"]["code"] == "SESSION_EXPIRED"

    async def test_refresh_tenant_missing(self, client: AsyncClient) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=None)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        body = response.json()
        assert body["error"]["code"] == "TENANT_ACCESS_DENIED"

    async def test_refresh_tenant_inactive(self, client: AsyncClient) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = uuid.uuid4()
        mock_tenant.is_active = False

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        body = response.json()
        assert body["error"]["code"] == "TENANT_ACCESS_DENIED"

    async def test_refresh_organization_id_from_tenant(
        self,
        client: AsyncClient,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        org_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = org_id
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            await client.post(
                "/auth/refresh",
                json={"refresh_token": "valid-token"},
            )

        subject = mock_token_service.create_access_token.call_args.args[0]
        assert subject.organization_id == org_id

    async def test_refresh_expires_in_correct(
        self,
        client: AsyncClient,
        app,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = uuid.uuid4()
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["expires_in"] == app._test_settings.jwt_access_token_expire_minutes * 60

    async def test_refresh_preserves_device_session_id(
        self,
        client: AsyncClient,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = uuid.uuid4()
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_200_OK
        subject = mock_token_service.create_access_token.call_args.args[0]
        assert subject.session_id == session_id

    async def test_refresh_returns_new_refresh_token(
        self,
        client: AsyncClient,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = uuid.uuid4()
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["refresh_token"] == "new-refresh-token"  # noqa: S105

    async def test_refresh_session_called_exactly_once(
        self,
        client: AsyncClient,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = uuid.uuid4()
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            await client.post(
                "/auth/refresh",
                json={"refresh_token": "valid-token"},
            )

        mock_session_service.refresh_session.assert_called_once()

    async def test_refresh_no_raw_token_leakage(self, client: AsyncClient) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = uuid.uuid4()
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            response = await client.post(
                "/auth/refresh",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert "new-refresh-token" in body["refresh_token"]
        assert body["refresh_token"] != "valid-token"  # noqa: S105
        assert body["access_token"] == "fake-access-token"  # noqa: S105

    async def test_refresh_propagates_request_id(
        self,
        client: AsyncClient,
        app,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = uuid.uuid4()
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            await client.post(
                "/auth/refresh",
                json={"refresh_token": "valid-token"},
            )

        mock_session_service.refresh_session.assert_called_once()
        call_kwargs = mock_session_service.refresh_session.call_args.kwargs
        assert "request_id" in call_kwargs
        assert isinstance(call_kwargs["request_id"], str)
        assert len(call_kwargs["request_id"]) > 0


class TestAuthToken:
    """Verify POST /auth/token behavior."""

    async def test_token_successful_refresh_grant(
        self,
        client: AsyncClient,
        app,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        org_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = org_id
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            response = await client.post(
                "/auth/token",
                json={"grant_type": "refresh_token", "refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["access_token"] == "fake-access-token"  # noqa: S105
        assert body["refresh_token"] == "new-refresh-token"  # noqa: S105
        assert body["token_type"] == "bearer"  # noqa: S105
        assert body["expires_in"] == app._test_settings.jwt_access_token_expire_minutes * 60

        mock_session_service.refresh_session.assert_called_once()

    async def test_token_invalid_token(self, client: AsyncClient) -> None:
        from app.auth.exceptions import TokenInvalidError

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(
                side_effect=TokenInvalidError("Invalid refresh token.")
            )
            mock_session_service_cls.return_value = mock_session_service

            response = await client.post(
                "/auth/token",
                json={"grant_type": "refresh_token", "refresh_token": "bad-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["error"] == "invalid_grant"
        assert body["error_description"] == "The refresh token is invalid."
        assert response.headers.get("WWW-Authenticate") == 'Bearer realm="platform"'

    async def test_token_reused_token(self, client: AsyncClient) -> None:
        from app.auth.exceptions import RefreshTokenReusedError

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(side_effect=RefreshTokenReusedError())
            mock_session_service_cls.return_value = mock_session_service

            response = await client.post(
                "/auth/token",
                json={"grant_type": "refresh_token", "refresh_token": "replayed-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["error"] == "invalid_grant"
        assert body["error_description"] == "The refresh token has been reused."
        assert response.headers.get("WWW-Authenticate") == 'Bearer realm="platform"'

    async def test_token_revoked_session(self, client: AsyncClient) -> None:
        from app.auth.exceptions import RefreshTokenReusedError

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(side_effect=RefreshTokenReusedError())
            mock_session_service_cls.return_value = mock_session_service

            response = await client.post(
                "/auth/token",
                json={"grant_type": "refresh_token", "refresh_token": "revoked-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["error"] == "invalid_grant"
        assert body["error_description"] == "The refresh token has been reused."
        assert response.headers.get("WWW-Authenticate") == 'Bearer realm="platform"'

    async def test_token_absolute_timeout(self, client: AsyncClient) -> None:
        from app.auth.exceptions import SessionExpiredError

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(
                side_effect=SessionExpiredError("Absolute timeout exceeded")
            )
            mock_session_service_cls.return_value = mock_session_service

            response = await client.post(
                "/auth/token",
                json={"grant_type": "refresh_token", "refresh_token": "expired-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["error"] == "invalid_grant"
        assert body["error_description"] == "The session has expired."
        assert response.headers.get("WWW-Authenticate") == 'Bearer realm="platform"'

    async def test_token_idle_timeout(self, client: AsyncClient) -> None:
        from app.auth.exceptions import SessionExpiredError

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(
                side_effect=SessionExpiredError("Idle timeout exceeded")
            )
            mock_session_service_cls.return_value = mock_session_service

            response = await client.post(
                "/auth/token",
                json={"grant_type": "refresh_token", "refresh_token": "idle-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["error"] == "invalid_grant"
        assert body["error_description"] == "The session has expired."
        assert response.headers.get("WWW-Authenticate") == 'Bearer realm="platform"'

    async def test_token_unsupported_grant_type(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/token",
            json={"grant_type": "password", "refresh_token": "x"},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["error"] == "unsupported_grant_type"
        assert body["error_description"] == "The grant type is not supported."

    async def test_token_empty_refresh_token(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/token",
            json={"grant_type": "refresh_token", "refresh_token": ""},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["error"] == "invalid_request"
        assert body["error_description"] == "The refresh token is required."

    async def test_token_missing_grant_type(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/token",
            json={"refresh_token": "x"},
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"

    async def test_token_missing_refresh_token(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/token",
            json={"grant_type": "refresh_token"},
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"

    async def test_token_session_called_exactly_once(
        self,
        client: AsyncClient,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = uuid.uuid4()
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            await client.post(
                "/auth/token",
                json={"grant_type": "refresh_token", "refresh_token": "valid-token"},
            )

        mock_session_service.refresh_session.assert_called_once()

    async def test_token_organization_id_from_tenant(
        self,
        client: AsyncClient,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        org_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = org_id
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            await client.post(
                "/auth/token",
                json={"grant_type": "refresh_token", "refresh_token": "valid-token"},
            )

        subject = mock_token_service.create_access_token.call_args.args[0]
        assert subject.organization_id == org_id

    async def test_token_expires_in_correct(
        self,
        client: AsyncClient,
        app,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = uuid.uuid4()
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            response = await client.post(
                "/auth/token",
                json={"grant_type": "refresh_token", "refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["expires_in"] == app._test_settings.jwt_access_token_expire_minutes * 60

    async def test_token_no_raw_token_leakage(self, client: AsyncClient) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

        mock_tenant = MagicMock()
        mock_tenant.organization_id = uuid.uuid4()
        mock_tenant.is_active = True

        with (
            patch("app.api.auth.router.SessionService") as mock_session_service_cls,
            patch("app.api.auth.router.TenantRepository") as mock_tenant_repo_cls,
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
        ):
            mock_session_service = MagicMock()
            mock_session_service.refresh_session = AsyncMock(return_value=(session, new_token))
            mock_session_service_cls.return_value = mock_session_service

            mock_token_service = MagicMock()
            mock_token_service.create_access_token.return_value = "fake-access-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_tenant_repo = MagicMock()
            mock_tenant_repo.get = AsyncMock(return_value=mock_tenant)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            response = await client.post(
                "/auth/token",
                json={"grant_type": "refresh_token", "refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert "new-refresh-token" in body["refresh_token"]
        assert body["refresh_token"] != "valid-token"  # noqa: S105
        assert body["access_token"] == "fake-access-token"  # noqa: S105


class TestAuthLogout:
    """Verify POST /auth/logout behavior."""

    async def test_logout_valid_token(self, client: AsyncClient, app) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

        with (
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
            patch("app.api.auth.router.DeviceSessionRepository") as mock_repo_cls,
        ):
            mock_token_service = MagicMock()
            mock_token_service.hash_refresh_token.return_value = "hashed-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_repo = MagicMock()
            mock_repo.get_by_refresh_token_hash = AsyncMock(return_value=session)
            mock_repo.revoke = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo

            response = await client.post(
                "/auth/logout",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        mock_repo.revoke.assert_called_once()
        call_args = mock_repo.revoke.call_args
        assert call_args.args[0] == session.id
        assert isinstance(call_args.kwargs["revoked_at"], datetime)

    async def test_logout_invalid_token(self, client: AsyncClient) -> None:
        with (
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
            patch("app.api.auth.router.DeviceSessionRepository") as mock_repo_cls,
        ):
            mock_token_service = MagicMock()
            mock_token_service.hash_refresh_token.return_value = "hashed-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_repo = MagicMock()
            mock_repo.get_by_refresh_token_hash = AsyncMock(return_value=None)
            mock_repo_cls.return_value = mock_repo

            response = await client.post(
                "/auth/logout",
                json={"refresh_token": "bad-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["error"]["code"] == "TOKEN_INVALID"

    async def test_logout_already_revoked_session(self, client: AsyncClient, app) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

        with (
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
            patch("app.api.auth.router.DeviceSessionRepository") as mock_repo_cls,
        ):
            mock_token_service = MagicMock()
            mock_token_service.hash_refresh_token.return_value = "hashed-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_repo = MagicMock()
            mock_repo.get_by_refresh_token_hash = AsyncMock(return_value=session)
            mock_repo.revoke = AsyncMock(return_value=False)
            mock_repo_cls.return_value = mock_repo

            response = await client.post(
                "/auth/logout",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        mock_repo.revoke.assert_called_once()

    async def test_logout_no_raw_token_leakage(self, client: AsyncClient, app) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

        with (
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
            patch("app.api.auth.router.DeviceSessionRepository") as mock_repo_cls,
        ):
            mock_token_service = MagicMock()
            mock_token_service.hash_refresh_token.return_value = "hashed-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_repo = MagicMock()
            mock_repo.get_by_refresh_token_hash = AsyncMock(return_value=session)
            mock_repo.revoke = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo

            response = await client.post(
                "/auth/logout",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        mock_token_service.hash_refresh_token.assert_called_once_with("valid-token")

    async def test_logout_event_includes_request_id(self, client: AsyncClient, app) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

        with (
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
            patch("app.api.auth.router.DeviceSessionRepository") as mock_repo_cls,
            patch("app.api.auth.router.security_event_emitter") as mock_emitter,
        ):
            mock_token_service = MagicMock()
            mock_token_service.hash_refresh_token.return_value = "hashed-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_repo = MagicMock()
            mock_repo.get_by_refresh_token_hash = AsyncMock(return_value=session)
            mock_repo.revoke = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo

            response = await client.post(
                "/auth/logout",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        revoked_calls = [
            c
            for c in mock_emitter.emit.call_args_list
            if c.args[0].event_type == SecurityEventType.SESSION_REVOKED
        ]
        assert len(revoked_calls) == 1
        assert revoked_calls[0].args[0].request_id is not None
        assert isinstance(revoked_calls[0].args[0].request_id, str)


class TestAuthLogoutAll:
    """Verify POST /auth/logout-all behavior."""

    async def test_logout_all_valid_token(self, client: AsyncClient, app) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        other_session_1 = DeviceSession(
            id=uuid.uuid4(),
            user_id=user_id,
            tenant_id=uuid.uuid4(),
        )
        other_session_2 = DeviceSession(
            id=uuid.uuid4(),
            user_id=user_id,
            tenant_id=uuid.uuid4(),
        )

        with (
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
            patch("app.api.auth.router.DeviceSessionRepository") as mock_repo_cls,
            patch("app.api.auth.router.security_event_emitter") as mock_emitter,
        ):
            mock_token_service = MagicMock()
            mock_token_service.hash_refresh_token.return_value = "hashed-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_repo = MagicMock()
            mock_repo.get_by_refresh_token_hash = AsyncMock(return_value=session)
            mock_repo.get_active_sessions_for_user = AsyncMock(
                return_value=[session, other_session_1, other_session_2]
            )
            mock_repo.revoke_all_for_user = AsyncMock(return_value=3)
            mock_repo_cls.return_value = mock_repo

            response = await client.post(
                "/auth/logout-all",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        mock_repo.revoke_all_for_user.assert_called_once()
        call_args = mock_repo.revoke_all_for_user.call_args
        assert call_args.args[0] == user_id
        assert isinstance(call_args.kwargs["revoked_at"], datetime)

        assert mock_emitter.emit.call_count == 4
        session_revoked_calls = [
            c
            for c in mock_emitter.emit.call_args_list
            if c.args[0].event_type == SecurityEventType.SESSION_REVOKED
        ]
        assert len(session_revoked_calls) == 3
        all_revoked_calls = [
            c
            for c in mock_emitter.emit.call_args_list
            if c.args[0].event_type == SecurityEventType.ALL_SESSIONS_REVOKED
        ]
        assert len(all_revoked_calls) == 1
        assert all_revoked_calls[0].args[0].metadata == {"revoked_count": "3"}

    async def test_logout_all_invalid_token(self, client: AsyncClient) -> None:
        with (
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
            patch("app.api.auth.router.DeviceSessionRepository") as mock_repo_cls,
        ):
            mock_token_service = MagicMock()
            mock_token_service.hash_refresh_token.return_value = "hashed-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_repo = MagicMock()
            mock_repo.get_by_refresh_token_hash = AsyncMock(return_value=None)
            mock_repo_cls.return_value = mock_repo

            response = await client.post(
                "/auth/logout-all",
                json={"refresh_token": "bad-token"},
            )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        body = response.json()
        assert body["error"]["code"] == "TOKEN_INVALID"

    async def test_logout_all_revokes_all_user_sessions(self, client: AsyncClient, app) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

        with (
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
            patch("app.api.auth.router.DeviceSessionRepository") as mock_repo_cls,
        ):
            mock_token_service = MagicMock()
            mock_token_service.hash_refresh_token.return_value = "hashed-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_repo = MagicMock()
            mock_repo.get_by_refresh_token_hash = AsyncMock(return_value=session)
            mock_repo.get_active_sessions_for_user = AsyncMock(return_value=[session])
            mock_repo.revoke_all_for_user = AsyncMock(return_value=5)
            mock_repo_cls.return_value = mock_repo

            response = await client.post(
                "/auth/logout-all",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        mock_repo.revoke_all_for_user.assert_called_once()
        call_args = mock_repo.revoke_all_for_user.call_args
        assert call_args.args[0] == user_id
        assert isinstance(call_args.kwargs["revoked_at"], datetime)

    async def test_logout_all_no_raw_token_leakage(self, client: AsyncClient, app) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

        with (
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
            patch("app.api.auth.router.DeviceSessionRepository") as mock_repo_cls,
        ):
            mock_token_service = MagicMock()
            mock_token_service.hash_refresh_token.return_value = "hashed-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_repo = MagicMock()
            mock_repo.get_by_refresh_token_hash = AsyncMock(return_value=session)
            mock_repo.get_active_sessions_for_user = AsyncMock(return_value=[session])
            mock_repo.revoke_all_for_user = AsyncMock(return_value=1)
            mock_repo_cls.return_value = mock_repo

            response = await client.post(
                "/auth/logout-all",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        mock_token_service.hash_refresh_token.assert_called_once_with("valid-token")

    async def test_logout_all_cross_tenant_revocation(self, client: AsyncClient, app) -> None:
        user_id = uuid.uuid4()
        tenant_a = uuid.uuid4()
        tenant_b = uuid.uuid4()
        session_in_tenant_a = DeviceSession(
            id=uuid.uuid4(),
            user_id=user_id,
            tenant_id=tenant_a,
        )
        session_in_tenant_b = DeviceSession(
            id=uuid.uuid4(),
            user_id=user_id,
            tenant_id=tenant_b,
        )
        current_session = DeviceSession(
            id=uuid.uuid4(),
            user_id=user_id,
            tenant_id=tenant_a,
        )

        with (
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
            patch("app.api.auth.router.DeviceSessionRepository") as mock_repo_cls,
            patch("app.api.auth.router.security_event_emitter") as mock_emitter,
        ):
            mock_token_service = MagicMock()
            mock_token_service.hash_refresh_token.return_value = "hashed-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_repo = MagicMock()
            mock_repo.get_by_refresh_token_hash = AsyncMock(return_value=current_session)
            mock_repo.get_active_sessions_for_user = AsyncMock(
                return_value=[session_in_tenant_a, session_in_tenant_b]
            )
            mock_repo.revoke_all_for_user = AsyncMock(return_value=2)
            mock_repo_cls.return_value = mock_repo

            response = await client.post(
                "/auth/logout-all",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        mock_repo.revoke_all_for_user.assert_called_once()
        call_args = mock_repo.revoke_all_for_user.call_args
        assert call_args.args[0] == user_id
        assert isinstance(call_args.kwargs["revoked_at"], datetime)
        session_revoked_calls = [
            c
            for c in mock_emitter.emit.call_args_list
            if c.args[0].event_type == SecurityEventType.SESSION_REVOKED
        ]
        assert len(session_revoked_calls) == 2
        revoked_session_ids = {c.args[0].session_id for c in session_revoked_calls}
        assert session_in_tenant_a.id in revoked_session_ids
        assert session_in_tenant_b.id in revoked_session_ids

    async def test_logout_all_other_user_sessions_untouched(self, client: AsyncClient, app) -> None:
        user_id = uuid.uuid4()
        current_session = DeviceSession(
            id=uuid.uuid4(),
            user_id=user_id,
            tenant_id=uuid.uuid4(),
        )

        with (
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
            patch("app.api.auth.router.DeviceSessionRepository") as mock_repo_cls,
        ):
            mock_token_service = MagicMock()
            mock_token_service.hash_refresh_token.return_value = "hashed-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_repo = MagicMock()
            mock_repo.get_by_refresh_token_hash = AsyncMock(return_value=current_session)
            mock_repo.get_active_sessions_for_user = AsyncMock(return_value=[current_session])
            mock_repo.revoke_all_for_user = AsyncMock(return_value=1)
            mock_repo_cls.return_value = mock_repo

            response = await client.post(
                "/auth/logout-all",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        mock_repo.revoke_all_for_user.assert_called_once()
        call_args = mock_repo.revoke_all_for_user.call_args
        assert call_args.args[0] == user_id
        assert isinstance(call_args.kwargs["revoked_at"], datetime)

    async def test_logout_all_events_include_request_id(self, client: AsyncClient, app) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()

        session = DeviceSession(
            id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

        with (
            patch("app.api.auth.router.TokenService") as mock_token_service_cls,
            patch("app.api.auth.router.DeviceSessionRepository") as mock_repo_cls,
            patch("app.api.auth.router.security_event_emitter") as mock_emitter,
        ):
            mock_token_service = MagicMock()
            mock_token_service.hash_refresh_token.return_value = "hashed-token"
            mock_token_service_cls.return_value = mock_token_service

            mock_repo = MagicMock()
            mock_repo.get_by_refresh_token_hash = AsyncMock(return_value=session)
            mock_repo.get_active_sessions_for_user = AsyncMock(return_value=[session])
            mock_repo.revoke_all_for_user = AsyncMock(return_value=1)
            mock_repo_cls.return_value = mock_repo

            response = await client.post(
                "/auth/logout-all",
                json={"refresh_token": "valid-token"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        all_events = [c.args[0] for c in mock_emitter.emit.call_args_list]
        assert all(e.request_id is not None for e in all_events)
        assert all(isinstance(e.request_id, str) for e in all_events)
        assert len({e.request_id for e in all_events}) == 1
