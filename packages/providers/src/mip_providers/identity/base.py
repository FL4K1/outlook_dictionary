"""Identity provider abstractions for external IdP authentication.

This package defines the protocol interface for identity provider authentication
(separate from mail provider authentication) and canonical data models for
identity verification results and provider credential sets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class IdentityVerificationResult:
    """Result of external identity provider verification.

    Returned by IdentityProviderAuth.validate_callback after successful
    OAuth/OIDC callback processing.
    """

    provider_user_id: str
    provider_email: str | None = None
    provider_metadata: dict[str, object] = field(default_factory=dict)
    access_token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    scopes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProviderCredentialSet:
    """OAuth tokens returned by the identity provider."""

    access_token: str
    refresh_token: str
    expires_at: datetime
    scopes: list[str] = field(default_factory=list)


@runtime_checkable
class IdentityProviderAuth(Protocol):
    """Interface for authenticating with an external identity provider.

    This protocol is separate from MailAuthProvider. It covers identity
    authentication, token validation, and user resolution for platform
    identity binding (e.g., Microsoft Entra ID OIDC).
    """

    async def get_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        """Generate the OAuth authorization URL with PKCE parameters."""
        ...

    async def validate_callback(
        self,
        code: str,
        state: str,
        expected_state: str,
        expected_nonce: str,
        code_verifier: str,
    ) -> IdentityVerificationResult:
        """Validate callback, exchange authorization code, verify ID token.

        Args:
            code: Authorization code from the IdP callback.
            state: State parameter from the callback.
            expected_state: Server-side stored state for validation.
            expected_nonce: Server-side stored nonce for validation.
            code_verifier: PKCE code verifier for token exchange.

        Returns:
            IdentityVerificationResult with verified identity claims.

        Raises:
            ValueError: If validation fails (invalid state, nonce, token, etc.).
        """
        ...

    async def refresh_credentials(
        self,
        identity: object,  # Identity model instance — avoid circular import
    ) -> ProviderCredentialSet:
        """Refresh expired provider credentials for an existing identity."""
        ...
