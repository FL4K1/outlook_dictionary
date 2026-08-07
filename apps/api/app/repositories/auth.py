"""Repository for Session entities."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import select, update

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.base import BaseRepository
from mip_models.auth import Session


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
