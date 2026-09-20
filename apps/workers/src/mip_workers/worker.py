"""ARQ Worker Runtime & Outbox Event Job Dispatcher.

Implements ARQ WorkerSettings, Redis connection configuration,
outbox discovery polling, and job processing with per-job DB session isolation.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mip_models.mail import OutboxEvent, OutboxEventStatus
from mip_workers.backfill import backfill_tenant_embeddings_job
from mip_workers.es_adapter import ElasticsearchMailAdapter
from mip_workers.outbox_worker import OutboxWorker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://mip_user:mip_password@localhost:5432/mail_intelligence",
)
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)


async def startup(ctx: dict[str, Any]) -> None:
    """Initialize DB engine, sessionmaker, and ES adapter on worker startup."""
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    es_adapter = ElasticsearchMailAdapter()

    from mip_ai.embeddings import get_embedding_provider

    provider = get_embedding_provider()

    ctx["db_engine"] = engine
    ctx["sessionmaker"] = sessionmaker
    ctx["es_adapter"] = es_adapter
    ctx["embedding_provider"] = provider


async def shutdown(ctx: dict[str, Any]) -> None:
    """Clean up DB engine on worker shutdown."""
    engine = ctx.get("db_engine")
    if engine:
        await engine.dispose()


async def discover_outbox_events(session: AsyncSession, limit: int = 50) -> list[uuid.UUID]:
    """Discover eligible outbox event IDs for processing.

    Eligible predicate matches EDD §4.2:
    - status IN ('PENDING', 'IN_FLIGHT')
    - next_attempt_at <= NOW()
    - locked_by IS NULL OR locked_until < NOW()
    """
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


async def process_outbox_event_job(ctx: dict[str, Any], event_id_str: str) -> bool:
    """Job function processing a single outbox event with fresh DB session lifecycle."""
    sessionmaker: async_sessionmaker[AsyncSession] = ctx["sessionmaker"]
    es_adapter = ctx.get("es_adapter")

    event_id = uuid.UUID(event_id_str)
    worker_id = uuid.uuid4()

    async with sessionmaker() as session:
        worker = OutboxWorker(session, es_adapter=es_adapter, arq_redis=ctx.get("redis"))
        return await worker.process_outbox_event(event_id, worker_id)


async def embed_message_job(
    ctx: dict[str, Any],
    document_id: str,
    tenant_id: str,
    version: int,
    semantic_text: str,
) -> bool:
    """ARQ job to generate embeddings and execute partial Elasticsearch update."""
    import logging

    from mip_ai.embeddings.errors import (
        EmbeddingPermanentError,
        EmbeddingTransientError,
    )
    from mip_workers.es_adapter import (
        PermanentElasticsearchError,
        RetryableElasticsearchError,
    )

    logger = logging.getLogger(__name__)

    es_adapter = ctx.get("es_adapter")
    if not es_adapter:
        logger.error("No es_adapter configured for embed_message_job.")
        return False

    provider = ctx.get("embedding_provider")
    if not provider:
        logger.error("No embedding_provider configured for embed_message_job.")
        return False

    try:
        result = await provider.embed([semantic_text])
        if not result.vectors:
            return False

        vector = result.vectors[0]
        index_name = f"mail_messages_{tenant_id}"

        success = await es_adapter.update_embeddings(
            index_name=index_name,
            document_id=document_id,
            semantic_vector=vector,
            embedding_model_id=provider.model_id,
            version=version,
        )
        return bool(success)

    except EmbeddingTransientError as exc:
        logger.warning("Transient embedding error for %s: %s", document_id, exc)
        raise  # ARQ will retry

    except EmbeddingPermanentError as exc:
        logger.error("Permanent embedding error for %s: %s", document_id, exc)
        return False  # dead-letter

    except RetryableElasticsearchError as exc:
        logger.warning("Transient ES error for %s: %s", document_id, exc)
        raise  # ARQ will retry

    except PermanentElasticsearchError as exc:
        logger.error("Permanent ES error for %s: %s", document_id, exc)
        return False  # dead-letter

    except Exception as exc:
        logger.error("Unexpected error embedding %s: %s", document_id, exc)
        raise


async def outbox_polling_cron(ctx: dict[str, Any]) -> int:
    """Cron task polling for eligible outbox events and enqueuing jobs."""
    sessionmaker: async_sessionmaker[AsyncSession] | None = ctx.get("sessionmaker")
    redis = ctx.get("redis")

    if not sessionmaker:
        return 0

    async with sessionmaker() as session:
        event_ids = await discover_outbox_events(session, limit=50)

    if not event_ids:
        return 0

    enqueued = 0
    for eid in event_ids:
        if redis:
            await redis.enqueue_job("process_outbox_event_job", str(eid))
            enqueued += 1
        else:
            await process_outbox_event_job(ctx, str(eid))
            enqueued += 1

    return enqueued


class WorkerSettings:
    """ARQ Worker configuration settings."""

    functions: ClassVar = [
        process_outbox_event_job,
        outbox_polling_cron,
        embed_message_job,
        backfill_tenant_embeddings_job,
    ]
    cron_jobs: ClassVar = [cron(outbox_polling_cron, second={0, 10, 20, 30, 40, 50})]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
    )
