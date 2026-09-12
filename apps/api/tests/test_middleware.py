"""Unit tests for AuthenticationMiddleware."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from starlette.responses import Response

from app.auth.context import AuthenticationContext
from app.auth.exceptions import (
    AuthenticationError,
    TokenInvalidError,
)
from app.auth.middleware import AuthenticationMiddleware
from app.auth.policy import PolicyEngine
from app.auth.tokens import TokenService
from app.common.config import Environment, LogFormat, Settings
from mip_models.user import Identity


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
        jwt_signing_secret="test-signing-secret-change-me-32-bytes",
        jwt_algorithm="HS256",
        jwt_issuer="mail-intelligence-platform-test",
        jwt_audience="mail-intelligence-api-test",
        jwt_access_token_expire_minutes=15,
        session_idle_timeout_hours=24,
    )


@pytest.fixture
def token_service() -> TokenService:
    return TokenService(_make_settings())


@pytest.fixture
def policy_engine() -> PolicyEngine:
    return PolicyEngine()


def _make_token(
    settings: Settings,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    org_id: uuid.UUID,
) -> str:
    from jwt import encode

    now = datetime.now(UTC)
    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "iat": now,
        "nbf": now,
        "exp": now + __import__("datetime").timedelta(minutes=15),
        "jti": str(uuid.uuid4()),
        "sid": str(session_id),
        "tid": str(tenant_id),
        "oid": str(org_id),
    }
    return encode(payload, settings.jwt_signing_secret, algorithm=settings.jwt_algorithm)


class TestAuthenticationMiddlewareUnit:
    """Unit tests for AuthenticationMiddleware.dispatch()."""

    @pytest.fixture
    def middleware(
        self,
        token_service: TokenService,
        policy_engine: PolicyEngine,
    ) -> AuthenticationMiddleware:
        return AuthenticationMiddleware(
            app=MagicMock(),
            policy_engine=policy_engine,
            token_service=token_service,
        )

    async def test_public_route_skips_auth(self, middleware: AuthenticationMiddleware) -> None:
        request = MagicMock(spec=Request)
        request.headers = {}
        request.url.path = "/health/live"
        request.method = "GET"
        request.client = None

        call_next = AsyncMock(return_value=Response(status_code=200))
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 200
        call_next.assert_called_once()

    async def test_missing_token_returns_401(self, middleware: AuthenticationMiddleware) -> None:
        request = MagicMock(spec=Request)
        request.headers = {}
        request.url.path = "/api/v1/protected"
        request.method = "GET"
        request.client = MagicMock(host="127.0.0.1")

        call_next = AsyncMock()
        with (
            patch("app.auth.middleware.get_session_factory", return_value=MagicMock()),
            pytest.raises(AuthenticationError),
        ):
            await middleware.dispatch(request, call_next)
        call_next.assert_not_called()


class TestProviderDerivation:
    """Tests for AD-PR13-010: AuthenticationContext.provider derivation."""

    @pytest.fixture
    def middleware(
        self,
        token_service: TokenService,
        policy_engine: PolicyEngine,
    ) -> AuthenticationMiddleware:
        return AuthenticationMiddleware(
            app=MagicMock(),
            policy_engine=policy_engine,
            token_service=token_service,
        )

    async def test_provider_derived_from_identity(
        self,
        middleware: AuthenticationMiddleware,
    ) -> None:
        settings = _make_settings()
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        org_id = uuid.uuid4()
        session_id = uuid.uuid4()
        identity_id = uuid.uuid4()
        role_id = uuid.uuid4()

        token = _make_token(settings, user_id, session_id, tenant_id, org_id)

        device_session = MagicMock()
        device_session.id = session_id
        device_session.user_id = user_id
        device_session.tenant_id = tenant_id
        device_session.revoked_at = None
        device_session.expires_at = datetime.now(UTC) + timedelta(hours=1)
        device_session.last_active_at = datetime.now(UTC)
        device_session.identity_id = identity_id

        tenant = MagicMock()
        tenant.id = tenant_id
        tenant.is_active = True
        tenant.organization_id = org_id

        membership = MagicMock()
        membership.id = uuid.uuid4()
        membership.role_id = role_id
        membership.is_active = True

        role = MagicMock()
        role.id = role_id
        role.name = "member"
        role.permissions = []

        identity = MagicMock(spec=Identity)
        identity.provider = "microsoft"

        def make_result(value):
            mock_result = MagicMock()
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = value
            mock_result.scalars.return_value = mock_scalars
            return mock_result

        class FakeAsyncSession:
            def __init__(self, results):
                self._results = results
                self._index = 0
                self.commit = AsyncMock()
                self.rollback = AsyncMock()
                self.flush = AsyncMock()

            async def execute(self, *args, **kwargs):
                result = self._results[self._index]
                self._index += 1
                return result

        execute_results = [
            make_result(device_session),
            make_result(tenant),
            make_result(membership),
            make_result(identity),
            make_result(role),
        ]

        fake_session = FakeAsyncSession(execute_results)

        mock_factory = MagicMock()
        mock_factory.get_session.return_value.__anext__ = AsyncMock(return_value=fake_session)
        mock_factory.get_session.return_value.aclose = AsyncMock()

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": f"Bearer {token}"}
        request.url.path = "/api/v1/protected"
        request.method = "GET"
        request.client = MagicMock(host="127.0.0.1")

        app_state = MagicMock()
        app_state.settings = settings
        request.app.state = app_state

        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("app.auth.middleware.get_session_factory", return_value=mock_factory):
            response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        assert isinstance(request.state.auth_context, AuthenticationContext)
        assert request.state.auth_context.provider == "microsoft"

    async def test_provider_none_without_identity(
        self,
        middleware: AuthenticationMiddleware,
    ) -> None:
        settings = _make_settings()
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        org_id = uuid.uuid4()
        session_id = uuid.uuid4()
        role_id = uuid.uuid4()

        token = _make_token(settings, user_id, session_id, tenant_id, org_id)

        device_session = MagicMock()
        device_session.id = session_id
        device_session.user_id = user_id
        device_session.tenant_id = tenant_id
        device_session.revoked_at = None
        device_session.expires_at = datetime.now(UTC) + timedelta(hours=1)
        device_session.last_active_at = datetime.now(UTC)
        device_session.identity_id = None

        tenant = MagicMock()
        tenant.id = tenant_id
        tenant.is_active = True
        tenant.organization_id = org_id

        membership = MagicMock()
        membership.id = uuid.uuid4()
        membership.role_id = role_id
        membership.is_active = True

        role = MagicMock()
        role.id = role_id
        role.name = "member"
        role.permissions = []

        def make_result(value):
            mock_result = MagicMock()
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = value
            mock_result.scalars.return_value = mock_scalars
            return mock_result

        class FakeAsyncSession:
            def __init__(self, results):
                self._results = results
                self._index = 0
                self.commit = AsyncMock()
                self.rollback = AsyncMock()
                self.flush = AsyncMock()

            async def execute(self, *args, **kwargs):
                result = self._results[self._index]
                self._index += 1
                return result

        execute_results = [
            make_result(device_session),
            make_result(tenant),
            make_result(membership),
            make_result(role),
        ]

        fake_session = FakeAsyncSession(execute_results)

        mock_factory = MagicMock()
        mock_factory.get_session.return_value.__anext__ = AsyncMock(return_value=fake_session)
        mock_factory.get_session.return_value.aclose = AsyncMock()

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": f"Bearer {token}"}
        request.url.path = "/api/v1/protected"
        request.method = "GET"
        request.client = MagicMock(host="127.0.0.1")

        app_state = MagicMock()
        app_state.settings = settings
        request.app.state = app_state

        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("app.auth.middleware.get_session_factory", return_value=mock_factory):
            response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        assert isinstance(request.state.auth_context, AuthenticationContext)
        assert request.state.auth_context.provider is None

    async def test_provider_fail_closed_when_identity_missing(
        self,
        middleware: AuthenticationMiddleware,
    ) -> None:
        settings = _make_settings()
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        org_id = uuid.uuid4()
        session_id = uuid.uuid4()
        identity_id = uuid.uuid4()
        role_id = uuid.uuid4()

        token = _make_token(settings, user_id, session_id, tenant_id, org_id)

        device_session = MagicMock()
        device_session.id = session_id
        device_session.user_id = user_id
        device_session.tenant_id = tenant_id
        device_session.revoked_at = None
        device_session.expires_at = datetime.now(UTC) + timedelta(hours=1)
        device_session.last_active_at = datetime.now(UTC)
        device_session.identity_id = identity_id

        tenant = MagicMock()
        tenant.id = tenant_id
        tenant.is_active = True
        tenant.organization_id = org_id

        membership = MagicMock()
        membership.id = uuid.uuid4()
        membership.role_id = role_id
        membership.is_active = True

        role = MagicMock()
        role.id = role_id
        role.name = "member"
        role.permissions = []

        def make_result(value):
            mock_result = MagicMock()
            mock_scalars = MagicMock()
            mock_scalars.first.return_value = value
            mock_result.scalars.return_value = mock_scalars
            return mock_result

        class FakeAsyncSession:
            def __init__(self, results):
                self._results = results
                self._index = 0
                self.commit = AsyncMock()
                self.rollback = AsyncMock()
                self.flush = AsyncMock()

            async def execute(self, *args, **kwargs):
                result = self._results[self._index]
                self._index += 1
                return result

        execute_results = [
            make_result(device_session),
            make_result(tenant),
            make_result(membership),
            make_result(None),
            make_result(role),
        ]

        fake_session = FakeAsyncSession(execute_results)

        mock_factory = MagicMock()
        mock_factory.get_session.return_value.__anext__ = AsyncMock(return_value=fake_session)
        mock_factory.get_session.return_value.aclose = AsyncMock()

        request = MagicMock(spec=Request)
        request.headers = {"Authorization": f"Bearer {token}"}
        request.url.path = "/api/v1/protected"
        request.method = "GET"
        request.client = MagicMock(host="127.0.0.1")

        app_state = MagicMock()
        app_state.settings = settings
        request.app.state = app_state

        call_next = AsyncMock()

        with (
            patch("app.auth.middleware.get_session_factory", return_value=mock_factory),
            pytest.raises(TokenInvalidError, match="Session identity not found"),
        ):
            await middleware.dispatch(request, call_next)

        call_next.assert_not_called()
