"""Unit tests for application configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.common.config import Settings


class TestEntraSettings:
    """Tests for Microsoft Entra ID configuration."""

    def test_default_entra_settings(self) -> None:
        settings = Settings()
        assert settings.entra_client_id == ""
        assert settings.entra_client_secret == ""
        assert settings.entra_tenant_id == ""
        assert settings.entra_redirect_uri == ""
        assert settings.entra_scopes == ["openid", "profile", "email", "offline_access"]
        assert settings.entra_jwks_endpoint == ""
        assert settings.entra_issuer == ""
        assert settings.entra_audience == ""
        assert settings.entra_clock_skew_seconds == 60

    def test_custom_entra_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENTRA_CLIENT_ID", "client-123")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "secret-123")
        monkeypatch.setenv("ENTRA_TENANT_ID", "tenant-123")
        monkeypatch.setenv("ENTRA_REDIRECT_URI", "https://example.com/callback")
        monkeypatch.setenv("ENTRA_SCOPES", '["openid","profile"]')
        monkeypatch.setenv("ENTRA_JWKS_ENDPOINT", "https://login.microsoftonline.com/jwks")
        monkeypatch.setenv("ENTRA_ISSUER", "https://login.microsoftonline.com/tenant-123/v2.0")
        monkeypatch.setenv("ENTRA_AUDIENCE", "client-123")
        monkeypatch.setenv("ENTRA_CLOCK_SKEW_SECONDS", "30")

        settings = Settings()
        assert settings.entra_client_id == "client-123"
        assert settings.entra_client_secret == "secret-123"  # noqa: S105
        assert settings.entra_tenant_id == "tenant-123"
        assert settings.entra_redirect_uri == "https://example.com/callback"
        assert settings.entra_scopes == ["openid", "profile"]
        assert settings.entra_jwks_endpoint == "https://login.microsoftonline.com/jwks"
        assert settings.entra_issuer == "https://login.microsoftonline.com/tenant-123/v2.0"
        assert settings.entra_audience == "client-123"
        assert settings.entra_clock_skew_seconds == 30


class TestEncryptionSettings:
    """Tests for encryption configuration."""

    def test_default_encryption_dek(self) -> None:
        settings = Settings()
        assert settings.encryption_dek == ""

    def test_valid_hex_dek(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dek = "ab" * 32
        monkeypatch.setenv("ENCRYPTION_DEK", dek)
        settings = Settings()
        assert settings.encryption_dek == dek

    def test_invalid_hex_dek_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENCRYPTION_DEK", "not-hex")
        with pytest.raises(ValidationError):
            Settings()

    def test_short_dek_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENCRYPTION_DEK", "ab" * 16)
        with pytest.raises(ValidationError, match="must be 32 bytes"):
            Settings()

    def test_long_dek_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENCRYPTION_DEK", "ab" * 33)
        with pytest.raises(ValidationError, match="must be 32 bytes"):
            Settings()


class TestProductionValidation:
    """Tests for production configuration validation."""

    def test_production_requires_entra_client_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SIGNING_SECRET", "a" * 32)
        monkeypatch.setenv("ENTRA_CLIENT_ID", "")
        with pytest.raises(ValidationError, match="entra_client_id must be configured"):
            Settings()

    def test_production_requires_encryption_dek(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SIGNING_SECRET", "a" * 32)
        monkeypatch.setenv("ENTRA_CLIENT_ID", "client-123")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "secret-123")
        monkeypatch.setenv("ENTRA_TENANT_ID", "tenant-123")
        monkeypatch.setenv("ENTRA_REDIRECT_URI", "https://example.com/callback")
        monkeypatch.setenv("ENTRA_JWKS_ENDPOINT", "https://example.com/jwks")
        monkeypatch.setenv("ENTRA_ISSUER", "https://example.com")
        monkeypatch.setenv("ENTRA_AUDIENCE", "client-123")
        monkeypatch.setenv("ENCRYPTION_DEK", "")
        with pytest.raises(ValidationError, match="encryption_dek must be configured"):
            Settings()

    def test_production_valid_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("JWT_SIGNING_SECRET", "a" * 32)
        monkeypatch.setenv("ENTRA_CLIENT_ID", "client-123")
        monkeypatch.setenv("ENTRA_CLIENT_SECRET", "secret-123")
        monkeypatch.setenv("ENTRA_TENANT_ID", "tenant-123")
        monkeypatch.setenv("ENTRA_REDIRECT_URI", "https://example.com/callback")
        monkeypatch.setenv("ENTRA_JWKS_ENDPOINT", "https://example.com/jwks")
        monkeypatch.setenv("ENTRA_ISSUER", "https://example.com")
        monkeypatch.setenv("ENTRA_AUDIENCE", "client-123")
        monkeypatch.setenv("ENCRYPTION_DEK", "ab" * 32)
        settings = Settings()
        assert settings.entra_client_id == "client-123"
        assert settings.encryption_dek == "ab" * 32
