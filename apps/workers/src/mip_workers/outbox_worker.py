"""Outbox projection worker: PostgreSQL outbox events to Elasticsearch (PR-3.3).

This module is the transactional-outbox consumer. It claims a single outbox event
under a fenced lease, projects the corresponding ``MailMessage`` into the
per-tenant Elasticsearch index, and then drives the event to a terminal state.

PR-3.3 changes, all confined to the worker/Elasticsearch slice
---------------------------------------------------------------
**M2 -- enqueue recovery and the transaction invariant.**

The previous ordering was::

    index_message()  # Elasticsearch
    update_status_cas(DONE)  # opens a PostgreSQL write transaction
    enqueue_job(embed)  # awaits Redis *while the transaction is open*
    commit()

If Redis was unavailable at the enqueue step, the handler attempted a
``DEAD_LETTER`` CAS. That CAS could not match, because the *uncommitted* ``DONE``
transition had already cleared ``locked_by``. The handler's trailing
``if self.session.in_transaction(): commit()`` then committed the ``DONE``
transition instead. The net effect: a transient Redis outage permanently marked
the event ``DONE`` with no embedding ever enqueued, no retry scheduled, and no
error recorded -- a silent, permanent loss of semantic search for that message.

The required invariant is now implemented as::

    PENDING
      -> enqueue attempt
      -> enqueue succeeds
      -> durable DB transition to DONE
      -> commit

    enqueue failure                        -> event returns to PENDING with backoff
    crash after enqueue, before commit     -> the same logical job is retried and
                                             duplicate delivery is harmless

The embedding job is therefore enqueued *before* the terminal CAS, with no
PostgreSQL transaction open. The enqueue carries a deterministic ``_job_id``
derived from the message identity and version, and the Elasticsearch
``update_embeddings`` painless script refuses to overwrite a newer version, so
duplicate delivery is idempotent at both the queue and the document layer.

**M3 -- error classification.**

The previous handler was ``except (PermanentElasticsearchError, Exception)``,
which is exactly a bare ``except Exception``: a ``TypeError`` in document
construction was classified identically to a genuine mapping conflict and
dead-lettered the event permanently. Classification is now explicit --
``PermanentElasticsearchError`` dead-letters, while retryable *and* unclassified
errors return the event to ``PENDING`` with exponential backoff, dead-lettering
only once the attempt budget is genuinely spent.

**M8 -- canonical sender extraction.** See
:func:`mip_workers.es_provisioning.canonical_sender_email`.

**H3 -- index provisioning.** The tenant index is provisioned from the
authoritative mapping before the first write, so an index is never created by
Elasticsearch dynamic mapping.
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
from mip_workers.es_provisioning import (
    ElasticsearchIndexProvisioner,
    canonical_sender_email,
    resolve_elasticsearch_url,
)
from mip_workers.observability import (
    record_outbox_dead_letter,
    record_outbox_failure,
    record_outbox_success,
)

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

    from mip_models.mail import MailMessage, OutboxEvent

logger = logging.getLogger(__name__)

#: Only this event type is projected by the outbox consumer.
SUPPORTED_EVENT_TYPE = "MAIL_MESSAGE_MUTATED"

#: Secret-shaped substrings are redacted before an error is persisted into
#: ``outbox_events.last_error``, which is durable and operator-visible. Graph and
#: Entra can echo request context back inside their error bodies.
_SECRET_PATTERN = re.compile(
    r"(Bearer\s+[A-Za-z0-9._~\+\/-]+=*)"
    r"|(access_token=[^&\s]+)"
    r"|(refresh_token=[^&\s]+)"
    r"|(api_key=[^&\s]+)"
    r"|(client_secret=[^&\s]+)"
    r"|(password=[^&\s]+)",
    re.IGNORECASE,
)


def sanitize_error(error: Exception | str) -> str:
    """Redact secret-shaped substrings from a provider error message."""
    return _SECRET_PATTERN.sub("[REDACTED_SECRET]", str(error))


def embed_job_id(message_id: uuid.UUID, version: int) -> str:
    """Return the deterministic ARQ job id for an embedding job.

    The logical identity of "embed message X at version N" is exactly
    ``(message_id, version)``.

    Verified ARQ semantics (``arq/connections.py`` v0.28.0):
    ``enqueue_job`` returns ``None`` when either ``arq:job:{id}`` **or**
    ``arq:result:{id}`` exists, and ``finish_job`` deletes the former while
    writing the latter with a ``keep_result`` TTL. Deduplication is therefore
    bounded by ``keep_result`` (default 3600s), *not* permanent. That is
    correct here: a message version is immutable, so re-embedding the same
    ``(id, version)`` can never be required, while a genuinely newer version
    yields a different job id.
    """
    return f"embed:{message_id}:{version}"


def outbox_job_id(event_id: uuid.UUID, lease_version: int) -> str:
    """Return the deterministic ARQ job id for a single outbox claim (M1).

    ARQ does not deduplicate a naive per-event job id: dedup holds only while
    ``arq:job:{id}`` or ``arq:result:{id}`` exists, and is released once the
    result TTL elapses. Re-claiming an event -- which happens whenever the
    previous lease expires -- increments ``lease_version`` (see
    ``OutboxEventRepository.acquire_outbox_lease``).

    Keying on ``(event_id, lease_version)`` therefore gives exactly the
    required semantics:

    * repeated poller ticks before the job runs collapse onto one queue entry;
    * a genuine retry after a crash, lease expiry, or backoff produces a new
      ``lease_version`` and is enqueued normally.

    This introduces no new distributed-locking mechanism. The PostgreSQL lease
    remains the single source of truth; ``_job_id`` is a queue-level
    optimisation layered on top of it, and correctness never depends on it.
    """
    return f"outbox:{event_id}:{lease_version}"


def _backoff_delay(attempt: int, base_seconds: float, max_seconds: float) -> float:
    """Exponential backoff, clamped to ``max_seconds``."""
    delay = base_seconds * (2 ** max(0, attempt - 1))
    return min(delay, max_seconds)


class OutboxWorker:
    """Projects outbox events into the per-tenant Elasticsearch index."""

    def __init__(
        self,
        session: AsyncSession,
        es_adapter: Any = None,
        arq_redis: Any = None,
        max_attempts: int = 5,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 300.0,
        index_provisioner: ElasticsearchIndexProvisioner | None = None,
    ) -> None:
        self.session = session
        self.es_adapter = (
            es_adapter
            if es_adapter is not None
            else ElasticsearchMailAdapter(base_url=resolve_elasticsearch_url())
        )
        self.arq_redis = arq_redis
        self.max_attempts = max_attempts
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds

        # Observed by tests to assert the M2 invariant: no PostgreSQL
        # transaction may be open when the ARQ enqueue is awaited.
        self.enqueue_transaction_violations = 0

        if index_provisioner is not None:
            self.index_provisioner: ElasticsearchIndexProvisioner | None = index_provisioner
        elif isinstance(self.es_adapter, ElasticsearchMailAdapter):
            # Provision only for a genuine Elasticsearch adapter, so injected
            # test doubles never require a live cluster.
            self.index_provisioner = ElasticsearchIndexProvisioner(self.es_adapter.base_url)
        else:
            self.index_provisioner = None

        self.outbox_repo = OutboxEventRepository(session)
        self.message_repo = MailMessageRepository(session)
        self.pivot_repo = MailMessageFolderRepository(session)
        self.participant_repo = MailMessageParticipantRepository(session)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def process_outbox_event(
        self,
        event_id: uuid.UUID,
        worker_id: uuid.UUID,
        lease_duration: timedelta = timedelta(minutes=5),
    ) -> bool:
        """Claim, project, and finalise a single outbox event.

        Returns:
            True when the event reached the terminal ``DONE`` state.
        """
        lease_version = await self.outbox_repo.acquire_outbox_lease(
            event_id, worker_id, lease_duration
        )
        if lease_version is None:
            # Another worker holds the lease, or the event is not eligible.
            # Both are normal and must not be retried.
            return False
        lease_version = int(lease_version)

        event = await self.outbox_repo.get(event_id)
        if event is None:
            return False

        if event.status in (OutboxEventStatus.DONE, OutboxEventStatus.DEAD_LETTER):
            return False

        if event.event_type != SUPPORTED_EVENT_TYPE:
            return await self._dead_letter(
                event,
                worker_id,
                lease_version,
                f"Unrecognized or unsupported event_type: '{event.event_type}'",
            )

        message = await self.message_repo.get(event.aggregate_id)
        if message is None:
            return await self._dead_letter(
                event,
                worker_id,
                lease_version,
                f"Canonical MailMessage {event.aggregate_id} not found",
            )

        # Tenant isolation: refuse to index the message under either tenant.
        if message.tenant_id != event.tenant_id:
            logger.error(
                "outbox_tenant_mismatch event_id=%s", event_id, extra={"event_id": str(event_id)}
            )
            return await self._dead_letter(
                event,
                worker_id,
                lease_version,
                (
                    f"Tenant isolation mismatch: message tenant {message.tenant_id} "
                    f"!= event tenant {event.tenant_id}"
                ),
            )

        participants = await self.participant_repo.get_by_message_id(message.id)
        memberships = await self.pivot_repo.get_memberships_for_message(message.id)

        document = _build_document(message, participants, memberships)
        index_name = f"mail_messages_{event.tenant_id}"

        if self.index_provisioner is not None:
            try:
                await self.index_provisioner.ensure_index(message.tenant_id)
            except Exception as exc:
                from mip_workers.es_provisioning import ElasticsearchProvisioningError

                if isinstance(exc, ElasticsearchProvisioningError) and exc.retryable:
                    return await self._fail_recoverable(
                        event, worker_id, lease_version, f"Index provisioning retryable: {exc}"
                    )
                return await self._dead_letter(
                    event, worker_id, lease_version, f"Index provisioning: {exc}"
                )

        # Release the DB connection before the external Elasticsearch call. The
        # lease is already durable in PostgreSQL, so no transaction needs to be
        # held for the duration of the network call.
        if self.session.in_transaction():
            await self.session.commit()

        try:
            result = await self.es_adapter.index_message(
                index_name=index_name,
                document=document,
                version=event.aggregate_version,
            )
        except PermanentElasticsearchError as exc:
            return await self._dead_letter(event, worker_id, lease_version, sanitize_error(exc))
        except RetryableElasticsearchError as exc:
            return await self._fail_recoverable(
                event, worker_id, lease_version, sanitize_error(exc), exc
            )
        except Exception as exc:
            return await self._fail_recoverable(
                event, worker_id, lease_version, f"unclassified: {sanitize_error(exc)}"
            )

        if not result.success:
            # The adapter reported non-success without raising. Treat as a
            # recoverable unclassified failure rather than leaving the event
            # dangling in IN_FLIGHT until the lease expires.
            return await self._fail_recoverable(
                event,
                worker_id,
                lease_version,
                f"Elasticsearch non-success (status {result.status_code})",
            )

        # ---- M2: enqueue first, durably mark DONE second ----
        # No PostgreSQL write transaction is open here: we committed above and
        # have issued no write since, so awaiting Redis cannot hold a pooled
        # connection open.
        if (
            self.arq_redis is not None
            and not message.is_deleted
            and not await self._enqueue_embedding(message)
        ):
            # Recoverable. The event returns to PENDING with backoff and is
            # re-discovered later; re-indexing the document is idempotent via
            # the Elasticsearch external version fence.
            return await self._fail_recoverable(
                event,
                worker_id,
                lease_version,
                "Embedding enqueue failed; event returned to PENDING for retry",
            )

        cas_ok = await self.outbox_repo.update_status_cas(
            event_id,
            worker_id,
            lease_version,
            new_status=OutboxEventStatus.DONE,
        )
        await self.session.commit()

        if cas_ok:
            record_outbox_success()
        return cas_ok

    # ------------------------------------------------------------------
    # Terminal state helpers
    # ------------------------------------------------------------------
    async def _dead_letter(
        self, event: OutboxEvent, worker_id: uuid.UUID, lease_version: int, reason: str
    ) -> bool:
        """Move an unrecoverable event to DEAD_LETTER and commit."""
        logger.error(
            "outbox_dead_letter",
            extra={
                "event_id": str(event.id),
                "event_type": event.event_type,
                "attempts": event.attempt_count + 1,
                "reason": reason,
            },
        )
        await self.outbox_repo.update_status_cas(
            event.id,
            worker_id,
            lease_version,
            new_status=OutboxEventStatus.DEAD_LETTER,
            last_error=reason,
        )
        await self.session.commit()
        record_outbox_dead_letter()
        return False

    async def _fail_recoverable(
        self,
        event: OutboxEvent,
        worker_id: uuid.UUID,
        lease_version: int,
        reason: str,
        exc: RetryableElasticsearchError | None = None,
    ) -> bool:
        """Return an event to PENDING with backoff, or dead-letter once spent.

        A single transient or unclassified failure never dead-letters the event.
        Only an exhausted attempt budget does.
        """
        attempt = event.attempt_count + 1

        if attempt >= self.max_attempts:
            logger.error(
                "outbox_dead_letter",
                extra={
                    "event_id": str(event.id),
                    "event_type": event.event_type,
                    "attempts": attempt,
                    "reason": reason,
                },
            )
            await self.outbox_repo.update_status_cas(
                event.id,
                worker_id,
                lease_version,
                new_status=OutboxEventStatus.DEAD_LETTER,
                last_error=reason,
            )
            await self.session.commit()
            record_outbox_dead_letter()
            return False

        if exc is not None and exc.retry_after is not None:
            delay = exc.retry_after
        else:
            delay = _backoff_delay(attempt, self.base_backoff_seconds, self.max_backoff_seconds)

        next_attempt = datetime.now(UTC) + timedelta(seconds=delay)
        logger.warning(
            "outbox_retry",
            extra={
                "event_id": str(event.id),
                "attempts": attempt,
                "next_attempt_in_seconds": delay,
                "reason": reason,
            },
        )
        await self.outbox_repo.update_status_cas(
            event.id,
            worker_id,
            lease_version,
            new_status=OutboxEventStatus.PENDING,
            last_error=reason,
            next_attempt_at=next_attempt,
        )
        await self.session.commit()
        record_outbox_failure("retryable" if exc is not None else "unknown")
        return False

    # ------------------------------------------------------------------
    # Embedding enqueue
    # ------------------------------------------------------------------
    async def _enqueue_embedding(self, message: MailMessage) -> bool:
        """Enqueue the embedding job while enforcing the no-open-transaction rule.

        Returns:
            True when the job was enqueued (or was already present under the
            same deterministic id). False when the enqueue failed and the event
            must be recovered.
        """
        from mip_ai.embeddings.text import build_semantic_text

        # Enforce the M2 invariant rather than merely documenting it.
        if self.session.in_transaction():
            self.enqueue_transaction_violations += 1
            logger.error(
                "outbox_open_transaction_at_enqueue",
                extra={"message_id": str(message.id)},
            )
            await self.session.rollback()
            return False

        # PR-3.3 / M8: read the canonical sender key. The previous code read the
        # raw Microsoft Graph shape (``sender["emailAddress"]["address"]``), which
        # never matches the persisted ``{"name", "email"}`` document, so every
        # generated embedding silently omitted the sender.
        sender_str = canonical_sender_email(message.sender)

        semantic_text = build_semantic_text(
            subject=message.subject or "",
            sender=sender_str,
            body=message.body_preview or "",
        )

        try:
            await self.arq_redis.enqueue_job(
                "embed_message_job",
                str(message.id),
                str(message.tenant_id),
                message.version,
                semantic_text,
                _job_id=embed_job_id(message.id, message.version),
            )
        except Exception:
            logger.error(
                "outbox_embedding_enqueue_failed",
                extra={"message_id": str(message.id), "version": message.version},
            )
            return False
        return True


# ----------------------------------------------------------------------
# Document projection
# ----------------------------------------------------------------------
def _build_document(
    message: MailMessage,
    participants: list[Any],
    memberships: list[Any],
) -> dict[str, Any]:
    """Build the canonical Elasticsearch document for a mail message.

    The field set here is exactly the field set declared by
    :func:`mip_workers.es_provisioning.build_mail_index_mappings`. The
    provisioned index uses ``dynamic: strict``, so this function and that
    mapping are two halves of one contract and must change together.
    """
    return {
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
        "sender_email": canonical_sender_email(message.sender) or None,
        "participants": [{"name": p.name, "email": p.email, "role": p.role} for p in participants],
        "received_date_time": (
            message.received_date_time.isoformat() if message.received_date_time else None
        ),
        "has_attachments": message.has_attachments,
        "is_read": message.is_read,
        "folder_ids": [str(m.mail_folder_id) for m in memberships],
    }
