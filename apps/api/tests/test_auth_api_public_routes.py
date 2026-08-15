"""Unit tests for public route registration of auth endpoints."""

from __future__ import annotations

from app.auth.public_routes import PUBLIC_ROUTES, is_public_route


class TestAuthApiPublicRoutes:
    """Verify all four auth endpoints are registered as public routes."""

    def test_auth_refresh_is_public(self) -> None:
        assert is_public_route("POST", "/auth/refresh") is True

    def test_auth_token_is_public(self) -> None:
        assert is_public_route("POST", "/auth/token") is True

    def test_auth_logout_is_public(self) -> None:
        assert is_public_route("POST", "/auth/logout") is True

    def test_auth_logout_all_is_public(self) -> None:
        assert is_public_route("POST", "/auth/logout-all") is True

    def test_all_four_in_public_routes_set(self) -> None:
        assert ("POST", "/auth/refresh") in PUBLIC_ROUTES
        assert ("POST", "/auth/token") in PUBLIC_ROUTES
        assert ("POST", "/auth/logout") in PUBLIC_ROUTES
        assert ("POST", "/auth/logout-all") in PUBLIC_ROUTES

    def test_method_case_insensitive(self) -> None:
        assert is_public_route("post", "/auth/refresh") is True
        assert is_public_route("Post", "/auth/token") is True
        assert is_public_route("post", "/auth/logout") is True
        assert is_public_route("post", "/auth/logout-all") is True
