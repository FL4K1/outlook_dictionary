"""JWKS client for Microsoft Entra ID token validation.

Fetches and caches JSON Web Key Sets from the Entra ID discovery endpoint.
Validates RS256 signatures and provides key lookup by kid.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTClaimsError, JWTError
from pydantic import BaseModel

from app.common.config import Settings, get_settings
from app.common.logging import get_logger

logger = get_logger(__name__)


class JWKKey(BaseModel):
    """Parsed JWK key material."""

    kid: str
    kty: str
    alg: str
    k: str | None = None
    n: str | None = None
    e: str | None = None
    x5c: list[str] | None = None
    x5t: str | None = None


class CachedJWKS:
    """Cached JWKS response with TTL."""

    def __init__(self, keys: list[JWKKey], fetched_at: float, ttl_seconds: int = 3600) -> None:
        self.keys = keys
        self.fetched_at = fetched_at
        self.ttl_seconds = ttl_seconds

    def is_expired(self) -> bool:
        return time.time() - self.fetched_at > self.ttl_seconds


class JWKSClient:
    """Client for fetching and caching Entra ID JWKS.

    Handles key rotation transparently by re-fetching when the cache expires.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._cache: CachedJWKS | None = None
        self._client = httpx.AsyncClient(timeout=10.0)

    async def get_keys(self) -> list[JWKKey]:
        """Return cached JWKS keys, re-fetching if expired."""
        if self._cache is None or self._cache.is_expired():
            await self._fetch_jwks()
        return self._cache.keys

    async def get_key_by_kid(self, kid: str) -> JWKKey | None:
        """Find a specific key by its kid."""
        keys = await self.get_keys()
        for key in keys:
            if key.kid == kid:
                return key
        return None

    async def _fetch_jwks(self) -> None:
        """Fetch JWKS from the Entra ID discovery endpoint."""
        jwks_url = self._settings.entra_jwks_endpoint
        if not jwks_url:
            msg = "Entra JWKS endpoint is not configured."
            raise ValueError(msg)

        logger.info("fetching_jwks", url=jwks_url)
        response = await self._client.get(jwks_url)
        response.raise_for_status()
        data = response.json()

        keys: list[JWKKey] = []
        for key_data in data.get("keys", []):
            keys.append(JWKKey(**key_data))

        self._cache = CachedJWKS(keys=keys, fetched_at=time.time())
        logger.info("jwks_fetched", key_count=len(keys))

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> JWKSClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


class EntraTokenValidator:
    """Validates Entra ID ID tokens using JWKS.

    Enforces all required claim validations per AD-PR13-009.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._jwks_client = JWKSClient(self._settings)

    async def validate_id_token(self, id_token: str, nonce: str) -> dict[str, Any]:
        """Validate an Entra ID ID token.

        Args:
            id_token: The raw ID token string from Entra ID.
            nonce: The expected nonce value from server-side state.

        Returns:
            The validated token payload.

        Raises:
            ValueError: If validation fails for any reason.
        """
        if not id_token:
            msg = "ID token is empty."
            raise ValueError(msg)

        try:
            unverified_header = jwt.get_unverified_header(id_token)
        except JWTError as exc:
            msg = f"Failed to parse ID token header: {exc}"
            raise ValueError(msg) from exc

        kid = unverified_header.get("kid")
        if not kid:
            msg = "ID token missing kid claim."
            raise ValueError(msg)

        alg = unverified_header.get("alg")
        if alg != "RS256":
            msg = f"Unsupported ID token algorithm: {alg}. Only RS256 is accepted."
            raise ValueError(msg)

        jwk_key = await self._jwks_client.get_key_by_kid(kid)
        if jwk_key is None:
            msg = f"JWKS key not found for kid: {kid}."
            raise ValueError(msg)

        public_key = self._build_public_key(jwk_key)
        expected_issuer = self._settings.entra_issuer
        if not expected_issuer:
            msg = "Entra issuer is not configured."
            raise ValueError(msg)

        expected_audience = self._settings.entra_audience
        if not expected_audience:
            msg = "Entra audience is not configured."
            raise ValueError(msg)

        expected_tid = self._settings.entra_tenant_id
        clock_skew = self._settings.entra_clock_skew_seconds

        try:
            payload = jwt.decode(
                id_token,
                public_key,
                algorithms=["RS256"],
                audience=expected_audience,
                issuer=expected_issuer,
                options={"leeway": clock_skew},
            )
        except JWTClaimsError as exc:
            msg = f"ID token claims invalid: {exc}"
            raise ValueError(msg) from exc
        except ExpiredSignatureError as exc:
            msg = f"ID token expired: {exc}"
            raise ValueError(msg) from exc
        except JWTError as exc:
            msg = f"ID token validation failed: {exc}"
            raise ValueError(msg) from exc

        token_nonce = payload.get("nonce")
        if not token_nonce or token_nonce != nonce:
            msg = "ID token nonce mismatch."
            raise ValueError(msg)

        token_tid = payload.get("tid")
        if expected_tid and token_tid != expected_tid:
            msg = f"ID token tid mismatch: expected {expected_tid}, got {token_tid}."
            raise ValueError(msg)

        sub = payload.get("sub")
        if not sub:
            msg = "ID token missing required sub claim."
            raise ValueError(msg)

        now = time.time()
        iat = payload.get("iat")
        if iat is not None and iat > now + clock_skew:
            msg = f"ID token iat is materially in the future: {iat} > {now + clock_skew}."
            raise ValueError(msg)

        return payload

    def _build_public_key(self, jwk_key: JWKKey) -> dict[str, Any]:
        """Build a public key dict from JWK material for jose verification."""
        if jwk_key.kty == "RSA":
            return {
                "kty": "RSA",
                "kid": jwk_key.kid,
                "n": jwk_key.n,
                "e": jwk_key.e,
            }
        msg = f"Unsupported key type: {jwk_key.kty}. Only RSA is accepted."
        raise ValueError(msg)

    async def close(self) -> None:
        await self._jwks_client.close()

    async def __aenter__(self) -> EntraTokenValidator:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
