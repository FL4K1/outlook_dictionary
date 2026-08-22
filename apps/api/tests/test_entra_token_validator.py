"""Unit tests for Entra ID token validator and JWKS client."""

from __future__ import annotations

import base64
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.common.config import Settings
from mip_providers.identity.entra import EntraIdentityProviderAuth
from mip_providers.identity.jwks import CachedJWKS, EntraTokenValidator, JWKKey, JWKSClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    public_numbers = public_key.public_numbers()
    n = (
        base64.urlsafe_b64encode(
            public_numbers.n.to_bytes((public_numbers.n.bit_length() + 7) // 8, "big")
        )
        .decode("ascii")
        .rstrip("=")
    )
    e = (
        base64.urlsafe_b64encode(
            public_numbers.e.to_bytes((public_numbers.e.bit_length() + 7) // 8, "big")
        )
        .decode("ascii")
        .rstrip("=")
    )
    return private_key, n, e


def _make_id_token(
    *,
    kid: str = "test-kid",
    iss: str = "https://login.microsoftonline.com/tenant-id/v2.0",
    aud: str = "client-id",
    sub: str = "user-sub",
    nonce: str = "test-nonce",
    tid: str = "tenant-id",
    exp: int | None = None,
    nbf: int | None = None,
    iat: int | None = None,
    private_key=None,
) -> str:
    now = int(time.time())
    payload = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "nonce": nonce,
        "tid": tid,
        "iat": iat if iat is not None else now,
        "exp": exp if exp is not None else now + 3600,
    }
    if nbf is not None:
        payload["nbf"] = nbf
    if private_key is not None:
        return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})
    return jwt.encode(payload, "secret", algorithm="HS256", headers={"kid": kid})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(
        app_env="testing",
        entra_jwks_endpoint="https://login.microsoftonline.com/common/discovery/v2.0/keys",
        entra_issuer="https://login.microsoftonline.com/tenant-id/v2.0",
        entra_audience="client-id",
        entra_tenant_id="tenant-id",
        entra_clock_skew_seconds=60,
    )


@pytest.fixture
def mock_jwks_client() -> MagicMock:
    client = MagicMock(spec=JWKSClient)
    client.get_key_by_kid = AsyncMock()
    return client


@pytest.fixture
def validator(mock_settings: Settings, mock_jwks_client: MagicMock) -> EntraTokenValidator:
    validator = EntraTokenValidator(mock_settings)
    validator._jwks_client = mock_jwks_client
    return validator


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEntraTokenValidator:
    """Tests for EntraTokenValidator."""

    async def test_validate_id_token_success(
        self,
        validator: EntraTokenValidator,
        mock_jwks_client: MagicMock,
    ) -> None:
        private_key, n, e = _rsa_keypair()
        mock_jwks_client.get_key_by_kid.return_value = JWKKey(
            kid="test-kid",
            kty="RSA",
            alg="RS256",
            n=n,
            e=e,
        )
        id_token = _make_id_token(private_key=private_key)
        payload = await validator.validate_id_token(id_token, "test-nonce")
        assert payload["sub"] == "user-sub"
        assert payload["tid"] == "tenant-id"

    async def test_validate_id_token_empty(self, validator: EntraTokenValidator) -> None:
        with pytest.raises(ValueError, match="ID token is empty"):
            await validator.validate_id_token("", "test-nonce")

    async def test_validate_id_token_missing_kid(self, validator: EntraTokenValidator) -> None:
        id_token = jwt.encode({"sub": "user"}, "secret", algorithm="HS256")
        with pytest.raises(ValueError, match="ID token missing kid claim"):
            await validator.validate_id_token(id_token, "test-nonce")

    async def test_validate_id_token_wrong_algorithm(self, validator: EntraTokenValidator) -> None:
        id_token = jwt.encode(
            {"sub": "user"}, "secret", algorithm="HS256", headers={"kid": "test-kid"}
        )
        with pytest.raises(ValueError, match="Unsupported ID token algorithm"):
            await validator.validate_id_token(id_token, "test-nonce")

    async def test_validate_id_token_key_not_found(
        self,
        validator: EntraTokenValidator,
        mock_jwks_client: MagicMock,
    ) -> None:
        private_key, _, _ = _rsa_keypair()
        mock_jwks_client.get_key_by_kid.return_value = None
        id_token = _make_id_token(private_key=private_key)
        with pytest.raises(ValueError, match="JWKS key not found for kid"):
            await validator.validate_id_token(id_token, "test-nonce")

    async def test_validate_id_token_nonce_mismatch(
        self,
        validator: EntraTokenValidator,
        mock_jwks_client: MagicMock,
    ) -> None:
        private_key, n, e = _rsa_keypair()
        mock_jwks_client.get_key_by_kid.return_value = JWKKey(
            kid="test-kid", kty="RSA", alg="RS256", n=n, e=e
        )
        id_token = _make_id_token(private_key=private_key)
        with pytest.raises(ValueError, match="ID token nonce mismatch"):
            await validator.validate_id_token(id_token, "wrong-nonce")

    async def test_validate_id_token_missing_nonce(
        self,
        validator: EntraTokenValidator,
        mock_jwks_client: MagicMock,
    ) -> None:
        private_key, n, e = _rsa_keypair()
        mock_jwks_client.get_key_by_kid.return_value = JWKKey(
            kid="test-kid", kty="RSA", alg="RS256", n=n, e=e
        )
        payload = {
            "iss": "https://login.microsoftonline.com/tenant-id/v2.0",
            "aud": "client-id",
            "sub": "user-sub",
            "tid": "tenant-id",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        id_token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-kid"})
        with pytest.raises(ValueError, match="ID token nonce mismatch"):
            await validator.validate_id_token(id_token, "test-nonce")

    async def test_validate_id_token_tid_mismatch(
        self,
        validator: EntraTokenValidator,
        mock_jwks_client: MagicMock,
    ) -> None:
        private_key, n, e = _rsa_keypair()
        mock_jwks_client.get_key_by_kid.return_value = JWKKey(
            kid="test-kid", kty="RSA", alg="RS256", n=n, e=e
        )
        id_token = _make_id_token(tid="wrong-tenant", private_key=private_key)
        with pytest.raises(ValueError, match="ID token tid mismatch"):
            await validator.validate_id_token(id_token, "test-nonce")

    async def test_validate_id_token_missing_sub(
        self,
        validator: EntraTokenValidator,
        mock_jwks_client: MagicMock,
    ) -> None:
        private_key, n, e = _rsa_keypair()
        mock_jwks_client.get_key_by_kid.return_value = JWKKey(
            kid="test-kid", kty="RSA", alg="RS256", n=n, e=e
        )
        payload = {
            "iss": "https://login.microsoftonline.com/tenant-id/v2.0",
            "aud": "client-id",
            "nonce": "test-nonce",
            "tid": "tenant-id",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        id_token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "test-kid"})
        with pytest.raises(ValueError, match="ID token missing required sub claim"):
            await validator.validate_id_token(id_token, "test-nonce")

    async def test_validate_id_token_expired(
        self,
        validator: EntraTokenValidator,
        mock_jwks_client: MagicMock,
    ) -> None:
        private_key, n, e = _rsa_keypair()
        mock_jwks_client.get_key_by_kid.return_value = JWKKey(
            kid="test-kid", kty="RSA", alg="RS256", n=n, e=e
        )
        id_token = _make_id_token(exp=int(time.time()) - 120, private_key=private_key)
        with pytest.raises(ValueError, match="ID token expired"):
            await validator.validate_id_token(id_token, "test-nonce")

    async def test_validate_id_token_future_nbf(
        self,
        validator: EntraTokenValidator,
        mock_jwks_client: MagicMock,
    ) -> None:
        private_key, n, e = _rsa_keypair()
        mock_jwks_client.get_key_by_kid.return_value = JWKKey(
            kid="test-kid", kty="RSA", alg="RS256", n=n, e=e
        )
        id_token = _make_id_token(nbf=int(time.time()) + 120, private_key=private_key)
        with pytest.raises(ValueError, match="ID token claims invalid"):
            await validator.validate_id_token(id_token, "test-nonce")

    async def test_validate_id_token_future_iat(
        self,
        validator: EntraTokenValidator,
        mock_jwks_client: MagicMock,
    ) -> None:
        private_key, n, e = _rsa_keypair()
        mock_jwks_client.get_key_by_kid.return_value = JWKKey(
            kid="test-kid", kty="RSA", alg="RS256", n=n, e=e
        )
        id_token = _make_id_token(iat=int(time.time()) + 120, private_key=private_key)
        with pytest.raises(ValueError, match="ID token iat is materially in the future"):
            await validator.validate_id_token(id_token, "test-nonce")


