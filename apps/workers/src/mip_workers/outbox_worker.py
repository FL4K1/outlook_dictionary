"""Transactional outbox worker (PR-2.4).

Processes OutboxEvents asynchronously, projecting canonical PostgreSQL message state
into Elasticsearch with fenced, at-least-once CAS semantics.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.repositories.mail import (
    MailMessageFolderRepository,
    MailMessageParticipantRepository,
    MailMessageRepository,
    OutboxEventRepository,
)
from mip_models.mail import OutboxEventStatus
from mip_workers.es_adapter import (
    ElasticsearchMailAdapter,
    PermanentElasticsearchError,
    RetryableElasticsearchError,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)

# Regex pattern for sanitizing secret tokens from error logs
_SECRET_PATTERN = re.compile(
    r"(Bearer\s+[A-Za-z0-9._~\+\/-]+=*)|(access_token=[^&\s]+)|(refresh_token=[^&\s]+)|(password=[^&\s]+)",
    re.IGNORECASE,
)


def sanitize_error(error: Exception | str) -> str:
    """Sanitize secrets, tokens, and credentials from exception messages."""
    raw_str = str(error)
    return _SECRET_PATTERN.sub("[REDACTED_SECRET]", raw_str)


class OutboxWorker:
    """Worker fetching pending outbox events and projecting canonical state to Elasticsearch."""

    def __init__(
        self,
        session: AsyncSession,
        es_adapter: Any = None,
        arq_redis: Any = None,
        max_attempts: int = 5,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 300.0,
    ) -> None:
        self.session = session
        self.es_adapter = es_adapter if es_adapter is not None else ElasticsearchMailAdapter()
        self.arq_redis = arq_redis
        self.max_attempts = max_attempts
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds

        self.outbox_repo = OutboxEventRepository(session)
        self.message_repo = MailMessageRepository(session)
        self.pivot_repo = MailMessageFolderRepository(session)
        self.participant_repo = MailMessageParticipantRepository(session)

    async def process_outbox_event(
        self,
        event_id: uuid.UUID,
        worker_id: uuid.UUID,
        lease_duration: timedelta = timedelta(minutes=5),
    ) -> bool:
        """Process a single outbox event with CAS lease fencing."""
        # 1. Acquire outbox worker lease
        lease_version = await self.outbox_repo.acquire_outbox_lease(
            event_id, worker_id, lease_duration
        )
        if lease_version is None:
            logger.debug(
                "Worker %s could not acquire outbox lease for event %s", worker_id, event_id
            )
            return False

        # 2. Read and validate event and canonical PostgreSQL state
        event = await self.outbox_repo.get(event_id)
        if event is None:
            return False

        if event.status in (OutboxEventStatus.DONE, OutboxEventStatus.DEAD_LETTER):
            return False

        if event.event_type != "MAIL_MESSAGE_MUTATED":
            err_msg = f"Unrecognized or unsupported event_type: '{event.event_type}'"
            await self.outbox_repo.update_status_cas(
                event_id,
                worker_id,
                lease_version,
                new_status=OutboxEventStatus.DEAD_LETTER,
                last_error=err_msg,
            )
            return False

        message = await self.message_repo.get(event.aggregate_id)
        if message is None:
            err_msg = f"Canonical MailMessage {event.aggregate_id} not found"
            await self.outbox_repo.update_status_cas(
                event_id,
                worker_id,
                lease_version,
                new_status=OutboxEventStatus.DEAD_LETTER,
                last_error=err_msg,
            )
            return False

        # Tenant isolation check
        if message.tenant_id != event.tenant_id:
            err_msg = (
                f"Tenant isolation mismatch: message tenant {message.tenant_id} "
                f"!= event tenant {event.tenant_id}"
            )
            logger.error(err_msg)

            await self.outbox_repo.update_status_cas(
                event_id,
                worker_id,
                lease_version,
                new_status=OutboxEventStatus.DEAD_LETTER,
                last_error=err_msg,
            )
            return False

        participants = await self.participant_repo.get_by_message_id(message.id)
        memberships = await self.pivot_repo.get_memberships_for_message(message.id)

        # Construct deterministic ES document from canonical DB state
        document: dict[str, Any] = {
            "id": str(message.id),
            "tenant_id": str(message.tenant_id),
            "mail_account_id": str(message.mail_account_id),
            "provider_message_id": message.provider_message_id,
            "version": message.version,
            "is_deleted": message.is_deleted,
            "subject": message.subject,
            "body": message.body,
            "body_preview": message.body_preview,
            "sender": message.sender,
            "participants": [
                {
                    "name": p.name,
                    "email": p.email,
                    "role": p.role,
                }
                for p in participants
            ],
            "received_date_time": (
                message.received_date_time.isoformat() if message.received_date_time else None
            ),
            "has_attachments": message.has_attachments,
            "is_read": message.is_read,
            "folder_ids": [str(m.mail_folder_id) for m in memberships],
        }

        index_name = f"mail_messages_{event.tenant_id}"

        # Commit/release DB transaction before external HTTP call (Fix NB-1)
        if self.session.in_transaction():
            await self.session.commit()

        # 3. Perform external Elasticsearch index HTTP request (OUTSIDE DB Transaction)
        try:
            res = await self.es_adapter.index_message(
                index_name=index_name,
                document=document,
                version=event.aggregate_version,
            )

            # 4. Fenced DB Transition on Success (including 409 version conflict)
            if res.success:
                cas_ok = await self.outbox_repo.update_status_cas(
                    event_id,
                    worker_id,
                    lease_version,
                    new_status=OutboxEventStatus.DONE,
                )
                if cas_ok and self.arq_redis is not None and not message.is_deleted:
                    from mip_ai.embeddings.text import build_semantic_text

                    # sender is a JSON column (dict), extract email for text
                    sender_str = ""
                    if isinstance(message.sender, dict):
                        sender_str = str(message.sender.get("emailAddress", {}).get("address", ""))
                    elif isinstance(message.sender, str):
                        sender_str = message.sender

                    semantic_text = build_semantic_text(
                        subject=message.subject or "",
                        sender=sender_str,
                        body=message.body_preview or "",
                    )
                    await self.arq_redis.enqueue_job(
                        "embed_message_job",
                        str(message.id),
                        str(message.tenant_id),
                        message.version,
                        semantic_text,
                    )
                if self.session.in_transaction():
                    await self.session.commit()
                return cas_ok

        except RetryableElasticsearchError as exc:
            sanitized = sanitize_error(exc)
            logger.warning(
                "Retryable error processing event %s (attempt %s): %s",
                event_id,
                event.attempt_count + 1,
                sanitized,
            )
            attempt = event.attempt_count + 1
            if attempt >= self.max_attempts:
                await self.outbox_repo.update_status_cas(
                    event_id,
                    worker_id,
                    lease_version,
                    new_status=OutboxEventStatus.DEAD_LETTER,
                    last_error=sanitized,
                )
            else:
                delay = (
                    exc.retry_after
                    if exc.retry_after is not None
                    else min(
                        self.max_backoff_seconds,
                        self.base_backoff_seconds * (2**event.attempt_count),
                    )
                )
                next_attempt = datetime.now(UTC) + timedelta(seconds=delay)
                await self.outbox_repo.update_status_cas(
                    event_id,
                    worker_id,
                    lease_version,
                    new_status=OutboxEventStatus.PENDING,
                    last_error=sanitized,
                    next_attempt_at=next_attempt,
                )
            if self.session.in_transaction():
                await self.session.commit()
            return False

        except (PermanentElasticsearchError, Exception) as exc:
            sanitized = sanitize_error(exc)
            logger.error("Permanent error processing event %s: %s", event_id, sanitized)
            await self.outbox_repo.update_status_cas(
                event_id,
                worker_id,
                lease_version,
                new_status=OutboxEventStatus.DEAD_LETTER,
                last_error=sanitized,
            )
            if self.session.in_transaction():
                await self.session.commit()
            return False

        return False
