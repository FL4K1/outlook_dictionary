"""Test suite for PR-2.5 ARQ Worker Runtime (Scenarios A-O).

Tests against real PostgreSQL database migrated via Alembic.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config
from mip_workers.es_adapter import IndexResult, RetryableElasticsearchError
from mip_workers.outbox_worker import OutboxWorker
from mip_workers.worker import (
    WorkerSettings,
    discover_outbox_events,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from mip_models import (
    MailAccount,
    MailFolder,
    MailMessage,
    MailMessageFolder,
    Organization,
    OutboxEvent,
    OutboxEventStatus,
    Tenant,
    User,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

POSTGRES_TEST_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://mip:mip_dev_password@localhost:5433/mail_intelligence",
)


def _sync_apply_alembic_migrations(db_url: str) -> None:
    alembic_cfg = Config("apps/api/alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_cfg.set_main_option("script_location", "apps/api/alembic")
    command.upgrade(alembic_cfg, "head")


async def apply_alembic_migrations(db_url: str) -> None:
    await asyncio.to_thread(_sync_apply_alembic_migrations, db_url)


@pytest.fixture
async def pg_engine():
    engine = create_async_engine(POSTGRES_TEST_URL, poolclass=NullPool, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE;"))
            await conn.execute(text("CREATE SCHEMA public;"))
        await engine.dispose()
    except (OSError, Exception) as err:
        await engine.dispose()
        pytest.skip(f"PostgreSQL database not available at {POSTGRES_TEST_URL}: {err}")

    await apply_alembic_migrations(POSTGRES_TEST_URL)

    engine = create_async_engine(POSTGRES_TEST_URL, poolclass=NullPool, echo=False)
    yield engine
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE;"))
        await conn.execute(text("CREATE SCHEMA public;"))
    await engine.dispose()


@pytest.fixture
async def session_maker(pg_engine):
    return async_sessionmaker(bind=pg_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def async_session(session_maker) -> AsyncGenerator[AsyncSession, None]:
    async with session_maker() as session:
        yield session
        await session.rollback()
        await session.close()


@pytest.fixture
async def setup_base_entities(
    async_session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage]:
    """Create Org, Tenant, User, Account, Folder, Message in PostgreSQL."""
    now = datetime.now(UTC)
    org = Organization(name="Worker Org", slug=f"org-{uuid.uuid4().hex[:8]}")
    async_session.add(org)
    await async_session.flush()

    tenant = Tenant(organization_id=org.id, name="Worker Tenant", slug=f"t-{uuid.uuid4().hex[:8]}")
    async_session.add(tenant)
    await async_session.flush()

    user = User(email=f"worker-{uuid.uuid4().hex[:8]}@example.com", display_name="Worker User")
    async_session.add(user)
    await async_session.flush()

    account = MailAccount(
        tenant_id=tenant.id,
        provider_type="microsoft_graph",
        email_address=f"worker-{uuid.uuid4().hex[:6]}@example.com",
        account_type="user",
        status="active",
        connected_by=user.id,
        connected_at=now,
    )
    async_session.add(account)
    await async_session.flush()

    folder = MailFolder(
        tenant_id=tenant.id,
        mail_account_id=account.id,
        provider_folder_id="inbox_folder_worker",
        name="Inbox",
    )
    async_session.add(folder)
    await async_session.commit()

    message = MailMessage(
        tenant_id=tenant.id,
        mail_account_id=account.id,
        provider_message_id=f"msg_{uuid.uuid4().hex[:8]}",
        subject="Worker Test Message",
        body_preview="Preview",
        body={"contentType": "text", "content": "Full Body"},
        received_date_time=now,
        is_read=False,
    )

    async_session.add(message)
    await async_session.flush()

    assoc = MailMessageFolder(
        tenant_id=tenant.id,
        mail_account_id=account.id,
        mail_message_id=message.id,
        mail_folder_id=folder.id,
        resync_generation=0,
    )

    async_session.add(assoc)
    await async_session.commit()

    return (org.id, tenant.id, user.id, account, folder, message)


# A. Worker entrypoint imports successfully
def test_a_worker_entrypoint_imports_successfully() -> None:
    import mip_workers.__main__ as main_mod
    import mip_workers.worker as worker_mod

    assert hasattr(worker_mod, "WorkerSettings")
    assert hasattr(main_mod, "main")


# B. WorkerSettings construction
def test_b_worker_settings_construction() -> None:
    settings = WorkerSettings
    assert hasattr(settings, "functions")
    assert hasattr(settings, "cron_jobs")
    assert hasattr(settings, "redis_settings")


# C. Redis configuration loads
def test_c_redis_configuration_loads() -> None:
    redis = WorkerSettings.redis_settings
    assert redis.host is not None
    assert redis.port > 0


# D. Eligible outbox event discovered
@pytest.mark.asyncio
async def test_d_eligible_outbox_event_discovered(
    async_session: AsyncSession,
    setup_base_entities: tuple[
        uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage
    ],
) -> None:
    _, tenant_id, _, _, _, message = setup_base_entities
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type="MAIL_MESSAGE_MUTATED",
        aggregate_id=message.id,
        aggregate_version=1,
        idempotency_key=f"idemp-{uuid.uuid4().hex}",
        status=OutboxEventStatus.PENDING,
        payload={"message_id": str(message.id)},
    )
    async_session.add(event)
    await async_session.commit()

    events = await discover_outbox_events(async_session)
    assert event.id in events


# E. DONE event skipped
@pytest.mark.asyncio
async def test_e_done_event_skipped(
    async_session: AsyncSession,
    setup_base_entities: tuple[
        uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage
    ],
) -> None:
    _, tenant_id, _, _, _, message = setup_base_entities
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type="MAIL_MESSAGE_MUTATED",
        aggregate_id=message.id,
        aggregate_version=1,
        idempotency_key=f"idemp-{uuid.uuid4().hex}",
        status=OutboxEventStatus.DONE,
        payload={},
    )
    async_session.add(event)
    await async_session.commit()

    events = await discover_outbox_events(async_session)
    assert event.id not in events


# F. DEAD_LETTER event skipped
@pytest.mark.asyncio
async def test_f_dead_letter_event_skipped(
    async_session: AsyncSession,
    setup_base_entities: tuple[
        uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage
    ],
) -> None:
    _, tenant_id, _, _, _, message = setup_base_entities
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type="MAIL_MESSAGE_MUTATED",
        aggregate_id=message.id,
        aggregate_version=1,
        idempotency_key=f"idemp-{uuid.uuid4().hex}",
        status=OutboxEventStatus.DEAD_LETTER,
        payload={},
    )
    async_session.add(event)
    await async_session.commit()

    events = await discover_outbox_events(async_session)
    assert event.id not in events


# G. Future next_attempt_at skipped
@pytest.mark.asyncio
async def test_g_future_next_attempt_at_skipped(
    async_session: AsyncSession,
    setup_base_entities: tuple[
        uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage
    ],
) -> None:
    _, tenant_id, _, _, _, message = setup_base_entities
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type="MAIL_MESSAGE_MUTATED",
        aggregate_id=message.id,
        aggregate_version=1,
        idempotency_key=f"idemp-{uuid.uuid4().hex}",
        status=OutboxEventStatus.PENDING,
        next_attempt_at=datetime.now(UTC) + timedelta(hours=1),
        payload={},
    )
    async_session.add(event)
    await async_session.commit()

    events = await discover_outbox_events(async_session)
    assert event.id not in events


# H. Event acquired with lease fencing
@pytest.mark.asyncio
async def test_h_event_acquired_with_lease_fencing(
    async_session: AsyncSession,
    setup_base_entities: tuple[
        uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage
    ],
) -> None:
    _, tenant_id, _, _, _, message = setup_base_entities
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type="MAIL_MESSAGE_MUTATED",
        aggregate_id=message.id,
        aggregate_version=1,
        idempotency_key=f"idemp-{uuid.uuid4().hex}",
        status=OutboxEventStatus.PENDING,
        payload={},
    )
    async_session.add(event)
    await async_session.commit()

    mock_es = AsyncMock()
    mock_es.index_message.return_value = IndexResult(
        success=True, is_conflict=False, status_code=200, document_id=str(message.id), version=1
    )

    worker_id = uuid.uuid4()
    worker = OutboxWorker(async_session, mock_es)
    res = await worker.process_outbox_event(event.id, worker_id)

    assert res is True
    reloaded = await async_session.get(OutboxEvent, event.id)
    assert reloaded is not None
    assert reloaded.status == OutboxEventStatus.DONE


# I. Process event indexes to ES and marks DONE
@pytest.mark.asyncio
async def test_i_process_event_indexes_to_es_and_marks_done(
    async_session: AsyncSession,
    setup_base_entities: tuple[
        uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage
    ],
) -> None:
    _, tenant_id, _, _, _, message = setup_base_entities
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type="MAIL_MESSAGE_MUTATED",
        aggregate_id=message.id,
        aggregate_version=1,
        idempotency_key=f"idemp-{uuid.uuid4().hex}",
        status=OutboxEventStatus.PENDING,
        payload={},
    )
    async_session.add(event)
    await async_session.commit()

    mock_es = AsyncMock()
    mock_es.index_message.return_value = IndexResult(
        success=True, is_conflict=False, status_code=200, document_id=str(message.id), version=1
    )

    worker = OutboxWorker(async_session, mock_es)
    res = await worker.process_outbox_event(event.id, uuid.uuid4())

    assert res is True
    mock_es.index_message.assert_awaited_once()

    reloaded = await async_session.get(OutboxEvent, event.id)
    assert reloaded is not None
    assert reloaded.status == OutboxEventStatus.DONE


# J. Crash before DONE allows later reclaim
@pytest.mark.asyncio
async def test_j_crash_before_done_allows_later_reclaim(
    async_session: AsyncSession,
    setup_base_entities: tuple[
        uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage
    ],
) -> None:
    _, tenant_id, _, _, _, message = setup_base_entities
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type="MAIL_MESSAGE_MUTATED",
        aggregate_id=message.id,
        aggregate_version=1,
        idempotency_key=f"idemp-{uuid.uuid4().hex}",
        status=OutboxEventStatus.IN_FLIGHT,
        locked_by=uuid.uuid4(),
        locked_until=datetime.now(UTC) - timedelta(seconds=10),
        payload={},
    )
    async_session.add(event)
    await async_session.commit()

    mock_es = AsyncMock()
    mock_es.index_message.return_value = IndexResult(
        success=True, is_conflict=False, status_code=200, document_id=str(message.id), version=1
    )

    reclaimer_id = uuid.uuid4()
    worker2 = OutboxWorker(async_session, mock_es)
    res = await worker2.process_outbox_event(event.id, reclaimer_id)

    assert res is True
    reloaded = await async_session.get(OutboxEvent, event.id)
    assert reloaded is not None
    assert reloaded.status == OutboxEventStatus.DONE


# K. Stale worker cannot finalize
@pytest.mark.asyncio
async def test_k_stale_worker_cannot_finalize(
    async_session: AsyncSession,
    setup_base_entities: tuple[
        uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage
    ],
) -> None:
    _, tenant_id, _, _, _, message = setup_base_entities
    worker1_id = uuid.uuid4()
    worker2_id = uuid.uuid4()
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type="MAIL_MESSAGE_MUTATED",
        aggregate_id=message.id,
        aggregate_version=1,
        idempotency_key=f"idemp-{uuid.uuid4().hex}",
        status=OutboxEventStatus.IN_FLIGHT,
        locked_by=worker2_id,
        lease_version=2,
        locked_until=datetime.now(UTC) + timedelta(minutes=5),
        payload={},
    )
    async_session.add(event)
    await async_session.commit()

    mock_es = AsyncMock()
    stale_worker = OutboxWorker(async_session, mock_es)

    # Worker 1 attempts to finalize with lease_version=1 (stale)
    ok = await stale_worker.outbox_repo.update_status_cas(
        event_id=event.id,
        worker_id=worker1_id,
        lease_version=1,
        new_status=OutboxEventStatus.DONE,
    )
    assert ok is False


# L. Separate DB sessions across events
@pytest.mark.asyncio
async def test_l_separate_db_sessions_across_events(
    session_maker: async_sessionmaker,
    setup_base_entities: tuple[
        uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage
    ],
) -> None:
    async with session_maker() as s1:
        repo1 = OutboxWorker(s1, AsyncMock())
        assert repo1.session is s1

    async with session_maker() as s2:
        repo2 = OutboxWorker(s2, AsyncMock())
        assert repo2.session is s2
        assert repo2.session is not s1


# M. DB transaction released before ES HTTP
@pytest.mark.asyncio
async def test_m_db_transaction_released_before_es_http(
    async_session: AsyncSession,
    setup_base_entities: tuple[
        uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage
    ],
) -> None:
    _, tenant_id, _, _, _, message = setup_base_entities
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type="MAIL_MESSAGE_MUTATED",
        aggregate_id=message.id,
        aggregate_version=1,
        idempotency_key=f"idemp-{uuid.uuid4().hex}",
        status=OutboxEventStatus.PENDING,
        payload={},
    )
    async_session.add(event)
    await async_session.commit()

    es_called_with_active_transaction = False

    async def mock_index(*args: Any, **kwargs: Any) -> IndexResult:
        nonlocal es_called_with_active_transaction
        es_called_with_active_transaction = async_session.in_transaction()
        return IndexResult(
            success=True, is_conflict=False, status_code=200, document_id=str(message.id), version=1
        )

    mock_es = AsyncMock()
    mock_es.index_message.side_effect = mock_index

    worker = OutboxWorker(async_session, mock_es)
    await worker.process_outbox_event(event.id, uuid.uuid4())

    assert es_called_with_active_transaction is False


# N. Worker can process multiple sequential events
@pytest.mark.asyncio
async def test_n_worker_can_process_multiple_sequential_events(
    async_session: AsyncSession,
    setup_base_entities: tuple[
        uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage
    ],
) -> None:
    _, tenant_id, _, _, _, message = setup_base_entities
    e1 = OutboxEvent(
        tenant_id=tenant_id,
        event_type="MAIL_MESSAGE_MUTATED",
        aggregate_id=message.id,
        aggregate_version=1,
        idempotency_key=f"idemp-{uuid.uuid4().hex}",
        status=OutboxEventStatus.PENDING,
        payload={},
    )
    e2 = OutboxEvent(
        tenant_id=tenant_id,
        event_type="MAIL_MESSAGE_MUTATED",
        aggregate_id=message.id,
        aggregate_version=2,
        idempotency_key=f"idemp-{uuid.uuid4().hex}",
        status=OutboxEventStatus.PENDING,
        payload={},
    )
    async_session.add_all([e1, e2])
    await async_session.commit()

    mock_es = AsyncMock()
    mock_es.index_message.return_value = IndexResult(
        success=True, is_conflict=False, status_code=200, document_id=str(message.id), version=1
    )

    worker = OutboxWorker(async_session, mock_es)
    res1 = await worker.process_outbox_event(e1.id, uuid.uuid4())
    res2 = await worker.process_outbox_event(e2.id, uuid.uuid4())

    assert res1 is True
    assert res2 is True
    assert mock_es.index_message.call_count == 2


# O. Graceful failure behavior
@pytest.mark.asyncio
async def test_o_graceful_failure_behavior(
    async_session: AsyncSession,
    setup_base_entities: tuple[
        uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, MailFolder, MailMessage
    ],
) -> None:
    _, tenant_id, _, _, _, message = setup_base_entities
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type="MAIL_MESSAGE_MUTATED",
        aggregate_id=message.id,
        aggregate_version=1,
        idempotency_key=f"idemp-{uuid.uuid4().hex}",
        status=OutboxEventStatus.PENDING,
        attempt_count=0,
        payload={},
    )
    async_session.add(event)
    await async_session.commit()

    mock_es = AsyncMock()
    mock_es.index_message.side_effect = RetryableElasticsearchError(
        "Elasticsearch cluster unavailable"
    )

    worker = OutboxWorker(async_session, mock_es)
    res = await worker.process_outbox_event(event.id, uuid.uuid4())

    assert res is False
    reloaded = await async_session.get(OutboxEvent, event.id)
    assert reloaded is not None
    assert reloaded.attempt_count == 1
    assert "Elasticsearch cluster unavailable" in (reloaded.last_error or "")
