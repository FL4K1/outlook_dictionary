"""Token management service.

Access tokens are short-lived JWTs with minimal identity/session claims.
Refresh tokens are opaque 256-bit random values and are only stored as
SHA-256 hashes. Signing is isolated behind a provider interface so HS256 can
be used locally today and asymmetric/JWKS signing can be introduced later
without changing callers.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from app.auth.exceptions import TokenExpiredError, TokenInvalidError
from app.common.logging import get_logger

if TYPE_CHECKING:
    from app.common.config import Settings

logger = get_logger(__name__)

ALLOWED_JWT_ALGORITHMS = frozenset({"HS256"})
REQUIRED_ACCESS_TOKEN_CLAIMS = frozenset(
    {"iss", "aud", "sub", "iat", "nbf", "exp", "jti", "sid", "tid", "oid"}
)
FORBIDDEN_ACCESS_TOKEN_CLAIMS = frozenset(
    {"role", "roles", "permissions", "mailbox_ids", "provider_token", "provider_refresh_token"}
)


@dataclass(frozen=True, slots=True)
class RefreshTokenPair:
    """A generated refresh token and its cryptographic hash."""

    plaintext: str
    hash_val: str


@dataclass(frozen=True, slots=True)
class AccessTokenSubject:
    """Stable identity and session claims used to issue access tokens."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID


@runtime_checkable
class SigningProvider(Protocol):
    """Provider abstraction for JWT signing and verification."""

    def encode(self, payload: dict[str, Any]) -> str:
        """Sign and encode a JWT payload."""
        ...

    def decode(self, token: str) -> dict[str, Any]:
        """Verify and decode a JWT."""
        ...


class HmacSigningProvider:
    """Initial HS256 signing provider for the monolithic API service."""

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.jwt_signing_secret
        self._algorithm = settings.jwt_algorithm
        self._issuer = settings.jwt_issuer
        self._audience = settings.jwt_audience
        self._leeway = settings.jwt_clock_skew_seconds
        if self._algorithm not in ALLOWED_JWT_ALGORITHMS:
            msg = f"Unsupported JWT algorithm: {self._algorithm}"
            raise ValueError(msg)

    def encode(self, payload: dict[str, Any]) -> str:
        """Sign and encode a JWT payload."""
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def decode(self, token: str) -> dict[str, Any]:
        """Verify and decode a JWT."""
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm != self._algorithm or algorithm not in ALLOWED_JWT_ALGORITHMS:
            msg = "Unsupported JWT algorithm."
            raise InvalidTokenError(msg)

        return jwt.decode(
            token,
            self._secret,
            algorithms=[self._algorithm],
            issuer=self._issuer,
            audience=self._audience,
            leeway=self._leeway,
            options={
                "require": ["exp", "iat", "nbf", "iss", "aud", "sub", "jti"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_nbf": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )


class TokenService:
    """Service for managing JWTs and opaque refresh tokens."""

    def __init__(
        self,
        settings: Settings,
        signing_provider: SigningProvider | None = None,
    ) -> None:
        self.settings = settings
        self.signing_provider = signing_provider or HmacSigningProvider(settings)

    def create_access_token(self, subject: AccessTokenSubject) -> str:
        """Create a short-lived access token.

        Claims are intentionally minimal. Roles, permissions, mailbox IDs, and
        provider tokens are resolved server-side and never embedded in JWTs.
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=self.settings.jwt_access_token_expire_minutes)

        payload: dict[str, Any] = {
            "iss": self.settings.jwt_issuer,
            "aud": self.settings.jwt_audience,
            "sub": str(subject.user_id),
            "iat": now,
            "nbf": now,
            "exp": expires_at,
            "jti": str(uuid.uuid4()),
            "sid": str(subject.session_id),
            "tid": str(subject.tenant_id),
            "oid": str(subject.organization_id),
        }

        try:
            return self.signing_provider.encode(payload)
        except Exception as exc:
            logger.error("token_signing_failed", error=str(exc))
            msg = "Failed to sign JWT."
            raise RuntimeError(msg) from exc

    def verify_access_token(self, token: str) -> dict[str, Any]:
        """Verify and decode an access token."""
        try:
            payload = self.signing_provider.decode(token)
            self._validate_access_token_claims(payload)
            return payload
        except ExpiredSignatureError as exc:
            raise TokenExpiredError() from exc
        except InvalidTokenError as exc:
            raise TokenInvalidError() from exc
        except Exception as exc:
            logger.warning("token_verification_unexpected_error", error=str(exc))
            raise TokenInvalidError() from exc

    def generate_refresh_token(self) -> RefreshTokenPair:
        """Generate a cryptographically secure opaque refresh token."""
        plaintext = secrets.token_urlsafe(32)
        hash_val = self.hash_refresh_token(plaintext)
        return RefreshTokenPair(plaintext=plaintext, hash_val=hash_val)

    def hash_refresh_token(self, token: str) -> str:
        """Hash a plaintext refresh token for database comparison."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _validate_access_token_claims(self, payload: dict[str, Any]) -> None:
        """Validate project-specific access-token claim invariants."""
        missing_claims = REQUIRED_ACCESS_TOKEN_CLAIMS - payload.keys()
        if missing_claims:
            msg = f"Missing required JWT claims: {sorted(missing_claims)}"
            raise InvalidTokenError(msg)

        forbidden_claims = FORBIDDEN_ACCESS_TOKEN_CLAIMS & payload.keys()
        if forbidden_claims:
            msg = f"Forbidden JWT claims present: {sorted(forbidden_claims)}"
            raise InvalidTokenError(msg)
