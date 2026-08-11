"""Unit tests for public route definitions."""

from __future__ import annotations

from app.auth.public_routes import PUBLIC_ROUTES, is_public_route


class TestPublicRoutes:
    """Verify public route detection."""

    def test_health_live_is_public(self) -> None:
        assert is_public_route("GET", "/health/live") is True

    def test_health_ready_is_public(self) -> None:
        assert is_public_route("GET", "/health/ready") is True

    def test_health_startup_is_public(self) -> None:
        assert is_public_route("GET", "/health/startup") is True

    def test_auth_token_is_public(self) -> None:
        assert is_public_route("POST", "/auth/token") is True

    def test_auth_refresh_is_public(self) -> None:
        assert is_public_route("POST", "/auth/refresh") is True

    def test_docs_is_public(self) -> None:
        assert is_public_route("GET", "/docs") is True

    def test_redoc_is_public(self) -> None:
        assert is_public_route("GET", "/redoc") is True

    def test_openapi_json_is_public(self) -> None:
        assert is_public_route("GET", "/openapi.json") is True

    def test_protected_route_is_not_public(self) -> None:
        assert is_public_route("GET", "/api/v1/mail/accounts") is False

    def test_method_case_insensitive(self) -> None:
        assert is_public_route("get", "/health/live") is True
        assert is_public_route("Get", "/health/live") is True

    def test_public_routes_is_frozenset(self) -> None:
        assert isinstance(PUBLIC_ROUTES, frozenset)

    def test_public_routes_contains_expected_items(self) -> None:
        assert ("GET", "/health/live") in PUBLIC_ROUTES
        assert ("POST", "/auth/token") in PUBLIC_ROUTES

    def test_public_routes_does_not_contain_protected(self) -> None:
        assert ("GET", "/api/v1/tenants") not in PUBLIC_ROUTES