class TestJWKSClient:
    """Tests for JWKSClient."""

    async def test_get_keys_caches(self, mock_settings: Settings) -> None:
        client = JWKSClient(mock_settings)
        client._cache = CachedJWKS(keys=[], fetched_at=time.time())
        keys = await client.get_keys()
        assert keys == []

    async def test_get_keys_refreshes_when_expired(self, mock_settings: Settings) -> None:
        client = JWKSClient(mock_settings)
        client._cache = CachedJWKS(keys=[], fetched_at=time.time() - 3601)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "keys": [{"kid": "k1", "kty": "RSA", "alg": "RS256", "n": "n", "e": "e"}]
        }
        mock_response.raise_for_status = MagicMock()
        client._client = MagicMock()
        client._client.get = AsyncMock(return_value=mock_response)
        keys = await client.get_keys()
        assert len(keys) == 1
        assert keys[0].kid == "k1"

    async def test_get_key_by_kid_found(self, mock_settings: Settings) -> None:
        client = JWKSClient(mock_settings)
        client._cache = CachedJWKS(
            keys=[JWKKey(kid="k1", kty="RSA", alg="RS256", n="n", e="e")],
            fetched_at=time.time(),
        )
        key = await client.get_key_by_kid("k1")
        assert key is not None
        assert key.kid == "k1"

    async def test_get_key_by_kid_not_found(self, mock_settings: Settings) -> None:
        client = JWKSClient(mock_settings)
        client._cache = CachedJWKS(keys=[], fetched_at=time.time())
        key = await client.get_key_by_kid("missing")
        assert key is None


class TestEntraIdentityProviderAuth:
    """Tests for EntraIdentityProviderAuth token exchange."""

    async def test_token_endpoint_includes_redirect_uri(
        self,
        mock_settings: Settings,
    ) -> None:
        mock_encryption = MagicMock()
        mock_encryption.key_id = "test-key"
        auth = EntraIdentityProviderAuth(settings=mock_settings, encryption_service=mock_encryption)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mip_providers.identity.entra.httpx.AsyncClient", return_value=mock_client):
            result = await auth._token_endpoint_request(
                grant_type="authorization_code",
                code="test-code",
                code_verifier="test-verifier",
            )

        assert result["access_token"] == "new-access-token"  # noqa: S105
        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        posted_data = (
            call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs.get("data")
        )
        assert posted_data is not None
        assert posted_data["redirect_uri"] == mock_settings.entra_redirect_uri
        assert posted_data["client_id"] == mock_settings.entra_client_id
        assert posted_data["grant_type"] == "authorization_code"
