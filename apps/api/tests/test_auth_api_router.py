"""Unit tests for the authentication API router."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.api.auth.router import router
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
    )


@pytest.fixture
def app_with_mocks():
    app = create_app(settings=_make_settings())
    mock_db = MagicMock()

    async def _override_get_db():
        yield mock_db

    def _override_get_settings():
        return _make_settings()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_settings] = _override_get_settings
    return app


@pytest.fixture
async def mock_client(app_with_mocks):
    transport = ASGITransport(app=app_with_mocks)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestAuthApiRouterRegistration:
    """Verify the four auth routes are registered with correct methods and paths."""

    def test_refresh_route_registered(self) -> None:
        paths = [str(route.path) for route in router.routes]
        methods = [list(route.methods or set()) for route in router.routes]
        route_map = {p: m for p, m in zip(paths, methods, strict=True)}
        assert "/auth/refresh" in route_map
        assert "POST" in route_map["/auth/refresh"]

    def test_token_route_registered(self) -> None:
        paths = [str(route.path) for route in router.routes]
        methods = [list(route.methods or set()) for route in router.routes]
        route_map = {p: m for p, m in zip(paths, methods, strict=True)}
        assert "/auth/token" in route_map
        assert "POST" in route_map["/auth/token"]

    def test_logout_route_registered(self) -> None:
        paths = [str(route.path) for route in router.routes]
        methods = [list(route.methods or set()) for route in router.routes]
        route_map = {p: m for p, m in zip(paths, methods, strict=True)}
        assert "/auth/logout" in route_map
        assert "POST" in route_map["/auth/logout"]

    def test_logout_all_route_registered(self) -> None:
        paths = [str(route.path) for route in router.routes]
        methods = [list(route.methods or set()) for route in router.routes]
        route_map = {p: m for p, m in zip(paths, methods, strict=True)}
        assert "/auth/logout-all" in route_map
        assert "POST" in route_map["/auth/logout-all"]

    def test_all_four_routes_registered(self) -> None:
        paths = [str(route.path) for route in router.routes]
        expected = {"/auth/refresh", "/auth/token", "/auth/logout", "/auth/logout-all"}
        assert expected.issubset(set(paths))


class TestAuthApiRouterReachability:
    """Verify endpoints are reachable via the test client."""

    @pytest.mark.asyncio
    async def test_token_endpoint_reachable(self, mock_client) -> None:
        session = DeviceSession(
            id=__import__("uuid").uuid4(),
            user_id=__import__("uuid").uuid4(),
            tenant_id=__import__("uuid").uuid4(),
        )
        new_token = RefreshTokenPair(
            plaintext="new-refresh-token",
            hash_val="new-hash",
        )

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
            mock_tenant_repo.get = AsyncMock(return_value=None)
            mock_tenant_repo_cls.return_value = mock_tenant_repo

            response = await mock_client.post(
                "/auth/token",
                json={"grant_type": "refresh_token", "refresh_token": "x"},
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_logout_endpoint_reachable(self, mock_client) -> None:
        session = DeviceSession(
            id=__import__("uuid").uuid4(),
            user_id=__import__("uuid").uuid4(),
            tenant_id=__import__("uuid").uuid4(),
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

            response = await mock_client.post(
                "/auth/logout",
                json={"refresh_token": "x"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT

    @pytest.mark.asyncio
    async def test_logout_all_endpoint_reachable(self, mock_client) -> None:
        session = DeviceSession(
            id=__import__("uuid").uuid4(),
            user_id=__import__("uuid").uuid4(),
            tenant_id=__import__("uuid").uuid4(),
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

            response = await mock_client.post(
                "/auth/logout-all",
                json={"refresh_token": "x"},
            )

        assert response.status_code == status.HTTP_204_NO_CONTENT
