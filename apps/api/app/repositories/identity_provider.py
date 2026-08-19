"""Repositories for identity provider authentication entities.

Provides persistence operations for:
- OAuthState: CSRF/replay protection state
- IdentityProviderCredential: Encrypted provider tokens
- EntraTenantMapping: Entra tenant to platform Tenant mapping
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from app.repositories.base import BaseRepository
from mip_models.identity_provider import (
    EntraTenantMapping,
    IdentityProviderCredential,
    OAuthState,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


class OAuthStateRepository(BaseRepository[OAuthState]):
    """Repository for OAuthState entities.

    Manages server-side database-backed state for OAuth CSRF/replay protection.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(OAuthState, session)

    async def get_by_state(self, state: str) -> OAuthState | None:
        """Look up an OAuth state record by its state value."""
        result = await self.session.execute(select(OAuthState).where(OAuthState.state == state))
        return result.scalars().first()

    async def create_state(
        self,
        state: str,
        nonce: str,
        code_verifier: str,
        provider: str,
        expires_at: datetime,
        request_id: str | None = None,
    ) -> OAuthState:
        """Create a new OAuth state record.

        Args:
            state: Cryptographic state token.
            nonce: Cryptographic nonce for ID token replay protection.
            code_verifier: PKCE code verifier (never exposed to client).
            provider: Identity provider type (e.g., "microsoft").
            expires_at: State expiration timestamp.
            request_id: Correlation request ID for audit trail.

        Returns:
            The newly created OAuthState record.
        """
        return await self.create(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            provider=provider,
            expires_at=expires_at,
            consumed_at=None,
            request_id=request_id,
        )

    async def consume_state(self, state: str) -> OAuthState | None:
        """Atomically consume an OAuth state record.

        Marks the state as consumed by setting consumed_at to now.
        Only consumes states that are not already consumed and not expired.

        Args:
            state: The state token to consume.

        Returns:
            The consumed OAuthState record, or None if not found/expired/already consumed.
        """
        now = datetime.now(UTC)
        stmt = (
            update(OAuthState)
            .where(
                OAuthState.state == state,
                OAuthState.consumed_at.is_(None),
                OAuthState.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(OAuthState)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalars().first()

    async def get_unconsumed(self, state: str) -> OAuthState | None:
        """Get an unconsumed, non-expired state record."""
        now = datetime.now(UTC)
        result = await self.session.execute(
            select(OAuthState).where(
                OAuthState.state == state,
                OAuthState.consumed_at.is_(None),
                OAuthState.expires_at > now,
            )
        )
        return result.scalars().first()


class IdentityProviderCredentialRepository(BaseRepository[IdentityProviderCredential]):
    """Repository for IdentityProviderCredential entities.

    Manages encrypted provider credentials with tenant isolation.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(IdentityProviderCredential, session)

    async def get_by_identity_id(self, identity_id: uuid.UUID) -> IdentityProviderCredential | None:
        """Look up credentials by identity ID."""
        result = await self.session.execute(
            select(IdentityProviderCredential).where(
                IdentityProviderCredential.identity_id == identity_id
            )
        )
        return result.scalars().first()

    async def get_by_tenant_id(self, tenant_id: uuid.UUID) -> list[IdentityProviderCredential]:
        """Get all credentials for a tenant (admin/operational use)."""
        result = await self.session.execute(
            select(IdentityProviderCredential).where(
                IdentityProviderCredential.tenant_id == tenant_id
            )
        )
        return list(result.scalars().all())

    async def revoke(self, credential_id: uuid.UUID, revoked_at: datetime) -> bool:
        """Soft-revoke a credential by setting revoked_at."""
        stmt = (
            update(IdentityProviderCredential)
            .where(
                IdentityProviderCredential.id == credential_id,
                IdentityProviderCredential.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0  # type: ignore[attr-defined]


class EntraTenantMappingRepository(BaseRepository[EntraTenantMapping]):
    """Repository for EntraTenantMapping entities.

    Manages the mapping from Entra tenant IDs to platform Tenant IDs.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(EntraTenantMapping, session)

    async def get_by_entra_tenant_id(self, entra_tenant_id: str) -> EntraTenantMapping | None:
        """Look up a mapping by Entra tenant ID."""
        result = await self.session.execute(
            select(EntraTenantMapping).where(EntraTenantMapping.entra_tenant_id == entra_tenant_id)
        )
        return result.scalars().first()

    async def get_active_mappings(self) -> list[EntraTenantMapping]:
        """Get all active Entra tenant mappings."""
        result = await self.session.execute(
            select(EntraTenantMapping).where(EntraTenantMapping.is_active == True)  # noqa: E712
        )
        return list(result.scalars().all())
