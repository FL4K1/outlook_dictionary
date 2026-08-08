"""Unit tests for TokenService."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import jwt
import pytest
from pydantic import ValidationError

from app.auth.exceptions import TokenExpiredError, TokenInvalidError
from app.auth.tokens import AccessTokenSubject, SigningProvider, TokenService
from app.common.config import Environment, Settings


@pytest.fixture
def token_settings() -> Settings:
    """Settings configured with a deterministic local signing secret."""
    return Settings(
        app_env=Environment.TESTING,
        jwt_signing_secret="test-signing-secret-change-me-32-bytes",
        jwt_algorithm="HS256",
        jwt_issuer="mail-intelligence-platform-test",
        jwt_audience="mail-intelligence-api-test",
        jwt_access_token_expire_minutes=15,
        jwt_refresh_token_expire_days=30,
        postgres_password="test",
        object_storage_secret_key="test",
    )


@pytest.fixture
def token_service(token_settings: Settings) -> TokenService:
    """Configured TokenService instance."""
    return TokenService(token_settings)


def make_subject() -> AccessTokenSubject:
    """Create a complete access-token subject."""
    return AccessTokenSubject(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )


def make_payload(settings: Settings, subject: AccessTokenSubject) -> dict[str, Any]:
    """Create a valid access-token payload for negative tests."""
    now = datetime.now(UTC)
    return {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(subject.user_id),
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=15),
        "jti": str(uuid.uuid4()),
        "sid": str(subject.session_id),
        "tid": str(subject.tenant_id),
        "oid": str(subject.organization_id),
    }


def encode_payload(settings: Settings, payload: dict[str, Any]) -> str:
    """Sign a payload with the test settings."""
    return jwt.encode(payload, settings.jwt_signing_secret, algorithm=settings.jwt_algorithm)


class CustomSigningProvider:
    """Test signing provider proving TokenService depends on the protocol."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.encode_called = False
        self.decode_called = False

    def encode(self, payload: dict[str, Any]) -> str:
        self.encode_called = True
        self.payload = payload
        return "custom.signed.token"

    def decode(self, token: str) -> dict[str, Any]:
        self.decode_called = True
        assert token == "custom.signed.token"  # noqa: S105
        return self.payload


