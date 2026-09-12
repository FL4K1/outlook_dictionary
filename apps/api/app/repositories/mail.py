"""Repositories for Mail Synchronization database foundation entities.

Provides persistence operations and fenced atomic CAS operations for:
- MailAccount
- MailFolder
- MailSyncState
- MailMessage
- MailMessageFolder (pivot)
- MailMessageParticipant
- OutboxEvent
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, or_, select, update

from app.repositories.base import BaseRepository
from mip_models.mail import (
    MailAccount,
    MailFolder,
    MailMessage,
    MailMessageFolder,
    MailMessageParticipant,
    MailSyncState,
    OutboxEvent,
    OutboxEventStatus,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    from sqlalchemy.ext.asyncio import AsyncSession


class MailAccountRepository(BaseRepository[MailAccount]):
    """Repository for MailAccount operations including credential refresh lease CAS."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(MailAccount, session)

    async def get_by_tenant(self, tenant_id: uuid.UUID) -> Sequence[MailAccount]:
        """Get all mail accounts for a tenant."""
        result = await self.session.execute(
            select(MailAccount).where(MailAccount.tenant_id == tenant_id)
        )
        return result.scalars().all()

    async def acquire_refresh_lease(
        self,
        mail_account_id: uuid.UUID,
        worker_id: uuid.UUID,
        lease_duration: timedelta,
    ) -> tuple[int, int] | None:
        """Atomically acquire the credential refresh lease for a mail account.

        Returns (refresh_lease_version, credential_generation) if acquired,
        or None if active lease is owned by another worker.
        """
        now = datetime.now(UTC)
        stmt = (
            update(MailAccount)
            .where(
                MailAccount.id == mail_account_id,
                or_(
                    MailAccount.refresh_locked_by.is_(None),
                    MailAccount.refresh_locked_until < now,
                ),
            )
            .values(
                refresh_locked_by=worker_id,
                refresh_locked_until=now + lease_duration,
                refresh_lease_version=MailAccount.refresh_lease_version + 1,
            )
            .returning(MailAccount.refresh_lease_version, MailAccount.credential_generation)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        row = result.first()
        if row is None:
            return None
        return (int(row[0]), int(row[1]))

    async def finalize_refresh_lease(
        self,
        mail_account_id: uuid.UUID,
        worker_id: uuid.UUID,
        lease_version: int,
    ) -> bool:
        """Atomically finalize a refresh operation using CAS fencing.

        Increments credential_generation and clears the refresh lock ONLY if
        worker_id and lease_version match.
        """
        stmt = (
            update(MailAccount)
            .where(
                MailAccount.id == mail_account_id,
                MailAccount.refresh_locked_by == worker_id,
                MailAccount.refresh_lease_version == lease_version,
            )
            .values(
                credential_generation=MailAccount.credential_generation + 1,
                refresh_locked_by=None,
                refresh_locked_until=None,
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount == 1  # type: ignore[no-any-return]

    async def release_refresh_lease(
        self,
        mail_account_id: uuid.UUID,
        worker_id: uuid.UUID,
        lease_version: int,
    ) -> bool:
        """Clear refresh lock using CAS fencing without incrementing credential_generation."""
        stmt = (
            update(MailAccount)
            .where(
                MailAccount.id == mail_account_id,
                MailAccount.refresh_locked_by == worker_id,
                MailAccount.refresh_lease_version == lease_version,
            )
            .values(
                refresh_locked_by=None,
                refresh_locked_until=None,
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount == 1  # type: ignore[no-any-return]


class MailFolderRepository(BaseRepository[MailFolder]):
    """Repository for MailFolder entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(MailFolder, session)

    async def get_by_provider_id(
        self,
        mail_account_id: uuid.UUID,
        provider_folder_id: str,
    ) -> MailFolder | None:
        """Get a folder by mail account and provider folder ID."""
        result = await self.session.execute(
            select(MailFolder).where(
                MailFolder.mail_account_id == mail_account_id,
                MailFolder.provider_folder_id == provider_folder_id,
            )
        )
        return result.scalars().first()

    async def get_by_account_id(self, mail_account_id: uuid.UUID) -> Sequence[MailFolder]:
        """Get all folders for a mail account."""
        result = await self.session.execute(
            select(MailFolder).where(MailFolder.mail_account_id == mail_account_id)
        )
        return result.scalars().all()


class MailSyncStateRepository(BaseRepository[MailSyncState]):
    """Repository for MailSyncState operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(MailSyncState, session)

    async def get_by_folder_id(self, mail_folder_id: uuid.UUID) -> MailSyncState | None:
        """Get sync state by folder ID."""
        result = await self.session.execute(
            select(MailSyncState).where(MailSyncState.mail_folder_id == mail_folder_id)
        )
        return result.scalars().first()

    async def acquire_sync_lease(
        self,
        sync_state_id: uuid.UUID,
        worker_id: uuid.UUID,
        lease_duration: timedelta,
    ) -> int | None:
        """Atomically acquire sync lease for a sync state row."""
        now = datetime.now(UTC)
        stmt = (
            update(MailSyncState)
            .where(
                MailSyncState.id == sync_state_id,
                or_(
                    MailSyncState.locked_by.is_(None),
                    MailSyncState.locked_until < now,
                ),
            )
            .values(
                locked_by=worker_id,
                locked_until=now + lease_duration,
                lease_version=MailSyncState.lease_version + 1,
            )
            .returning(MailSyncState.lease_version)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        row = result.first()
        if row is None:
            return None
        return int(row[0])

    async def renew_sync_lease(
        self,
        sync_state_id: uuid.UUID,
        worker_id: uuid.UUID,
        lease_version: int,
        lease_duration: timedelta,
    ) -> bool:
        """Renew sync lease for an active worker using CAS fencing."""
        now = datetime.now(UTC)
        stmt = (
            update(MailSyncState)
            .where(
                MailSyncState.id == sync_state_id,
                MailSyncState.locked_by == worker_id,
                MailSyncState.lease_version == lease_version,
            )
            .values(
                locked_until=now + lease_duration,
                updated_at=now,
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount == 1  # type: ignore[no-any-return]

    async def update_sync_state_cas(
        self,
        sync_state_id: uuid.UUID,
        worker_id: uuid.UUID,
        lease_version: int,
        state: str,
        sync_token: str | None = None,
        resync_generation: int | None = None,
        clear_lock: bool = False,
        clear_sync_token: bool = False,
    ) -> bool:
        """Update sync token and state using CAS fencing on worker_id and lease_version."""
        now = datetime.now(UTC)
        values: dict[str, object] = {
            "state": state,
            "updated_at": now,
        }
        if sync_token is not None:
            values["sync_token"] = sync_token
        elif clear_sync_token:
            values["sync_token"] = None
        if resync_generation is not None:
            values["resync_generation"] = resync_generation
        if clear_lock:
            values["locked_by"] = None
            values["locked_until"] = None

        stmt = (
            update(MailSyncState)
            .where(
                MailSyncState.id == sync_state_id,
                MailSyncState.locked_by == worker_id,
                MailSyncState.lease_version == lease_version,
            )
            .values(**values)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount == 1  # type: ignore[no-any-return]

    async def release_sync_lease(
        self,
        sync_state_id: uuid.UUID,
        worker_id: uuid.UUID,
        lease_version: int,
    ) -> bool:
        """Clear sync lease lock using CAS fencing."""
        now = datetime.now(UTC)
        stmt = (
            update(MailSyncState)
            .where(
                MailSyncState.id == sync_state_id,
                MailSyncState.locked_by == worker_id,
                MailSyncState.lease_version == lease_version,
            )
            .values(
                locked_by=None,
                locked_until=None,
                updated_at=now,
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount == 1  # type: ignore[no-any-return]


class MailMessageRepository(BaseRepository[MailMessage]):
    """Repository for MailMessage entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(MailMessage, session)

    async def get_by_provider_id(
        self,
        mail_account_id: uuid.UUID,
        provider_message_id: str,
    ) -> MailMessage | None:
        """Get a message by mail account and provider message ID."""
        result = await self.session.execute(
            select(MailMessage).where(
                MailMessage.mail_account_id == mail_account_id,
                MailMessage.provider_message_id == provider_message_id,
            )
        )
        return result.scalars().first()

    async def get_by_provider_id_for_update(
        self,
        mail_account_id: uuid.UUID,
        provider_message_id: str,
    ) -> MailMessage | None:
        """Get a message by mail account and provider message ID with SELECT FOR UPDATE."""
        result = await self.session.execute(
            select(MailMessage)
            .where(
                MailMessage.mail_account_id == mail_account_id,
                MailMessage.provider_message_id == provider_message_id,
            )
            .with_for_update()
        )
        return result.scalars().first()


class MailMessageFolderRepository:
    """Repository for MailMessageFolder pivot relationships."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        mail_message_id: uuid.UUID,
        mail_folder_id: uuid.UUID,
        tenant_id: uuid.UUID,
        mail_account_id: uuid.UUID,
        resync_generation: int,
    ) -> MailMessageFolder:
        """Add a message to a folder pivot."""
        instance = MailMessageFolder(
            mail_message_id=mail_message_id,
            mail_folder_id=mail_folder_id,
            tenant_id=tenant_id,
            mail_account_id=mail_account_id,
            resync_generation=resync_generation,
        )
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def get_membership(
        self,
        mail_message_id: uuid.UUID,
        mail_folder_id: uuid.UUID,
    ) -> MailMessageFolder | None:
        """Get pivot membership for message and folder."""
        result = await self.session.execute(
            select(MailMessageFolder).where(
                MailMessageFolder.mail_message_id == mail_message_id,
                MailMessageFolder.mail_folder_id == mail_folder_id,
            )
        )
        return result.scalars().first()

    async def get_memberships_for_message(
        self,
        mail_message_id: uuid.UUID,
    ) -> Sequence[MailMessageFolder]:
        """Get all folder memberships for a message."""
        result = await self.session.execute(
            select(MailMessageFolder).where(MailMessageFolder.mail_message_id == mail_message_id)
        )
        return result.scalars().all()

    async def get_stale_memberships(
        self,
        mail_folder_id: uuid.UUID,
        resync_generation: int,
    ) -> Sequence[MailMessageFolder]:
        """Get folder memberships whose resync_generation is older than current."""
        result = await self.session.execute(
            select(MailMessageFolder).where(
                MailMessageFolder.mail_folder_id == mail_folder_id,
                MailMessageFolder.resync_generation < resync_generation,
            )
        )
        return result.scalars().all()

    async def remove_membership(
        self,
        mail_message_id: uuid.UUID,
        mail_folder_id: uuid.UUID,
    ) -> bool:
        """Remove a message from a folder."""
        stmt = delete(MailMessageFolder).where(
            MailMessageFolder.mail_message_id == mail_message_id,
            MailMessageFolder.mail_folder_id == mail_folder_id,
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0  # type: ignore[no-any-return]


class MailMessageParticipantRepository(BaseRepository[MailMessageParticipant]):
    """Repository for MailMessageParticipant entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(MailMessageParticipant, session)

    async def get_by_message_id(
        self,
        mail_message_id: uuid.UUID,
    ) -> Sequence[MailMessageParticipant]:
        """Get all participants for a message."""
        result = await self.session.execute(
            select(MailMessageParticipant).where(
                MailMessageParticipant.mail_message_id == mail_message_id
            )
        )
        return result.scalars().all()

    async def delete_by_role(
        self,
        mail_message_id: uuid.UUID,
        role: str,
    ) -> int:
        """Delete all participants for a message by role."""
        stmt = delete(MailMessageParticipant).where(
            MailMessageParticipant.mail_message_id == mail_message_id,
            MailMessageParticipant.role == role,
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount  # type: ignore[no-any-return]


class OutboxEventRepository(BaseRepository[OutboxEvent]):
    """Repository for OutboxEvent entities with CAS worker fencing."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(OutboxEvent, session)

    async def get_by_idempotency_key(self, idempotency_key: str) -> OutboxEvent | None:
        """Get outbox event by idempotency key."""
        result = await self.session.execute(
            select(OutboxEvent).where(OutboxEvent.idempotency_key == idempotency_key)
        )
        return result.scalars().first()

    async def create_event(
        self,
        tenant_id: uuid.UUID,
        aggregate_id: uuid.UUID,
        aggregate_version: int,
        event_type: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> OutboxEvent:
        """Create a transactional outbox event."""
        now = datetime.now(UTC)
        event = OutboxEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
            status=OutboxEventStatus.PENDING,
            created_at=now,
            next_attempt_at=now,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def acquire_outbox_lease(
        self,
        event_id: uuid.UUID,
        worker_id: uuid.UUID,
        lease_duration: timedelta,
    ) -> int | None:
        """Atomically acquire outbox worker lease using CAS.

        Guarantees eligibility: status IN ('PENDING', 'IN_FLIGHT'), next_attempt_at <= NOW(),
        and lock unassigned or expired.
        Returns the new lease_version if acquired, else None.
        """
        now = datetime.now(UTC)
        stmt = (
            update(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                OutboxEvent.status.in_([OutboxEventStatus.PENDING, OutboxEventStatus.IN_FLIGHT]),
                OutboxEvent.next_attempt_at <= now,
                or_(
                    OutboxEvent.locked_by.is_(None),
                    OutboxEvent.locked_until < now,
                ),
            )
            .values(
                locked_by=worker_id,
                locked_until=now + lease_duration,
                lease_version=OutboxEvent.lease_version + 1,
                status=OutboxEventStatus.IN_FLIGHT,
            )
            .returning(OutboxEvent.lease_version)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        row = result.first()
        if row is None:
            return None
        return int(row[0])

    async def update_status_cas(
        self,
        event_id: uuid.UUID,
        worker_id: uuid.UUID,
        lease_version: int,
        new_status: str,
        last_error: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> bool:
        """Update outbox event status using strict CAS fencing.

        Validates event_id + worker_id + lease_version.
        """
        now = datetime.now(UTC)
        values: dict[str, object] = {
            "status": new_status,
            "locked_by": None,
            "locked_until": None,
        }
        if last_error is not None:
            values["last_error"] = last_error
            values["attempt_count"] = OutboxEvent.attempt_count + 1
        if next_attempt_at is not None:
            values["next_attempt_at"] = next_attempt_at
        if new_status == OutboxEventStatus.DONE:
            values["completed_at"] = now

        stmt = (
            update(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                OutboxEvent.locked_by == worker_id,
                OutboxEvent.lease_version == lease_version,
            )
            .values(**values)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount == 1  # type: ignore[no-any-return]
