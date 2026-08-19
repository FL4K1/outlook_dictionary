"""Unit tests for identity provider abstractions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from mip_providers.identity import (
    IdentityProviderAuth,
    IdentityVerificationResult,
    ProviderCredentialSet,
)


class TestIdentityVerificationResult:
    """Tests for IdentityVerificationResult dataclass."""

    def test_create_with_required_fields(self) -> None:
        result = IdentityVerificationResult(
            provider_user_id="sub-123",
        )
        assert result.provider_user_id == "sub-123"
        assert result.provider_email is None
        assert result.provider_metadata == {}
        assert result.access_token is None
        assert result.refresh_token is None
        assert result.token_expires_at is None
        assert result.scopes == []

    def test_create_with_all_fields(self) -> None:
        expires = datetime.now(UTC)
        result = IdentityVerificationResult(
            provider_user_id="sub-123",
            provider_email="user@example.com",
            provider_metadata={"tid": "tenant-123"},
            access_token="access-token",
            refresh_token="refresh-token",
            token_expires_at=expires,
            scopes=["openid", "profile"],
        )
        assert result.provider_user_id == "sub-123"
        assert result.provider_email == "user@example.com"
        assert result.provider_metadata == {"tid": "tenant-123"}
        assert result.access_token == "access-token"  # noqa: S105
        assert result.refresh_token == "refresh-token"  # noqa: S105
        assert result.token_expires_at == expires
        assert result.scopes == ["openid", "profile"]

    def test_frozen(self) -> None:
        result = IdentityVerificationResult(provider_user_id="sub-123")
        with pytest.raises(AttributeError):
            result.provider_user_id = "sub-456"  # type: ignore[misc]


class TestProviderCredentialSet:
    """Tests for ProviderCredentialSet dataclass."""

    def test_create(self) -> None:
        expires = datetime.now(UTC)
        creds = ProviderCredentialSet(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=expires,
            scopes=["User.Read"],
        )
        assert creds.access_token == "access-token"  # noqa: S105
        assert creds.refresh_token == "refresh-token"  # noqa: S105
        assert creds.expires_at == expires
        assert creds.scopes == ["User.Read"]

    def test_default_scopes(self) -> None:
        expires = datetime.now(UTC)
        creds = ProviderCredentialSet(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=expires,
        )
        assert creds.scopes == []

    def test_frozen(self) -> None:
        expires = datetime.now(UTC)
        creds = ProviderCredentialSet(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=expires,
        )
        with pytest.raises(AttributeError):
            creds.access_token = "new-token"  # type: ignore[misc]  # noqa: S105


class ConcreteIdentityProviderAuth:
    """Concrete implementation for protocol testing."""

    async def get_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        return f"https://login.microsoftonline.com/authorize?state={state}"

    async def validate_callback(
        self,
        code: str,
        state: str,
        expected_state: str,
        expected_nonce: str,
        code_verifier: str,
    ) -> IdentityVerificationResult:
        return IdentityVerificationResult(provider_user_id="sub-123")

    async def refresh_credentials(self, identity: Any) -> ProviderCredentialSet:
        return ProviderCredentialSet(
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at=datetime.now(UTC),
        )


class TestIdentityProviderAuthProtocol:
    """Tests for IdentityProviderAuth protocol conformance."""

    def test_concrete_implementation_satisfies_protocol(self) -> None:
        impl = ConcreteIdentityProviderAuth()
        assert isinstance(impl, IdentityProviderAuth)

    @pytest.mark.asyncio
    async def test_get_authorization_url(self) -> None:
        impl = ConcreteIdentityProviderAuth()
        url = await impl.get_authorization_url(
            redirect_uri="https://example.com/callback",
            state="state-123",
            nonce="nonce-123",
            code_challenge="challenge-123",
        )
        assert "state=state-123" in url

    @pytest.mark.asyncio
    async def test_validate_callback(self) -> None:
        impl = ConcreteIdentityProviderAuth()
        result = await impl.validate_callback(
            code="code-123",
            state="state-123",
            expected_state="state-123",
            expected_nonce="nonce-123",
            code_verifier="verifier-123",
        )
        assert result.provider_user_id == "sub-123"

    @pytest.mark.asyncio
    async def test_refresh_credentials(self) -> None:
        impl = ConcreteIdentityProviderAuth()
        result = await impl.refresh_credentials(identity=None)
        assert result.access_token == "new-access"  # noqa: S105
        assert result.refresh_token == "new-refresh"  # noqa: S105
