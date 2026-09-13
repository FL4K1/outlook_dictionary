"""Mail synchronization orchestrator (PR-2.3).

Coordinates MailSyncState lease acquisition, Graph provider delta page fetching,
canonical PostgreSQL message mutations, scoped folder memberships, participant
reconciliation, version incrementing, transactional outbox emission, and
410 resynchronization recovery.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.repositories.mail import (
    MailFolderRepository,
    MailMessageFolderRepository,
    MailMessageParticipantRepository,
    MailMessageRepository,
    MailSyncStateRepository,
    OutboxEventRepository,
)
from mip_models.mail import (
    MailMessage,
    MailMessageParticipant,
    MailSyncState,
    MailSyncStateValue,
    ParticipantRole,
)
from mip_providers import (
    AuthExpiredError,
    DeltaCursorExpiredError,
    MicrosoftGraphMailAdapter,
    ProviderMessage,
    ProviderNotFoundError,
    ProviderPermissionError,
    ProviderRateLimitedError,
    UnsetType,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncResult:
    """Summary of a folder sync operation."""

    mail_folder_id: uuid.UUID
    state: str
    messages_processed: int
    messages_mutated: int
    removals_processed: int
    resync_triggered: bool = False
    error: str | None = None


class SyncOrchestrator:
    """Engine executing deterministic, fenced, transactional mail synchronization."""

    def __init__(
        self,
        session: AsyncSession,
        adapter_factory: Any = None,
        provider_auth_service: Any = None,
    ) -> None:
        self.session = session
        self.adapter_factory = adapter_factory
        self.provider_auth_service = provider_auth_service
        self.sync_state_repo = MailSyncStateRepository(session)
        self.folder_repo = MailFolderRepository(session)
        self.message_repo = MailMessageRepository(session)
        self.pivot_repo = MailMessageFolderRepository(session)
        self.participant_repo = MailMessageParticipantRepository(session)
        self.outbox_repo = OutboxEventRepository(session)

    async def sync_folder(
        self,
        mail_folder_id: uuid.UUID,
        worker_id: uuid.UUID,
        access_token: str,
        adapter: Any = None,
        lease_duration: timedelta = timedelta(minutes=5),
        provider_auth_service: Any = None,
    ) -> SyncResult:
        """Synchronize a single folder using lease fencing and page-transaction boundaries."""
        if adapter is None:
            if self.adapter_factory:
                adapter = self.adapter_factory(access_token)
            else:
                adapter = MicrosoftGraphMailAdapter(access_token=access_token)

        # 1. Fetch Folder and SyncState
        folder = await self.folder_repo.get(mail_folder_id)
        if folder is None:
            return SyncResult(
                mail_folder_id=mail_folder_id,
                state=MailSyncStateValue.ERROR,
                messages_processed=0,
                messages_mutated=0,
                removals_processed=0,
                error=f"MailFolder {mail_folder_id} not found",
            )

        # Store scalar values locally to avoid lazy-loading MissingGreenlet errors on commit
        tenant_id: uuid.UUID = folder.tenant_id
        mail_account_id: uuid.UUID = folder.mail_account_id
        provider_folder_id: str = folder.provider_folder_id

        sync_state = await self.sync_state_repo.get_by_folder_id(mail_folder_id)
        if sync_state is None:
            sync_state = MailSyncState(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                mail_folder_id=mail_folder_id,
                state=MailSyncStateValue.PENDING_INITIAL_SYNC,
                sync_token=None,
                resync_generation=1,
                lease_version=1,
                updated_at=datetime.now(UTC),
            )
            self.session.add(sync_state)
            await self.session.flush()

        sync_state_id: uuid.UUID = sync_state.id
        sync_token: str | None = sync_state.sync_token
        resync_generation: int = sync_state.resync_generation
        current_state: str = sync_state.state

        # 2. Acquire sync lease
        lease_version = await self.sync_state_repo.acquire_sync_lease(
            sync_state_id, worker_id, lease_duration
        )
        if lease_version is None:
            logger.warning("Could not acquire sync lease for folder %s", mail_folder_id)
            return SyncResult(
                mail_folder_id=mail_folder_id,
                state=current_state,
                messages_processed=0,
                messages_mutated=0,
                removals_processed=0,
                error="Lease acquisition failed",
            )
        await self.session.commit()

        total_processed = 0
        total_mutated = 0
        total_removals = 0
        resync_triggered = False
        refresh_attempted = False

        # 3. Main Sync Loop
        try:
            while True:
                # 3a. Renew/verify lease before outbound HTTP call
                renewed = await self.sync_state_repo.renew_sync_lease(
                    sync_state_id, worker_id, lease_version, lease_duration
                )
                await self.session.commit()
                if not renewed:
                    logger.warning("Worker %s lost sync lease version %s", worker_id, lease_version)
                    return SyncResult(
                        mail_folder_id=mail_folder_id,
                        state=current_state,
                        messages_processed=total_processed,
                        messages_mutated=total_mutated,
                        removals_processed=total_removals,
                        error="Lease lost",
                    )

                # 3b. External HTTP call (OUTSIDE DB Transaction)
                try:
                    page = await adapter.get_message_delta(
                        provider_folder_id,
                        opaque_continuation=sync_token,
                    )
                except AuthExpiredError as err:
                    auth_svc = provider_auth_service or self.provider_auth_service
                    if not refresh_attempted and auth_svc is not None:
                        refresh_attempted = True
                        try:
                            refreshed_creds = await auth_svc.refresh_mail_account_credentials(
                                db=self.session,
                                mail_account_id=mail_account_id,
                                worker_id=worker_id,
                            )
                            new_token = (
                                refreshed_creds.access_token
                                if hasattr(refreshed_creds, "access_token")
                                else str(refreshed_creds)
                            )
                            if self.adapter_factory:
                                adapter = self.adapter_factory(new_token)
                            elif hasattr(adapter, "access_token"):
                                adapter.access_token = new_token
                            else:
                                adapter = MicrosoftGraphMailAdapter(access_token=new_token)

                            # Retry the delta fetch ONCE with refreshed token
                            page = await adapter.get_message_delta(
                                provider_folder_id,
                                opaque_continuation=sync_token,
                            )
                        except Exception as refresh_err:
                            await self.sync_state_repo.update_sync_state_cas(
                                sync_state_id,
                                worker_id,
                                lease_version,
                                state=MailSyncStateValue.AUTH_REQUIRED,
                                clear_lock=True,
                            )
                            return SyncResult(
                                mail_folder_id=mail_folder_id,
                                state=MailSyncStateValue.AUTH_REQUIRED,
                                messages_processed=total_processed,
                                messages_mutated=total_mutated,
                                removals_processed=total_removals,
                                error=str(refresh_err),
                            )
                    else:
                        await self.sync_state_repo.update_sync_state_cas(
                            sync_state_id,
                            worker_id,
                            lease_version,
                            state=MailSyncStateValue.AUTH_REQUIRED,
                            clear_lock=True,
                        )
                        return SyncResult(
                            mail_folder_id=mail_folder_id,
                            state=MailSyncStateValue.AUTH_REQUIRED,
                            messages_processed=total_processed,
                            messages_mutated=total_mutated,
                            removals_processed=total_removals,
                            error=str(err),
                        )

                except (ProviderPermissionError, ProviderNotFoundError) as err:
                    await self.sync_state_repo.update_sync_state_cas(
                        sync_state_id,
                        worker_id,
                        lease_version,
                        state=MailSyncStateValue.ERROR,
                        clear_lock=True,
                    )
                    return SyncResult(
                        mail_folder_id=mail_folder_id,
                        state=MailSyncStateValue.ERROR,
                        messages_processed=total_processed,
                        messages_mutated=total_mutated,
                        removals_processed=total_removals,
                        error=str(err),
                    )
                except ProviderRateLimitedError as err:
                    return SyncResult(
                        mail_folder_id=mail_folder_id,
                        state=current_state,
                        messages_processed=total_processed,
                        messages_mutated=total_mutated,
                        removals_processed=total_removals,
                        error=str(err),
                    )
                except DeltaCursorExpiredError:
                    # 410 Resync Workflow
                    resync_triggered = True
                    resync_generation += 1
                    sync_token = None
                    current_state = MailSyncStateValue.SYNCING
                    await self.sync_state_repo.update_sync_state_cas(
                        sync_state_id,
                        worker_id,
                        lease_version,
                        state=MailSyncStateValue.SYNCING,
                        sync_token=None,
                        clear_sync_token=True,
                        resync_generation=resync_generation,
                    )
                    # Restart delta sync loop with new resync_generation
                    continue

                # 3c. Process Page inside DB Transaction
                async with (
                    self.session.begin_nested()
                    if self.session.in_transaction()
                    else self.session.begin()
                ):
                    # Validate lease before applying mutations
                    check = await self.sync_state_repo.renew_sync_lease(
                        sync_state_id, worker_id, lease_version, lease_duration
                    )
                    if not check:
                        raise RuntimeError("Lease lost during page processing")

                    page_mutated = 0

                    # Process ProviderMessages
                    for msg in page.messages:
                        mutated = await self._process_provider_message(
                            tenant_id=tenant_id,
                            mail_account_id=mail_account_id,
                            folder_id=mail_folder_id,
                            msg=msg,
                            resync_generation=resync_generation,
                        )
                        if mutated:
                            page_mutated += 1

                    # Process ProviderRemovals
                    for removal in page.removals:
                        rem_mutated = await self._process_provider_removal(
                            tenant_id=tenant_id,
                            mail_account_id=mail_account_id,
                            folder_id=mail_folder_id,
                            removal_id=removal.provider_message_id,
                        )
                        if rem_mutated:
                            page_mutated += 1
                            total_removals += 1

                    total_processed += len(page.messages)
                    total_mutated += page_mutated

                    # If page indicates completion/checkpoint and we were in a resync:
                    if resync_triggered and (page.is_delta_checkpoint or not page.has_more):
                        await self._reconcile_stale_memberships(
                            tenant_id=tenant_id,
                            mail_account_id=mail_account_id,
                            folder_id=mail_folder_id,
                            current_resync_generation=resync_generation,
                        )

                    # Determine next sync state
                    next_state = (
                        MailSyncStateValue.DELTA_TRACKING
                        if (page.is_delta_checkpoint or not page.has_more)
                        else MailSyncStateValue.SYNCING
                    )

                    # Update local state & token
                    sync_token = page.next_continuation
                    current_state = next_state

                    cas_ok = await self.sync_state_repo.update_sync_state_cas(
                        sync_state_id,
                        worker_id,
                        lease_version,
                        state=next_state,
                        sync_token=sync_token,
                    )
                    if not cas_ok:
                        raise RuntimeError("Lease CAS update failed at page commit")

                # If no more pages, break out of sync loop
                if not page.has_more or page.is_delta_checkpoint:
                    break

            # 4. Release sync lease gracefully
            await self.sync_state_repo.release_sync_lease(sync_state_id, worker_id, lease_version)
            await self.session.commit()

            return SyncResult(
                mail_folder_id=mail_folder_id,
                state=current_state,
                messages_processed=total_processed,
                messages_mutated=total_mutated,
                removals_processed=total_removals,
                resync_triggered=resync_triggered,
            )

        except Exception as exc:
            logger.exception("Synchronization failed for folder %s: %s", mail_folder_id, exc)

            return SyncResult(
                mail_folder_id=mail_folder_id,
                state=current_state,
                messages_processed=total_processed,
                messages_mutated=total_mutated,
                removals_processed=total_removals,
                resync_triggered=resync_triggered,
                error=str(exc),
            )

    async def _process_provider_message(
        self,
        tenant_id: uuid.UUID,
        mail_account_id: uuid.UUID,
        folder_id: uuid.UUID,
        msg: ProviderMessage,
        resync_generation: int,
    ) -> bool:
        """Perform canonical message mutation and participant reconciliation."""
        # 1. Fetch or create MailMessage

        existing = await self.message_repo.get_by_provider_id_for_update(
            mail_account_id, msg.provider_message_id
        )

        is_new = False
        if existing is None:
            message_id = uuid.uuid4()
            stmt = (
                pg_insert(MailMessage)
                .values(
                    id=message_id,
                    tenant_id=tenant_id,
                    mail_account_id=mail_account_id,
                    provider_message_id=msg.provider_message_id,
                    version=1,
                    is_deleted=False,
                    subject=None if isinstance(msg.subject, UnsetType) else msg.subject,
                    body=None if isinstance(msg.body, UnsetType) else msg.body,
                    body_preview=None
                    if isinstance(msg.body_preview, UnsetType)
                    else msg.body_preview,
                    received_date_time=None
                    if isinstance(msg.received_date_time, UnsetType)
                    else msg.received_date_time,
                    has_attachments=False
                    if isinstance(msg.has_attachments, UnsetType) or msg.has_attachments is None
                    else msg.has_attachments,
                    is_read=False
                    if isinstance(msg.is_read, UnsetType) or msg.is_read is None
                    else msg.is_read,
                )
                .on_conflict_do_nothing(index_elements=["mail_account_id", "provider_message_id"])
            )
            res = await self.session.execute(stmt)
            await self.session.flush()

            rowcount = getattr(res, "rowcount", 0)
            if rowcount == 1:
                is_new = True
                message = await self.message_repo.get(message_id)
                assert message is not None
            else:
                message = await self.message_repo.get_by_provider_id_for_update(
                    mail_account_id, msg.provider_message_id
                )
                assert message is not None
        else:
            message = existing

        aggregate_changed = False

        # 2. Scalar Field Mutations
        if not is_new:
            if not isinstance(msg.subject, UnsetType) and message.subject != msg.subject:
                message.subject = msg.subject
                aggregate_changed = True

            if not isinstance(msg.body, UnsetType) and message.body != msg.body:
                message.body = msg.body
                aggregate_changed = True

            if (
                not isinstance(msg.body_preview, UnsetType)
                and message.body_preview != msg.body_preview
            ):
                message.body_preview = msg.body_preview
                aggregate_changed = True

            if (
                not isinstance(msg.received_date_time, UnsetType)
                and message.received_date_time != msg.received_date_time
            ):
                message.received_date_time = msg.received_date_time
                aggregate_changed = True

            if (
                not isinstance(msg.has_attachments, UnsetType)
                and msg.has_attachments is not None
                and message.has_attachments != msg.has_attachments
            ):
                message.has_attachments = msg.has_attachments
                aggregate_changed = True

            if (
                not isinstance(msg.is_read, UnsetType)
                and msg.is_read is not None
                and message.is_read != msg.is_read
            ):
                message.is_read = msg.is_read
                aggregate_changed = True

        # 3. Sender Reconciliation
        if not isinstance(msg.sender, UnsetType):
            if msg.sender is None:
                if message.sender is not None:
                    message.sender = None
                    aggregate_changed = True
            else:
                sender_dict = {
                    "name": msg.sender.name,
                    "email": msg.sender.email.strip().lower(),
                }
                if message.sender != sender_dict:
                    message.sender = sender_dict
                    aggregate_changed = True

        # 4. Participant Reconciliation (TO, CC, BCC)
        for role_name, recipient_field in [
            (ParticipantRole.TO, msg.recipients_to),
            (ParticipantRole.CC, msg.recipients_cc),
            (ParticipantRole.BCC, msg.recipients_bcc),
        ]:
            if isinstance(recipient_field, UnsetType):
                continue

            if recipient_field is None:
                deleted_cnt = await self.participant_repo.delete_by_role(message.id, role_name)
                if deleted_cnt > 0:
                    aggregate_changed = True
            else:
                existing_parts = await self.session.execute(
                    select(MailMessageParticipant).where(
                        MailMessageParticipant.mail_message_id == message.id,
                        MailMessageParticipant.role == role_name,
                    )
                )
                existing_list = existing_parts.scalars().all()
                existing_tuples = sorted(
                    [(p.name or "", p.email.strip().lower()) for p in existing_list]
                )

                new_tuples = sorted(
                    [(p.name or "", p.email.strip().lower()) for p in recipient_field]
                )

                if existing_tuples != new_tuples or is_new:
                    await self.participant_repo.delete_by_role(message.id, role_name)
                    for p in recipient_field:
                        part = MailMessageParticipant(
                            id=uuid.uuid4(),
                            mail_message_id=message.id,
                            name=p.name or None,
                            email=p.email.strip().lower(),
                            role=role_name,
                        )
                        self.session.add(part)
                    if not is_new:
                        aggregate_changed = True

        # 5. Scoped Folder Membership Pivot
        pivot = await self.pivot_repo.get_membership(message.id, folder_id)
        if pivot is None:
            await self.pivot_repo.create(
                mail_message_id=message.id,
                mail_folder_id=folder_id,
                tenant_id=tenant_id,
                mail_account_id=mail_account_id,
                resync_generation=resync_generation,
            )
            if not is_new:
                aggregate_changed = True
        else:
            if pivot.resync_generation != resync_generation:
                pivot.resync_generation = resync_generation

        # 6. Version Increment & Transactional Outbox Event Emission
        if is_new:
            outbox_key = f"{message.id}::1::MAIL_MESSAGE_MUTATED"
            await self.outbox_repo.create_event(
                tenant_id=tenant_id,
                aggregate_id=message.id,
                aggregate_version=1,
                event_type="MAIL_MESSAGE_MUTATED",
                idempotency_key=outbox_key,
                payload={
                    "mail_message_id": str(message.id),
                    "mail_account_id": str(mail_account_id),
                    "tenant_id": str(tenant_id),
                    "provider_message_id": message.provider_message_id,
                    "version": 1,
                    "action": "created",
                },
            )
            return True
        elif aggregate_changed:
            message.version += 1
            outbox_key = f"{message.id}::{message.version}::MAIL_MESSAGE_MUTATED"
            await self.outbox_repo.create_event(
                tenant_id=tenant_id,
                aggregate_id=message.id,
                aggregate_version=message.version,
                event_type="MAIL_MESSAGE_MUTATED",
                idempotency_key=outbox_key,
                payload={
                    "mail_message_id": str(message.id),
                    "mail_account_id": str(mail_account_id),
                    "tenant_id": str(tenant_id),
                    "provider_message_id": message.provider_message_id,
                    "version": message.version,
                    "action": "updated",
                },
            )
            return True

        return False

    async def _process_provider_removal(
        self,
        tenant_id: uuid.UUID,
        mail_account_id: uuid.UUID,
        folder_id: uuid.UUID,
        removal_id: str,
    ) -> bool:
        """Handle scoped folder removal (@removed)."""
        message = await self.message_repo.get_by_provider_id_for_update(mail_account_id, removal_id)

        if message is None:
            return False

        removed = await self.pivot_repo.remove_membership(message.id, folder_id)
        if removed:
            message.version += 1
            outbox_key = f"{message.id}::{message.version}::MAIL_MESSAGE_MUTATED"
            await self.outbox_repo.create_event(
                tenant_id=tenant_id,
                aggregate_id=message.id,
                aggregate_version=message.version,
                event_type="MAIL_MESSAGE_MUTATED",
                idempotency_key=outbox_key,
                payload={
                    "mail_message_id": str(message.id),
                    "mail_account_id": str(mail_account_id),
                    "tenant_id": str(tenant_id),
                    "provider_message_id": message.provider_message_id,
                    "version": message.version,
                    "action": "folder_removed",
                    "removed_from_folder_id": str(folder_id),
                },
            )
            return True

        return False

    async def _reconcile_stale_memberships(
        self,
        tenant_id: uuid.UUID,
        mail_account_id: uuid.UUID,
        folder_id: uuid.UUID,
        current_resync_generation: int,
    ) -> None:
        """Clean up folder memberships for this folder from previous resync generations."""
        stale_pivots = await self.pivot_repo.get_stale_memberships(
            folder_id, current_resync_generation
        )
        for pivot in stale_pivots:
            message = await self.message_repo.get(pivot.mail_message_id)
            if message is not None:
                await self.pivot_repo.remove_membership(message.id, folder_id)
                message.version += 1
                outbox_key = f"{message.id}::{message.version}::MAIL_MESSAGE_MUTATED"
                await self.outbox_repo.create_event(
                    tenant_id=tenant_id,
                    aggregate_id=message.id,
                    aggregate_version=message.version,
                    event_type="MAIL_MESSAGE_MUTATED",
                    idempotency_key=outbox_key,
                    payload={
                        "mail_message_id": str(message.id),
                        "mail_account_id": str(mail_account_id),
                        "tenant_id": str(tenant_id),
                        "provider_message_id": message.provider_message_id,
                        "version": message.version,
                        "action": "stale_membership_reconciled",
                        "removed_from_folder_id": str(folder_id),
                    },
                )
