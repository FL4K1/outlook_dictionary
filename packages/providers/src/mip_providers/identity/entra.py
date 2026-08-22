"""Microsoft Entra ID identity provider adapter.

Implements the IdentityProviderAuth protocol for Microsoft Entra ID OAuth 2.0
Authorization Code + PKCE with OIDC ID-token validation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.common.config import Settings, get_settings
from app.common.encryption import EncryptionService
from mip_providers.identity.base import (
    IdentityVerificationResult,
    ProviderCredentialSet,
)

from .jwks import EntraTokenValidator


class EntraIdentityProviderAuth:
    """Microsoft Entra ID identity provider adapter.

    Implements IdentityProviderAuth using Authorization Code + PKCE + OIDC.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        encryption_service: EncryptionService | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._encryption = encryption_service or EncryptionService()
        self._token_validator = EntraTokenValidator(self._settings)

    async def get_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str:
        """Generate the Entra ID authorization URL with PKCE parameters."""
        params = {
            "client_id": self._settings.entra_client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(self._settings.entra_scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        base_url = f"https://login.microsoftonline.com/{self._settings.entra_tenant_id}/oauth2/v2.0/authorize"
        return f"{base_url}?{urlencode(params)}"

    async def validate_callback(
        self,
        code: str,
        state: str,
        expected_state: str,
        expected_nonce: str,
        code_verifier: str,
    ) -> IdentityVerificationResult:
        """Validate callback, exchange authorization code, verify ID token."""
        if state != expected_state:
            msg = "State mismatch."
            raise ValueError(msg)

        token_data = await self._exchange_code(code, code_verifier)
        id_token = token_data.get("id_token")
        if not id_token:
            msg = "Missing id_token in token response."
            raise ValueError(msg)

        payload = await self._token_validator.validate_id_token(id_token, expected_nonce)

        return IdentityVerificationResult(
            provider_user_id=payload.get("sub", ""),
            provider_email=payload.get("email") or payload.get("preferred_username"),
            provider_metadata={
                "tid": payload.get("tid", ""),
                "oid": payload.get("oid", ""),
                "name": payload.get("name", ""),
                "scopes": self._settings.entra_scopes,
            },
            access_token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            token_expires_at=self._compute_expiry(token_data.get("expires_in", 3600)),
            scopes=token_data.get("scope", "").split() if token_data.get("scope") else [],
        )

    async def refresh_credentials(self, identity: object) -> ProviderCredentialSet:
        """Refresh expired provider credentials for an existing identity.

        Note: Background refresh is deferred per PR-1.3 design.
        On-demand refresh requires IdentityProviderCredential repository access.
        """
        msg = "Provider credential refresh is not implemented in PR-1.3 Phase 2."
        raise NotImplementedError(msg)

    async def _exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        """Exchange authorization code for tokens."""
        return await self._token_endpoint_request(
            grant_type="authorization_code",
            code=code,
            code_verifier=code_verifier,
        )

    async def _token_endpoint_request(self, **kwargs: Any) -> dict[str, Any]:
        """Make a token endpoint request to Entra ID.

        Uses PKCE; no client_secret is sent per AD-PR13-003.
        """
        token_url = (
            f"https://login.microsoftonline.com/{self._settings.entra_tenant_id}/oauth2/v2.0/token"
        )
        data = {
            "client_id": self._settings.entra_client_id,
            "redirect_uri": self._settings.entra_redirect_uri,
            **kwargs,
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _compute_expiry(seconds: int) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=seconds)