class TestAccessToken:
    """Verify JWT creation and validation."""

    def test_create_and_verify_success(self, token_service: TokenService) -> None:
        subject = make_subject()

        token = token_service.create_access_token(subject)

        assert isinstance(token, str)
        assert len(token) > 0

        payload = token_service.verify_access_token(token)
        assert payload["sub"] == str(subject.user_id)
        assert payload["tid"] == str(subject.tenant_id)
        assert payload["oid"] == str(subject.organization_id)
        assert payload["sid"] == str(subject.session_id)
        assert payload["iss"] == token_service.settings.jwt_issuer
        assert payload["aud"] == token_service.settings.jwt_audience
        assert "exp" in payload
        assert "iat" in payload
        assert "nbf" in payload
        assert "jti" in payload

    def test_access_token_does_not_include_authorization_claims(
        self, token_service: TokenService
    ) -> None:
        token = token_service.create_access_token(make_subject())

        payload = token_service.verify_access_token(token)

        assert "role" not in payload
        assert "roles" not in payload
        assert "permissions" not in payload
        assert "mailbox_ids" not in payload
        assert "provider_token" not in payload

    def test_verify_expired_token(self, token_service: TokenService) -> None:
        with patch.object(token_service.settings, "jwt_access_token_expire_minutes", -2):
            token = token_service.create_access_token(make_subject())

        time.sleep(0.1)

        with pytest.raises(TokenExpiredError):
            token_service.verify_access_token(token)

    def test_verify_invalid_signature(self, token_service: TokenService) -> None:
        token = token_service.create_access_token(make_subject())
        tampered_token = token[:-5] + "aaaaa"

        with pytest.raises(TokenInvalidError):
            token_service.verify_access_token(tampered_token)

    def test_verify_wrong_secret(self, token_service: TokenService) -> None:
        token = token_service.create_access_token(make_subject())
        other_settings = token_service.settings.model_copy(
            update={"jwt_signing_secret": "different-test-secret-minimum-32-bytes"}
        )
        other_service = TokenService(other_settings)

        with pytest.raises(TokenInvalidError):
            other_service.verify_access_token(token)

    def test_verify_wrong_issuer(self, token_service: TokenService) -> None:
        token = token_service.create_access_token(make_subject())
        other_settings = token_service.settings.model_copy(
            update={"jwt_issuer": "different-issuer"}
        )
        other_service = TokenService(other_settings)

        with pytest.raises(TokenInvalidError):
            other_service.verify_access_token(token)

    def test_verify_wrong_audience(self, token_service: TokenService) -> None:
        token = token_service.create_access_token(make_subject())
        other_settings = token_service.settings.model_copy(
            update={"jwt_audience": "different-audience"}
        )
        other_service = TokenService(other_settings)

        with pytest.raises(TokenInvalidError):
            other_service.verify_access_token(token)

    def test_verify_malformed_token(self, token_service: TokenService) -> None:
        with pytest.raises(TokenInvalidError):
            token_service.verify_access_token("not.a.jwt")

    def test_verify_missing_project_required_claim(self, token_settings: Settings) -> None:
        payload = make_payload(token_settings, make_subject())
        del payload["sid"]
        token = encode_payload(token_settings, payload)

        with pytest.raises(TokenInvalidError):
            TokenService(token_settings).verify_access_token(token)

    def test_verify_forbidden_authorization_claim(self, token_settings: Settings) -> None:
        payload = make_payload(token_settings, make_subject())
        payload["permissions"] = ["tenant.admin"]
        token = encode_payload(token_settings, payload)

        with pytest.raises(TokenInvalidError):
            TokenService(token_settings).verify_access_token(token)

    def test_verify_alg_none_token_is_rejected(self, token_settings: Settings) -> None:
        payload = make_payload(token_settings, make_subject())
        token = jwt.encode(payload, key="", algorithm="none")

        with pytest.raises(TokenInvalidError):
            TokenService(token_settings).verify_access_token(token)

    def test_verify_future_nbf_token_is_rejected(self, token_settings: Settings) -> None:
        payload = make_payload(token_settings, make_subject())
        future = datetime.now(UTC) + timedelta(minutes=10)
        payload["nbf"] = future
        token = encode_payload(token_settings, payload)

        with pytest.raises(TokenInvalidError):
            TokenService(token_settings).verify_access_token(token)

    def test_custom_signing_provider_can_be_injected(self, token_settings: Settings) -> None:
        provider = CustomSigningProvider(make_payload(token_settings, make_subject()))
        service = TokenService(token_settings, signing_provider=provider)

        token = service.create_access_token(make_subject())
        payload = service.verify_access_token(token)

        assert token == "custom.signed.token"  # noqa: S105
        assert payload == provider.payload
        assert provider.encode_called is True
        assert provider.decode_called is True
        assert isinstance(provider, SigningProvider)


class TestTokenConfiguration:
    """Verify token configuration fails closed."""

    def test_unsupported_algorithm_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Unsupported JWT algorithm"):
            Settings(
                app_env=Environment.TESTING,
                jwt_algorithm="HS512",
                jwt_signing_secret="test-signing-secret-change-me-32-bytes",
                postgres_password="test",
                object_storage_secret_key="test",
            )

    def test_weak_hs256_secret_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="at least 32 bytes"):
            Settings(
                app_env=Environment.TESTING,
                jwt_signing_secret="too-short",
                postgres_password="test",
                object_storage_secret_key="test",
            )

    def test_default_secret_is_rejected_in_production(self) -> None:
        with pytest.raises(ValidationError, match="development default"):
            Settings(
                app_env=Environment.PRODUCTION,
                jwt_signing_secret="dev-only-change-me-minimum-32-bytes",
                postgres_password="test",
                object_storage_secret_key="test",
            )


class TestRefreshToken:
    """Verify refresh token generation and hashing."""

    def test_generate_refresh_token(self, token_service: TokenService) -> None:
        pair = token_service.generate_refresh_token()

        assert pair.plaintext
        assert pair.hash_val
        assert pair.plaintext != pair.hash_val
        assert len(pair.plaintext) >= 43

    def test_hash_refresh_token_is_deterministic(self, token_service: TokenService) -> None:
        token = "test-refresh-token-value"  # noqa: S105
        hash1 = token_service.hash_refresh_token(token)
        hash2 = token_service.hash_refresh_token(token)

        assert hash1 == hash2
        assert len(hash1) == 64

    def test_generated_hash_matches_manual_hash(self, token_service: TokenService) -> None:
        pair = token_service.generate_refresh_token()
        manual_hash = token_service.hash_refresh_token(pair.plaintext)

        assert pair.hash_val == manual_hash

    def test_generate_refresh_token_is_unique(self, token_service: TokenService) -> None:
        pair1 = token_service.generate_refresh_token()
        pair2 = token_service.generate_refresh_token()

        assert pair1.plaintext != pair2.plaintext
        assert pair1.hash_val != pair2.hash_val
