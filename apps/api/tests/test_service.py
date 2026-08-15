"""Unit tests for provider-agnostic AuthenticationService."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.auth.service import AuthenticationService
from app.auth.sessions import SessionService
from app.auth.tokens import RefreshTokenPair, TokenService
from mip_models.auth import DeviceSession


@pytest.fixture
def mock_session_service() -> MagicMock:
    service = MagicMock(spec=SessionService)
    service.create_session = AsyncMock()
    service.refresh_session = AsyncMock()
    return service


@pytest.fixture
def mock_token_service() -> MagicMock:
    service = MagicMock(spec=TokenService)
    service.create_access_token.return_value = "fake.jwt.token"
    return service


@pytest.fixture
def auth_service(
    mock_session_service: MagicMock,
    mock_token_service: MagicMock,
) -> AuthenticationService:
    return AuthenticationService(
        session_service=mock_session_service,
        token_service=mock_token_service,
    )


class TestAuthenticationService:
    """Verify platform-core token/session orchestration."""

    async def test_create_session_tokens(
        self,
        auth_service: AuthenticationService,
        mock_session_service: MagicMock,
        mock_token_service: MagicMock,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        org_id = uuid.uuid4()
        session_id = uuid.uuid4()
        session = DeviceSession(id=session_id, user_id=user_id, tenant_id=tenant_id)
        refresh = RefreshTokenPair(plaintext="refresh", hash_val="hash")
        mock_session_service.create_session.return_value = (session, refresh)

        result = await auth_service.create_session_tokens(
            user_id=user_id,
            tenant_id=tenant_id,
            organization_id=org_id,
            ip_address="127.0.0.1",
            user_agent="pytest",
            remember_me=True,
        )

        assert result.access_token == "fake.jwt.token"  # noqa: S105
        assert result.refresh_token == "refresh"  # noqa: S105
        assert result.user_id == user_id
        assert result.tenant_id == tenant_id
        assert result.organization_id == org_id
        assert result.session_id == session_id

        mock_session_service.create_session.assert_called_once_with(
            user_id=user_id,
            tenant_id=tenant_id,
            ip_address="127.0.0.1",
            user_agent="pytest",
            remember_me=True,
            request_id=None,
        )
        mock_token_service.create_access_token.assert_called_once()
        subject = mock_token_service.create_access_token.call_args.args[0]
        assert subject.user_id == user_id
        assert subject.tenant_id == tenant_id
        assert subject.organization_id == org_id
        assert subject.session_id == session_id

    async def test_refresh_session_tokens(
        self,
        auth_service: AuthenticationService,
        mock_session_service: MagicMock,
        mock_token_service: MagicMock,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        org_id = uuid.uuid4()
        session_id = uuid.uuid4()
        session = DeviceSession(id=session_id, user_id=user_id, tenant_id=tenant_id)
        refresh = RefreshTokenPair(plaintext="new-refresh", hash_val="new-hash")
        mock_session_service.refresh_session.return_value = (session, refresh)

        result = await auth_service.refresh_session_tokens(
            plaintext_refresh_token="old-refresh",
            organization_id=org_id,
            ip_address="10.0.0.1",
            user_agent="pytest",
        )

        assert result.access_token == "fake.jwt.token"  # noqa: S105
        assert result.refresh_token == "new-refresh"  # noqa: S105
        assert result.user_id == user_id
        assert result.tenant_id == tenant_id
        assert result.organization_id == org_id
        assert result.session_id == session_id

        mock_session_service.refresh_session.assert_called_once_with(
            plaintext_refresh_token="old-refresh",
            ip_address="10.0.0.1",
            user_agent="pytest",
            request_id=None,
        )
        mock_token_service.create_access_token.assert_called_once()
