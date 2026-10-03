"""ARQ worker entrypoint for the mail intelligence platform (PR-3.3).

Registered production jobs
--------------------------
===========================  =====================================================
``process_outbox_event_job`` Projects one outbox event into Elasticsearch.
``outbox_polling_cron``      Discovers eligible outbox events and enqueues them.
``mail_sync_discovery_cron`` Discovers folders and enqueues due folder syncs.
``sync_mailbox_job``         Runs one ``SyncOrchestrator.sync_folder`` pass.
``embed_message_job``        Generates and writes a message embedding.
``backfill_tenant_embeddings_job``  Bulk re-embeds a tenant.
===========================  =====================================================

PR-3.3 / H1 -- mail sync wiring
-------------------------------
Before PR-3.3 ``SyncOrchestrator`` had **no production call site anywhere in the
repository**: the only references were in tests. The outbox worker projected
messages that already existed in PostgreSQL, and nothing ever put them there.
The delta loop, 410 resync, participant reconciliation, and
refresh-on-``AuthExpired`` logic were all unreachable at runtime.

``mail_sync_discovery_cron`` + ``sync_mailbox_job`` wire the *existing* frozen
``SyncOrchestrator`` into ARQ. No second sync engine is introduced: the job is a
thin adapter that resolves credentials, builds the orchestrator, and calls
``sync_folder``.

PR-3.3 / B2 -- Elasticsearch configuration
------------------------------------------
The worker previously hardcoded ``http://localhost:9200`` in the ES adapter
constructor and read no Elasticsearch environment variable at all. Startup now
resolves the URL through the canonical ``Settings.elasticsearch_url`` and fails
fast when ``ELASTICSEARCH_HOST`` has not been set explicitly, so a
misconfigured deployment cannot silently write to loopback.

PR-3.3 / B1 -- startup / packaging
----------------------------------
Imports ``mip_ai`` and ``app.*``, neither of which the worker image previously
installed. Startup also applies the canonical structured logging configuration
so worker logs are the same JSON envelope as the API's, and owns a single
shared ``httpx.AsyncClient`` instead of opening a new connection per operation.
"""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
from arq import cron
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mip_models.mail import MailSyncStateValue
from mip_workers.backfill import backfill_tenant_embeddings_job
from mip_workers.es_adapter import ElasticsearchMailAdapter
from mip_workers.es_provisioning import (
    ElasticsearchIndexProvisioner,
    resolve_elasticsearch_url,
    resolve_embedding_dimensions,
)
from mip_workers.observability import (
    configure_worker_logging,
    get_worker_logger,
    record_outbox_dead_letter,
    record_outbox_failure,
    record_outbox_success,
    record_sync_auth_required,
    record_sync_failure,
    record_sync_success,
    safe_log_fields,
    set_outbox_pending,
)
from mip_workers.outbox_worker import OutboxWorker, outbox_job_id

if TYPE_CHECKING:
    from mip_models.mail import MailAccount, MailFolder, MailSyncState

logger = get_worker_logger(__name__)

#: Namespaced ARQ heartbeat key used by the container HEALTHCHECK.
HEALTH_CHECK_KEY = "mip:worker:heartbeat"
#: ARQ writes the heartbeat on this interval; the health check allows 3x slack.
HEALTH_CHECK_INTERVAL_SECONDS = 30
HEALTH_CHECK_MAX_AGE_SECONDS = HEALTH_CHECK_INTERVAL_SECONDS * 3


class WorkerConfigurationError(RuntimeError):
    """Raised when the worker cannot start with the supplied configuration."""


# Worker tuning knobs.
#
# These are read from the environment rather than from ``Settings`` because
# ``apps/api/app/common/config.py`` (owned outside this slice) does not declare
# them. They are read once at startup into ``ctx`` so a job never re-parses the
# environment. Promoting them to ``Settings`` fields is a follow-up, not a
# blocker: reading them here keeps the worker correct today.
DEFAULT_OUTBOX_BATCH_SIZE = 50
DEFAULT_OUTBOX_LEASE_MINUTES = 5


