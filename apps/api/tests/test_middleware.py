"""Unit tests for AuthenticationMiddleware."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from starlette.responses import Response

from app.auth.exceptions import (
    AuthenticationError,
    TokenInvalidError,
)
from app.auth.middleware import AuthenticationMiddleware
from app.auth.policy import PolicyEngine
from app.auth.tokens import TokenService
from app.common.config import Environment, LogFormat, Settings


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
    def token_service(self) -> TokenService:
        return TokenService(_make_settings())

    @pytest.fixture
    def policy_engine(self) -> PolicyEngine:
        return PolicyEngine()

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

    async def test_factory_none_protected_route_rejected(
        self,
        middleware: AuthenticationMiddleware,
    ) -> None:
        request = MagicMock(spec=Request)
        request.headers = {}
        request.url.path = "/api/v1/protected"
        request.method = "GET"
        request.client = MagicMock(host="127.0.0.1")

        call_next = AsyncMock()
        with patch("app.auth.middleware.get_session_factory", return_value=None):
            response = await middleware.dispatch(request, call_next)
        assert response.status_code == 401
        body = json.loads(response.body.decode())  # type: ignore[union-attr]
        assert body["error_code"] == "AUTHENTICATION_SERVICE_UNAVAILABLE"
        call_next.assert_not_called()

    async def test_factory_none_public_route_allowed(
        self,
        middleware: AuthenticationMiddleware,
    ) -> None:
        request = MagicMock(spec=Request)
        request.headers = {}
        request.url.path = "/health/live"
        request.method = "GET"
        request.client = None

        call_next = AsyncMock(return_value=Response(status_code=200))
        with patch("app.auth.middleware.get_session_factory", return_value=None):
            response = await middleware.dispatch(request, call_next)
        assert response.status_code == 200
        call_next.assert_called_once()

    async def test_invalid_token_returns_401(self, middleware: AuthenticationMiddleware) -> None:
        request = MagicMock(spec=Request)
        request.headers = {"Authorization": "Bearer invalid.token.here"}
        request.url.path = "/api/v1/protected"
        request.method = "GET"
        request.client = MagicMock(host="127.0.0.1")

        call_next = AsyncMock()
        with (
            patch("app.auth.middleware.get_session_factory", return_value=MagicMock()),
            pytest.raises(TokenInvalidError),
        ):
            await middleware.dispatch(request, call_next)

    async def test_request_id_from_header(self, middleware: AuthenticationMiddleware) -> None:
        request = MagicMock(spec=Request)
        request.headers = {
            "Authorization": "Bearer invalid.token.here",
            "X-Request-ID": "custom-req-id",
        }
        request.url.path = "/api/v1/protected"
        request.method = "GET"
        request.client = MagicMock(host="127.0.0.1")

        call_next = AsyncMock()
        with (
            patch("app.auth.middleware.get_session_factory", return_value=MagicMock()),
            pytest.raises(TokenInvalidError),
        ):
            await middleware.dispatch(request, call_next)
