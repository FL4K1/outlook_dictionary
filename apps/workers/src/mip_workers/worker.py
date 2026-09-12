"""ARQ Worker Runtime & Outbox Event Job Dispatcher.

Implements ARQ WorkerSettings, Redis connection configuration,
outbox discovery polling, and job processing with per-job DB session isolation.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mip_models.mail import OutboxEvent, OutboxEventStatus
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

    ctx["db_engine"] = engine
    ctx["sessionmaker"] = sessionmaker
    ctx["es_adapter"] = es_adapter


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
        worker = OutboxWorker(session, es_adapter=es_adapter)
        return await worker.process_outbox_event(event_id, worker_id)


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

    functions = [process_outbox_event_job, outbox_polling_cron]
    cron_jobs = [cron(outbox_polling_cron, second={0, 10, 20, 30, 40, 50})]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
    )