def outbox_batch_size(environ: dict[str, str] | None = None) -> int:
    """Return the outbox discovery batch size."""
    env = os.environ if environ is None else environ
    try:
        return max(1, int(env.get("OUTBOX_BATCH_SIZE", DEFAULT_OUTBOX_BATCH_SIZE)))
    except (TypeError, ValueError):
        return DEFAULT_OUTBOX_BATCH_SIZE


def outbox_lease_minutes(environ: dict[str, str] | None = None) -> int:
    """Return the outbox/sync lease duration in minutes."""
    env = os.environ if environ is None else environ
    try:
        return max(1, int(env.get("OUTBOX_LEASE_MINUTES", DEFAULT_OUTBOX_LEASE_MINUTES)))
    except (TypeError, ValueError):
        return DEFAULT_OUTBOX_LEASE_MINUTES


# ----------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------
async def startup(ctx: dict[str, Any]) -> None:
    """Initialise shared worker resources.

    Fails fast and loudly: a misconfigured worker must not silently degrade
    against a wrong Elasticsearch cluster or an unprovisioned database.
    """
    from app.common.config import get_settings

    settings = get_settings()

    # H8: worker logs must be structured exactly like the API's.
    configure_worker_logging(settings.app_log_level, settings.app_log_format)
    logger.info(
        "worker_starting",
        extra=safe_log_fields(
            {
                "app_env": settings.app_env.value,
                "count": outbox_batch_size(),
                "dimensions": settings.embedding_dimension,
            }
        ),
    )

    # B2: resolve the canonical Elasticsearch URL, or refuse to start.
    elasticsearch_url = resolve_elasticsearch_url()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # One shared client: reuses connections across Elasticsearch and Graph calls
    # instead of paying a TCP+TLS handshake per operation.
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    from mip_ai.embeddings import get_embedding_provider

    ctx["settings"] = settings
    ctx["db_engine"] = engine
    ctx["sessionmaker"] = sessionmaker
    ctx["http_client"] = http_client
    ctx["elasticsearch_url"] = elasticsearch_url
    ctx["es_adapter"] = ElasticsearchMailAdapter(base_url=elasticsearch_url, client=http_client)
    # M7: the same dimension value the embedding provider uses, passed
    # explicitly so producer, provisioning, and retrieval cannot drift.
    ctx["embedding_dimensions"] = resolve_embedding_dimensions(settings.embedding_dimension)
    ctx["outbox_batch_size"] = outbox_batch_size()
    ctx["outbox_lease_minutes"] = outbox_lease_minutes()
    ctx["index_provisioner"] = ElasticsearchIndexProvisioner(
        elasticsearch_url,
        client=http_client,
        dimensions=ctx["embedding_dimensions"],
    )
    ctx["embedding_provider"] = get_embedding_provider()
    ctx["worker_id"] = uuid.uuid4()
    ctx["started_at"] = time.time()

    logger.info(
        "worker_started",
        extra=safe_log_fields(
            {"index": elasticsearch_url, "dimensions": ctx["embedding_dimensions"]}
        ),
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    """Release shared worker resources."""
    client: httpx.AsyncClient | None = ctx.get("http_client")
    if client is not None:
        await client.aclose()
        ctx["http_client"] = None

    engine = ctx.get("db_engine")
    if engine is not None:
        await engine.dispose()
        ctx["db_engine"] = None

    logger.info("worker_stopped")


def check_health() -> None:
    """Container HEALTHCHECK entrypoint.

    Asserts the ARQ heartbeat key is present and recent. The worker has no HTTP
    listener, so the Redis heartbeat is the only meaningful liveness signal.

    Raises:
        SystemExit: Non-zero when the heartbeat is missing or stale.
    """
    from redis import asyncio as aioredis

    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD") or None
    database = int(os.getenv("REDIS_DB", "0"))

    async def _probe() -> bool:
        client = aioredis.Redis(
            host=host, port=port, password=password, db=database, socket_timeout=5.0
        )
        try:
            data = await client.hgetall(HEALTH_CHECK_KEY)
        finally:
            await client.aclose()
        if not data:
            return False
        finished = data.get(b"finished") or data.get("finished")
        if finished is None:
            return False
        return (time.time() - float(finished)) <= HEALTH_CHECK_MAX_AGE_SECONDS

    fresh = asyncio_run(_probe())
    if not fresh:
        msg = f"Worker heartbeat {HEALTH_CHECK_KEY!r} is missing or stale."
        raise SystemExit(msg)


def asyncio_run(coro: Any) -> Any:
    """Run a coroutine to completion (used by the synchronous health check)."""
    import asyncio

    return asyncio.run(coro)


# ----------------------------------------------------------------------
# Outbox projection
# ----------------------------------------------------------------------
async def process_outbox_event_job(
    ctx: dict[str, Any], event_id: str, lease_version: int | None = None
) -> bool:
    """Project a single outbox event into Elasticsearch.

    Args:
        event_id: Serialized outbox event id.
        lease_version: The lease version this job was scheduled for. Present so
            the enqueue can be traced back to a specific claim; the lease is
            always re-acquired (or verified) inside ``process_outbox_event`` and
            correctness never depends on this value.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    es_adapter = ctx["es_adapter"]
    arq_redis = ctx.get("redis")
    worker_id: uuid.UUID = ctx["worker_id"]
    lease_minutes: int = ctx.get("outbox_lease_minutes", DEFAULT_OUTBOX_LEASE_MINUTES)

    async with sessionmaker() as session:
        worker = OutboxWorker(session, es_adapter=es_adapter, arq_redis=arq_redis)
        return await worker.process_outbox_event(
            uuid.UUID(str(event_id)),
            worker_id,
            lease_duration=timedelta(minutes=lease_minutes),
        )


async def discover_outbox_events(session: AsyncSession, limit: int = 50) -> list[uuid.UUID]:
    """Discover eligible outbox event ids for processing.

    Eligible predicate: ``status IN ('PENDING', 'IN_FLIGHT')``,
    ``next_attempt_at <= NOW()``, and lock unassigned or expired.
    """
    from mip_models.mail import OutboxEvent, OutboxEventStatus

    now = datetime.now(UTC)
    stmt = (
        select(OutboxEvent.id)
        .where(
            OutboxEvent.status.in_([OutboxEventStatus.PENDING, OutboxEventStatus.IN_FLIGHT]),
            OutboxEvent.next_attempt_at <= now,
            or_(OutboxEvent.locked_by.is_(None), OutboxEvent.locked_until < now),
        )
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def outbox_polling_cron(ctx: dict[str, Any]) -> int:
    """Discover eligible outbox events and enqueue them for projection.

    M1 -- duplicate scheduling. Before PR-3.3 this enqueued with no ``_job_id``,
    so ARQ generated a fresh uuid on every 10-second tick and the same event was
    re-enqueued on every tick until a job acquired its lease. Each event is now
    enqueued under ``outbox:{event_id}:{lease_version}``: ARQ collapses repeated
    ticks onto one queue entry, while a genuine re-claim (which increments
    ``lease_version``) is enqueued normally.

    Correctness does not depend on the queue -- ``acquire_outbox_lease`` remains
    the single source of truth -- so this is an optimisation, not a lock.

    Returns:
        The number of jobs newly enqueued (ARQ-deduplicated ones excluded).
    """
    sessionmaker: async_sessionmaker[AsyncSession] | None = ctx.get("sessionmaker")
    if sessionmaker is None:
        return 0
    redis = ctx.get("redis")
    batch_size = ctx.get("outbox_batch_size", 50)

    from mip_models.mail import OutboxEvent

    async with sessionmaker() as session:
        event_ids = await discover_outbox_events(session, limit=batch_size)
        lease_versions: dict[uuid.UUID, int] = {}
        if event_ids:
            # One extra round trip; the eligibility predicate stays here so it
            # cannot drift from the projection worker.
            rows = await session.execute(
                select(OutboxEvent.id, OutboxEvent.lease_version).where(
                    OutboxEvent.id.in_(event_ids)
                )
            )
            lease_versions = {row[0]: int(row[1]) for row in rows}

    set_outbox_pending(len(event_ids))
    if not event_ids:
        return 0

    enqueued = 0
    for event_id in event_ids:
        lease_version = lease_versions.get(event_id)
        if lease_version is None:
            continue
        if redis is None:
            await process_outbox_event_job(ctx, str(event_id))
            enqueued += 1
            continue
        job = await redis.enqueue_job(
            "process_outbox_event_job",
            str(event_id),
            lease_version,
            _job_id=outbox_job_id(event_id, lease_version),
        )
        # None means ARQ already holds this exact claim: correctly deduplicated.
        if job is not None:
            enqueued += 1

    logger.info("outbox_polled", extra=safe_log_fields({"count": enqueued, "outcome": "enqueued"}))
    return enqueued


# ----------------------------------------------------------------------
# Mail sync (PR-3.3 / H1)
# ----------------------------------------------------------------------
async def mail_sync_discovery_cron(ctx: dict[str, Any]) -> None:
    """Enqueue folder syncs that are due, discovering folders when absent.

    This is the entry point that makes mail ingestion possible at all. It does
    not implement synchronization: it only decides *what* needs syncing and hands
    the work to :func:`sync_mailbox_job`, which delegates to the frozen
    ``SyncOrchestrator``.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    redis = ctx.get("redis")
    batch_size = ctx.get("outbox_batch_size", DEFAULT_OUTBOX_BATCH_SIZE)

    from mip_models.mail import MailAccount, MailFolder, MailSyncState

    async with sessionmaker() as session:
        accounts = (
            (
                await session.execute(
                    select(MailAccount)
                    .where(
                        MailAccount.is_active.is_(True),
                        MailAccount.deleted_at.is_(None),
                        MailAccount.status.in_(["active", "syncing"]),
                    )
                    .order_by(MailAccount.last_sync_at.nullsfirst())
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
        if not accounts:
            return

        account_ids = [account.id for account in accounts]
        folders = (
            (
                await session.execute(
                    select(MailFolder).where(
                        MailFolder.mail_account_id.in_(account_ids),
                        MailFolder.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        folders_by_account: dict[uuid.UUID, list[MailFolder]] = {}
        for folder in folders:
            folders_by_account.setdefault(folder.mail_account_id, []).append(folder)

        states: dict[uuid.UUID, MailSyncState] = {}
        if folders:
            state_rows = (
                (
                    await session.execute(
                        select(MailSyncState).where(
                            MailSyncState.mail_folder_id.in_([f.id for f in folders])
                        )
                    )
                )
                .scalars()
                .all()
            )
            states = {s.mail_folder_id: s for s in state_rows}

    due: list[uuid.UUID] = []
    for account in accounts:
        account_folders = folders_by_account.get(account.id, [])
        if not account_folders:
            # Folder set has never been discovered for this mailbox.
            await _discover_folders(ctx, account)
            continue
        for folder in account_folders:
            if _is_sync_due(states.get(folder.id)):
                due.append(folder.id)

    if not due:
        return

    for folder_id in due:
        # Deterministic per folder: repeated discovery ticks within the sync
        # window collapse onto one queue entry.
        job = None
        if redis is not None:
            job = await redis.enqueue_job(
                "sync_mailbox_job", str(folder_id), _job_id=f"sync:{folder_id}"
            )
            if job is None:
                continue
        await sync_mailbox_job(ctx, str(folder_id))

    logger.info("sync_scheduled", extra=safe_log_fields({"count": len(due), "outcome": "enqueued"}))


def _is_sync_due(state: MailSyncState | None) -> bool:
    """Return True when a folder needs another sync pass.

    ``AUTH_REQUIRED`` is excluded because it needs operator re-consent, not
    retries, and retrying cannot succeed. ``ERROR`` is excluded so a
    permanently failing folder does not spin on every discovery tick.
    """
    if state is None:
        return True
    if state.state == MailSyncStateValue.PENDING_INITIAL_SYNC:
        return True
    if state.state == MailSyncStateValue.SYNCING:
        # A crashed previous run leaves the lock to expire; treat it as due so
        # the fenced re-claim can make progress.
        return state.locked_until is None or state.locked_until < datetime.now(UTC)
    return False


async def _discover_folders(ctx: dict[str, Any], account: MailAccount) -> int:
    """Create MailFolder rows for a mailbox that has none.

    Returns the number of folders created. Failures are logged and swallowed:
    one unreachable mailbox must not stop discovery for every other tenant.
    """
    from mip_models.mail import MailFolder

    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    try:
        token = await _load_access_token(ctx, account)
        from mip_providers.mail.graph import MicrosoftGraphMailAdapter

        adapter = MicrosoftGraphMailAdapter(access_token=token, client=ctx.get("http_client"))
        provider_folders = await adapter.get_folders()
    except Exception as exc:
        logger.error(
            "sync_folder_discovery_failed",
            extra=safe_log_fields(
                {
                    "mail_account_id": str(account.id),
                    "tenant_id": str(account.tenant_id),
                    "error_class": type(exc).__name__,
                }
            ),
        )
        record_sync_failure("error")
        return 0

    if not provider_folders:
        return 0

    async with sessionmaker() as session:
        existing = {
            row[0]
            for row in (
                await session.execute(
                    select(MailFolder.provider_folder_id).where(
                        MailFolder.mail_account_id == account.id
                    )
                )
            ).all()
        }
        created = 0
        for provider_folder in provider_folders:
            if provider_folder.provider_folder_id in existing:
                continue
            session.add(
                MailFolder(
                    tenant_id=account.tenant_id,
                    mail_account_id=account.id,
                    provider_folder_id=provider_folder.provider_folder_id,
                    name=provider_folder.name,
                    is_active=provider_folder.is_active,
                )
            )
            created += 1
        await session.commit()

    logger.info(
        "sync_folders_discovered",
        extra=safe_log_fields(
            {
                "mail_account_id": str(account.id),
                "tenant_id": str(account.tenant_id),
                "count": created,
            }
        ),
    )
    return created


async def _load_access_token(ctx: dict[str, Any], account: MailAccount) -> str:
    """Return a usable Microsoft Graph access token for a mailbox.

    Prefers the stored credential and refreshes through the *existing* fenced
    refresh service when it is expired. A loader may be injected via
    ``ctx["access_token_loader"]`` so the sync job can be tested without real
    credentials.
    """
    loader = ctx.get("access_token_loader")
    if loader is not None:
        return await loader(ctx, account)

    from mip_models.mail import ProviderCredential

    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    async with sessionmaker() as session:
        credential = (
            (
                await session.execute(
                    select(ProviderCredential).where(
                        ProviderCredential.mail_account_id == account.id
                    )
                )
            )
            .scalars()
            .first()
        )
        if credential is None:
            msg = f"MailAccount {account.id} has no stored provider credential."
            raise WorkerConfigurationError(msg)

        encryption = _encryption_service(ctx)
        token = encryption.decrypt_string(credential.encrypted_access_token)
        expires_at = credential.token_expires_at
        skew = timedelta(minutes=2)
        if expires_at is not None and expires_at <= datetime.now(UTC) + skew:
            auth_service = _provider_auth_service(ctx, encryption)
            refreshed = await auth_service.refresh_mail_account_credentials(
                session,
                account.id,
                worker_id=ctx["worker_id"],
                expected_generation=account.credential_generation,
            )
            token = refreshed.access_token
    return token


def _encryption_service(ctx: dict[str, Any]) -> Any:
    """Return the shared EncryptionService, building it on first use."""
    service = ctx.get("encryption_service")
    if service is None:
        from app.common.encryption import EncryptionService

        service = EncryptionService()
        ctx["encryption_service"] = service
    return service


def _provider_auth_service(ctx: dict[str, Any], encryption: Any = None) -> Any:
    """Return the shared ProviderAuthService, building it on first use."""
    service = ctx.get("provider_auth_service")
    if service is None:
        from app.common.config import get_settings
        from app.services.identity_provider import ProviderAuthService
        from mip_providers.identity.entra import EntraIdentityProviderAuth

        settings = get_settings()
        encryption = encryption if encryption is not None else _encryption_service(ctx)
        provider_auth = EntraIdentityProviderAuth(settings=settings, encryption_service=encryption)
        service = ProviderAuthService(
            provider_auth=provider_auth, settings=settings, encryption_service=encryption
        )
        ctx["provider_auth_service"] = service
    return service


async def sync_mailbox_job(ctx: dict[str, Any], mail_folder_id: str) -> str | None:
    """Run one folder sync pass via the frozen ``SyncOrchestrator``.

    Returns:
        The resulting ``MailSyncState`` value, or ``None`` when the folder could
        not be loaded.
    """
    from app.services.sync_orchestrator import SyncOrchestrator
    from mip_models.mail import MailFolder

    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    folder_uuid = uuid.UUID(str(mail_folder_id))
    lease_minutes: int = ctx.get("outbox_lease_minutes", DEFAULT_OUTBOX_LEASE_MINUTES)

    async with sessionmaker() as session:
        folder = (
            (await session.execute(select(MailFolder).where(MailFolder.id == folder_uuid)))
            .scalars()
            .first()
        )
        if folder is None or not folder.is_active:
            logger.info(
                "sync_skipped",
                extra=safe_log_fields({"mail_folder_id": str(folder_uuid)}),
            )
            return None

        account = folder.mail_account if folder.mail_account is not None else None
        if account is None:
            from mip_models.mail import MailAccount

            account = (
                (
                    await session.execute(
                        select(MailAccount).where(MailAccount.id == folder.mail_account_id)
                    )
                )
                .scalars()
                .first()
            )
        if account is None:
            record_sync_failure("error")
            return None

        try:
            token = await _load_access_token(ctx, account)
        except WorkerConfigurationError:
            raise
        except Exception as exc:
            logger.error(
                "sync_credentials_unavailable",
                extra=safe_log_fields(
                    {
                        "mail_folder_id": str(folder_uuid),
                        "tenant_id": str(folder.tenant_id),
                        "error_class": type(exc).__name__,
                    }
                ),
            )
            record_sync_auth_required()
            return MailSyncStateValue.AUTH_REQUIRED

        encryption = _encryption_service(ctx)
        orchestrator = SyncOrchestrator(
            session=session,
            provider_auth_service=_provider_auth_service(ctx, encryption),
        )
        result = await orchestrator.sync_folder(
            mail_folder_id=folder_uuid,
            worker_id=ctx["worker_id"],
            access_token=token,
            lease_duration=timedelta(minutes=lease_minutes),
        )

    state = result.state
    log_fields = {
        "mail_folder_id": str(folder_uuid),
        "tenant_id": str(folder.tenant_id),
        "state": str(state),
        "count": result.messages_processed,
        "reason": result.error,
    }
    if state == MailSyncStateValue.AUTH_REQUIRED:
        record_sync_auth_required()
        logger.warning("sync_auth_required", extra=safe_log_fields(log_fields))
    elif result.error is not None or state == MailSyncStateValue.ERROR:
        record_sync_failure("error")
        logger.error("sync_failure", extra=safe_log_fields(log_fields))
    else:
        record_sync_success()
        logger.info("sync_success", extra=safe_log_fields(log_fields))

    return str(state)


# ----------------------------------------------------------------------
# Embeddings
# ----------------------------------------------------------------------
async def embed_message_job(
    ctx: dict[str, Any],
    message_id: str,
    tenant_id: str,
    version: int,
    semantic_text: str,
) -> bool:
    """Generate an embedding and write it into the tenant index.

    Args:
        semantic_text: Pre-computed text. Never logged: it is derived from mail
            content and is covered by the enterprise mail privacy boundary.
    """
    from mip_ai.embeddings.errors import EmbeddingPermanentError, EmbeddingTransientError
    from mip_workers.es_adapter import (
        PermanentElasticsearchError,
        RetryableElasticsearchError,
    )

    provider = ctx.get("embedding_provider")
    es_adapter = ctx.get("es_adapter")
    if provider is None or es_adapter is None:
        msg = "embed_message_job requires ctx['embedding_provider'] and ctx['es_adapter']."
        raise WorkerConfigurationError(msg)

    try:
        result = await provider.embed([semantic_text])
        if not result.vectors:
            return False
        semantic_vector = result.vectors[0]
        index_name = f"mail_messages_{tenant_id}"

        success = await es_adapter.update_embeddings(
            index_name=index_name,
            document_id=message_id,
            semantic_vector=semantic_vector,
            embedding_model_id=result.model_id,
            version=version,
        )
        if success:
            record_outbox_success()
        return bool(success)

    except EmbeddingTransientError as exc:
        # Transient (429/5xx/timeout): let ARQ retry the job.
        logger.warning(
            "embed_retryable_failed",
            extra=safe_log_fields({"message_id": message_id, "error_class": type(exc).__name__}),
        )
        record_outbox_failure("retryable")
        raise

    except EmbeddingPermanentError as exc:
        logger.error(
            "embed_permanent_failed",
            extra=safe_log_fields({"message_id": message_id, "error_class": type(exc).__name__}),
        )
        record_outbox_dead_letter()
        return False

    except RetryableElasticsearchError as exc:
        logger.warning(
            "embed_es_retryable_failed",
            extra=safe_log_fields({"message_id": message_id, "error_class": type(exc).__name__}),
        )
        record_outbox_failure("retryable")
        raise

    except PermanentElasticsearchError as exc:
        logger.error(
            "embed_es_permanent_failed",
            extra=safe_log_fields({"message_id": message_id, "error_class": type(exc).__name__}),
        )
        record_outbox_dead_letter()
        return False

    except Exception as exc:
        logger.error(
            "embed_unclassified_failed",
            extra=safe_log_fields({"message_id": message_id, "error_class": type(exc).__name__}),
        )
        record_outbox_failure("unknown")
        raise


# ----------------------------------------------------------------------
# ARQ configuration
# ----------------------------------------------------------------------
class WorkerSettings:
    """ARQ worker configuration."""

    functions: ClassVar[list[Any]] = [
        process_outbox_event_job,
        mail_sync_discovery_cron,
        sync_mailbox_job,
        embed_message_job,
        backfill_tenant_embeddings_job,
    ]

    cron_jobs: ClassVar[list[Any]] = [
        # M13: the poller and the discovery job are registered as crons only.
        # They were previously listed in both `functions` and `cron_jobs`, which
        # made scheduled and manually-enqueued execution indistinguishable.
        cron(outbox_polling_cron, second={0, 10, 20, 30, 40, 50}),
        cron(mail_sync_discovery_cron, minute={1, 16, 31, 46}),
    ]

    redis_settings: ClassVar[Any] = None  # Populated below by build_redis_settings().

    on_startup = startup
    on_shutdown = shutdown

    job_timeout: ClassVar[int] = 600
    max_tries: ClassVar[int] = 3
    keep_result: ClassVar[int] = 3600
    health_check_interval: ClassVar[int] = HEALTH_CHECK_INTERVAL_SECONDS
    health_check_key: ClassVar[str] = HEALTH_CHECK_KEY
    max_jobs: ClassVar[int] = 10


def build_redis_settings() -> Any:
    """Build ARQ RedisSettings from the environment."""
    from arq.connections import RedisSettings

    return RedisSettings(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD") or None,
        database=int(os.getenv("REDIS_DB", "0")),
    )


# Resolved at import time so `WorkerSettings.redis_settings` is a real class
# attribute, as ARQ requires. Wrapped in contextlib purely for symmetry with the
# lazy settings above.
with contextlib.suppress(Exception):
    WorkerSettings.redis_settings = build_redis_settings()
