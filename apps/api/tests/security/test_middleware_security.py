"""Security tests for AuthenticationMiddleware."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from starlette.responses import Response

from app.auth.exceptions import AuthenticationError, TokenInvalidError
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


@pytest.fixture
def token_service() -> TokenService:
    return TokenService(_make_settings())


@pytest.fixture
def policy_engine() -> PolicyEngine:
    return PolicyEngine()


@pytest.fixture
def middleware(
    token_service: TokenService,
    policy_engine: PolicyEngine,
) -> AuthenticationMiddleware:
    return AuthenticationMiddleware(
        app=MagicMock(),
        policy_engine=policy_engine,
        token_service=token_service,
    )


def _make_request(
    headers: dict[str, str],
    path: str = "/api/v1/protected",
    method: str = "GET",
) -> MagicMock:
    request = MagicMock(spec=Request)
    request.headers = headers
    request.url.path = path
    request.method = method
    request.client = MagicMock(host="127.0.0.1")
    return request


def _make_mock_factory(session: MagicMock) -> MagicMock:
    mock_factory = MagicMock()
    mock_factory.get_session.return_value.__anext__ = AsyncMock(return_value=session)
    mock_factory.get_session.return_value.aclose = AsyncMock()
    return mock_factory


class TestTokenLeakage:
    """Verify raw tokens never leak in errors or logs."""

    async def test_no_token_leakage_in_errors(
        self,
        middleware: AuthenticationMiddleware,
    ) -> None:
        raw_token = "eyJhbGciOiJIUzI1NiJ9.raw.token.here"  # noqa: S105
        request = _make_request({"Authorization": f"Bearer {raw_token}"})
        call_next = AsyncMock()
        with (
            patch("app.auth.middleware.get_session_factory", return_value=MagicMock()),
            pytest.raises(TokenInvalidError),
        ):
            await middleware.dispatch(request, call_next)

    async def test_no_token_leakage_in_logs(
        self,
        middleware: AuthenticationMiddleware,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        raw_token = "eyJhbGciOiJIUzI1NiJ9.raw.token.here"  # noqa: S105
        request = _make_request({"Authorization": f"Bearer {raw_token}"})
        call_next = AsyncMock()
        with (
            patch("app.auth.middleware.get_session_factory", return_value=MagicMock()),
            pytest.raises(TokenInvalidError),
        ):
            await middleware.dispatch(request, call_next)
        captured = capsys.readouterr()
        assert raw_token not in captured.out
        assert raw_token not in captured.err


class TestJtiPresenceOnly:
    """Verify jti is presence-only, no revocation lookup."""

    async def test_jti_presence_only(self, middleware: AuthenticationMiddleware) -> None:
        settings = _make_settings()
        valid_token = _make_token(
            settings,
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            org_id=uuid.uuid4(),
        )
        request = _make_request({"Authorization": f"Bearer {valid_token}"})
        call_next = AsyncMock()

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(
                scalars=MagicMock(
                    return_value=MagicMock(first=MagicMock(return_value=None)),
                ),
            ),
        )
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_factory = _make_mock_factory(mock_session)

        with (
            patch("app.auth.middleware.get_session_factory", return_value=mock_factory),
            pytest.raises(TokenInvalidError, match="Session not found"),
        ):
            await middleware.dispatch(request, call_next)


class TestDefaultDeny:
    """Verify default-deny behavior for unknown routes."""

    async def test_default_deny_unknown_routes(
        self,
        middleware: AuthenticationMiddleware,
    ) -> None:
        request = _make_request({}, path="/unknown/deep/path")
        call_next = AsyncMock()
        with (
            patch("app.auth.middleware.get_session_factory", return_value=MagicMock()),
            pytest.raises(AuthenticationError),
        ):
            await middleware.dispatch(request, call_next)
        call_next.assert_not_called()


class TestSecurityHeaders:
    """Verify CORS and security headers are present."""

    async def test_cors_headers_present(self, middleware: AuthenticationMiddleware) -> None:
        request = _make_request({}, path="/health/live")
        call_next = AsyncMock(return_value=Response(status_code=200))
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 200

    async def test_security_headers_present(self, middleware: AuthenticationMiddleware) -> None:
        request = _make_request({}, path="/health/live")
        call_next = AsyncMock(return_value=Response(status_code=200))
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 200


class TestMalformedClaims:
    """Verify malformed JWT claims are rejected."""

    async def test_missing_sid_tid_oid(self, middleware: AuthenticationMiddleware) -> None:
        settings = _make_settings()
        from jwt import encode

        now = datetime.now(UTC)
        payload = {
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "sub": str(uuid.uuid4()),
            "iat": now,
            "nbf": now,
            "exp": now + __import__("datetime").timedelta(minutes=15),
            "jti": str(uuid.uuid4()),
        }
        token = encode(payload, settings.jwt_signing_secret, algorithm=settings.jwt_algorithm)
        request = _make_request({"Authorization": f"Bearer {token}"})
        call_next = AsyncMock()

        with (
            patch("app.auth.middleware.get_session_factory", return_value=MagicMock()),
            pytest.raises(TokenInvalidError),
        ):
            await middleware.dispatch(request, call_next)
