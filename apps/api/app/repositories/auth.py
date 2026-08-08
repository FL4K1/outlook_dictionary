"""Repositories for DeviceSession and RefreshTokenFamily entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import select, update

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.base import BaseRepository
from mip_models.auth import DeviceSession, RefreshTokenFamily, Session


class DeviceSessionRepository(BaseRepository[DeviceSession]):
    """Repository for DeviceSession entities.

    All methods are persistence-focused. Tenant isolation is enforced
    at the service layer by passing the correct tenant context.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(DeviceSession, session)

    async def get_by_refresh_token_hash(
        self,
        hash_val: str,
        for_update: bool = False,
    ) -> DeviceSession | None:
        """Find a device session by its current refresh token hash.

        Args:
            hash_val: SHA-256 hash of the refresh token.
            for_update: If True, acquires a row-level exclusive lock
                       (SELECT FOR UPDATE). Use during refresh operations
                       to prevent concurrent refresh race conditions.

        Returns:
            The matching DeviceSession, or None if not found.
        """
        stmt = select(DeviceSession).where(DeviceSession.current_refresh_token_hash == hash_val)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_active_sessions_for_user(
        self,
        user_id: uuid.UUID,
    ) -> Sequence[DeviceSession]:
        """Get all non-revoked, non-expired sessions for a user.

        Note: Expiry filtering is performed at the service layer because
        it requires a datetime comparison against the current time.

        Args:
            user_id: The user's UUID.

        Returns:
            Sequence of active DeviceSession records.
        """
        result = await self.session.execute(
            select(DeviceSession).where(
                DeviceSession.user_id == user_id,
                DeviceSession.revoked_at.is_(None),
            )
        )
        return result.scalars().all()

    async def get_active_sessions_for_tenant(
        self,
        tenant_id: uuid.UUID,
    ) -> Sequence[DeviceSession]:
        """Get all non-revoked sessions for a tenant.

        Admin/future use. Does not filter by expiry; that is enforced
        at the service layer.

        Args:
            tenant_id: The tenant's UUID.

        Returns:
            Sequence of active DeviceSession records for the tenant.
        """
        result = await self.session.execute(
            select(DeviceSession).where(
                DeviceSession.tenant_id == tenant_id,
                DeviceSession.revoked_at.is_(None),
            )
        )
        return result.scalars().all()

    async def revoke(
        self,
        session_id: uuid.UUID,
        revoked_at: datetime,
    ) -> bool:
        """Soft-revoke a specific device session.

        Sets revoked_at to the provided timestamp. Only revokes sessions
        that are not already revoked.

        Args:
            session_id: The DeviceSession UUID.
            revoked_at: The timestamp to set as revoked_at.

        Returns:
            True if a row was updated, False if the session was already revoked.
        """
        stmt = (
            update(DeviceSession)
            .where(DeviceSession.id == session_id, DeviceSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return cast("int", result.rowcount) > 0  # type: ignore[attr-defined]

    async def revoke_all_for_user(
        self,
        user_id: uuid.UUID,
        revoked_at: datetime,
    ) -> int:
        """Revoke all active sessions for a user.

        Sets revoked_at on all non-revoked sessions for the user.
        Used for security events like refresh token reuse detection.

        Args:
            user_id: The user's UUID.
            revoked_at: The timestamp to set as revoked_at.

        Returns:
            The number of sessions revoked.
        """
        stmt = (
            update(DeviceSession)
            .where(DeviceSession.user_id == user_id, DeviceSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return cast("int", result.rowcount)  # type: ignore[attr-defined]


class RefreshTokenFamilyRepository(BaseRepository[RefreshTokenFamily]):
    """Repository for RefreshTokenFamily entities.

    All methods are persistence-focused. Locking primitives are provided
    for the refresh flow to prevent concurrent refresh race conditions.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(RefreshTokenFamily, session)

    async def get_by_token_hash(
        self,
        hash_val: str,
        for_update: bool = False,
    ) -> RefreshTokenFamily | None:
        """Find a refresh token family epoch by its token hash.

        Args:
            hash_val: SHA-256 hash of the refresh token.
            for_update: If True, acquires a row-level exclusive lock
                       (SELECT FOR UPDATE). Use during refresh operations
                       to prevent concurrent refresh race conditions.

        Returns:
            The matching RefreshTokenFamily, or None if not found.
        """
        stmt = select(RefreshTokenFamily).where(RefreshTokenFamily.token_hash == hash_val)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_active_for_session(
        self,
        device_session_id: uuid.UUID,
        for_update: bool = False,
    ) -> RefreshTokenFamily | None:
        """Get the current unconsumed epoch for a device session.

        Args:
            device_session_id: The parent DeviceSession UUID.
            for_update: If True, acquires a row-level exclusive lock.

        Returns:
            The active (unconsumed) RefreshTokenFamily, or None if not found.
        """
        stmt = select(RefreshTokenFamily).where(
            RefreshTokenFamily.device_session_id == device_session_id,
            RefreshTokenFamily.consumed_at.is_(None),
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def create_epoch(
        self,
        device_session_id: uuid.UUID,
        token_hash: str,
        consumed_at: datetime | None = None,
    ) -> RefreshTokenFamily:
        """Create a new refresh-token epoch.

        Args:
            device_session_id: The parent DeviceSession UUID.
            token_hash: SHA-256 hash of the refresh token.
            consumed_at: Optional timestamp for consumed epochs.
                        None for active epochs.

        Returns:
            The newly created RefreshTokenFamily.
        """
        return await self.create(
            device_session_id=device_session_id,
            token_hash=token_hash,
            consumed_at=consumed_at,
        )

    async def mark_consumed(
        self,
        family_id: uuid.UUID,
        consumed_at: datetime,
    ) -> RefreshTokenFamily | None:
        """Mark a family epoch as consumed.

        Sets consumed_at to the provided timestamp. Only marks epochs
        that are not already consumed.

        Args:
            family_id: The RefreshTokenFamily UUID.
            consumed_at: The timestamp to set as consumed_at.

        Returns:
            The updated RefreshTokenFamily, or None if already consumed.
        """
        stmt = (
            update(RefreshTokenFamily)
            .where(
                RefreshTokenFamily.id == family_id,
                RefreshTokenFamily.consumed_at.is_(None),
            )
            .values(consumed_at=consumed_at)
            .returning(RefreshTokenFamily)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalars().first()


class SessionRepository(BaseRepository[Session]):
    """Repository for Session entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Session, session)

    async def get_by_refresh_token_hash(self, hash_val: str) -> Session | None:
        """Find a session by its refresh token hash."""
        result = await self.session.execute(
            select(Session).where(Session.refresh_token_hash == hash_val)
        )
        return result.scalars().first()

    async def get_active_sessions_for_user(self, user_id: uuid.UUID) -> Sequence[Session]:
        """Get all non-revoked sessions for a user."""
        result = await self.session.execute(
            select(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
        )
        return result.scalars().all()

    async def revoke_all_for_user(self, user_id: uuid.UUID, revoked_at: datetime) -> int:
        """Revoke all active sessions for a user (e.g., on reuse detection).

        Returns the number of sessions revoked.
        """
        stmt = (
            update(Session)
            .where(Session.user_id == user_id, Session.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return cast("int", result.rowcount)  # type: ignore[attr-defined]

    async def revoke(self, session_id: uuid.UUID, revoked_at: datetime) -> bool:
        """Revoke a specific session."""
        stmt = (
            update(Session)
            .where(Session.id == session_id, Session.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return cast("int", result.rowcount) > 0  # type: ignore[attr-defined]
