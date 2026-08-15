"""Unit tests for authentication API schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.auth.schemas import (
    LogoutAllRequest,
    LogoutRequest,
    RefreshTokenRequest,
    TokenRequest,
    TokenResponse,
)


class TestRefreshTokenRequest:
    """Verify RefreshTokenRequest validation."""

    def test_valid_request(self) -> None:
        req = RefreshTokenRequest(refresh_token="valid-token")
        assert req.refresh_token == "valid-token"  # noqa: S105

    def test_missing_refresh_token_raises_422(self) -> None:
        with pytest.raises(ValidationError):
            RefreshTokenRequest()


class TestTokenRequest:
    """Verify TokenRequest validation."""

    def test_valid_refresh_token_grant(self) -> None:
        req = TokenRequest(
            grant_type="refresh_token",
            refresh_token="valid-token",
        )
        assert req.grant_type == "refresh_token"
        assert req.refresh_token == "valid-token"  # noqa: S105
        assert req.scope is None

    def test_valid_with_scope(self) -> None:
        req = TokenRequest(
            grant_type="refresh_token",
            refresh_token="valid-token",
            scope="openid profile",
        )
        assert req.scope == "openid profile"

    def test_any_grant_type_accepted_by_schema(self) -> None:
        req = TokenRequest(grant_type="password", refresh_token="token")
        assert req.grant_type == "password"

    def test_missing_grant_type_raises_422(self) -> None:
        with pytest.raises(ValidationError):
            TokenRequest(refresh_token="token")

    def test_missing_refresh_token_raises_422(self) -> None:
        with pytest.raises(ValidationError):
            TokenRequest(grant_type="refresh_token")


class TestTokenResponse:
    """Verify TokenResponse serialization."""

    def test_valid_response(self) -> None:
        resp = TokenResponse(
            access_token="access-123",
            refresh_token="refresh-123",
            token_type="bearer",
            expires_in=3600,
        )
        assert resp.access_token == "access-123"  # noqa: S105
        assert resp.refresh_token == "refresh-123"  # noqa: S105
        assert resp.token_type == "bearer"  # noqa: S105
        assert resp.expires_in == 3600

    def test_default_token_type(self) -> None:
        resp = TokenResponse(
            access_token="access-123",
            refresh_token="refresh-123",
            expires_in=3600,
        )
        assert resp.token_type == "bearer"  # noqa: S105


class TestLogoutRequest:
    """Verify LogoutRequest validation."""

    def test_valid_request(self) -> None:
        req = LogoutRequest(refresh_token="valid-token")
        assert req.refresh_token == "valid-token"  # noqa: S105

    def test_missing_refresh_token_raises_422(self) -> None:
        with pytest.raises(ValidationError):
            LogoutRequest()


class TestLogoutAllRequest:
    """Verify LogoutAllRequest validation."""

    def test_valid_request(self) -> None:
        req = LogoutAllRequest(refresh_token="valid-token")
        assert req.refresh_token == "valid-token"  # noqa: S105

    def test_missing_refresh_token_raises_422(self) -> None:
        with pytest.raises(ValidationError):
            LogoutAllRequest()
