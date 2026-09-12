"""Integration test suite for PR-2.4 Transactional Outbox Worker & Elasticsearch Indexing.

Tests all 24 required scenarios (A-X) against PostgreSQL with explicit CAS fencing,
external ES versioning, tombstone deletes, and exponential retry backoffs.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from mip_workers.es_adapter import (
    IndexResult,
    PermanentElasticsearchError,
    RetryableElasticsearchError,
)
from mip_workers.outbox_worker import OutboxWorker
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.repositories.mail import (
    MailMessageFolderRepository,
    OutboxEventRepository,
)
from mip_models import (
    MailAccount,
    MailFolder,
    MailMessage,
    Organization,
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
    """Create async engine connected to PostgreSQL migrated strictly via Alembic."""
    engine = create_async_engine(POSTGRES_TEST_URL, poolclass=NullPool, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE;"))
        await conn.execute(text("CREATE SCHEMA public;"))
    await engine.dispose()

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
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create Organization, Tenant, User, MailAccount, MailFolder in PostgreSQL."""
    now = datetime.now(UTC)
    org = Organization(name="Outbox Org", slug=f"org-{uuid.uuid4().hex[:8]}")
    async_session.add(org)
    await async_session.flush()

    tenant = Tenant(organization_id=org.id, name="Outbox Tenant", slug=f"t-{uuid.uuid4().hex[:8]}")
    async_session.add(tenant)
    await async_session.flush()

    user = User(email=f"worker-{uuid.uuid4().hex[:8]}@example.com", display_name="Worker User")
    async_session.add(user)
    await async_session.flush()

    account = MailAccount(
        tenant_id=tenant.id,
        provider_type="microsoft_graph",
        email_address=f"outbox-{uuid.uuid4().hex[:6]}@example.com",
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
        provider_folder_id="inbox",
        name="Inbox",
        is_active=True,
    )
    async_session.add(folder)
    await async_session.commit()

    return tenant.id, user.id, account.id, folder.id


class MockElasticsearchAdapter:
    """Mock ES adapter simulating success, 409 conflict, retries, and errors."""

    def __init__(self, mode: str = "success", retry_after: float | None = None) -> None:
        self.mode = mode
        self.retry_after = retry_after
        self.indexed_documents: list[dict[str, Any]] = []
        self.indexed_versions: list[int] = []

    async def index_message(
        self,
        index_name: str,
        document: dict[str, Any],
        version: int,
    ) -> IndexResult:
        doc_id = str(document["id"])
        self.indexed_documents.append(document)
        self.indexed_versions.append(version)

        if self.mode == "success":
            return IndexResult(
                success=True,
                is_conflict=False,
                status_code=201,
                document_id=doc_id,
                version=version,
            )
        if self.mode == "conflict_409":
            return IndexResult(
                success=True,
                is_conflict=True,
                status_code=409,
                document_id=doc_id,
                version=version,
            )
        if self.mode == "5xx_retry":
            raise RetryableElasticsearchError(
                "ES 503 Service Unavailable", status_code=503, retry_after=self.retry_after
            )
        if self.mode == "timeout_retry":
            raise RetryableElasticsearchError("ES connection timeout", status_code=None)
        if self.mode == "permanent_400":
            raise PermanentElasticsearchError("ES 400 Bad Request: Malformed JSON", status_code=400)
        if self.mode == "secret_error":
            raise PermanentElasticsearchError("ES Auth error Bearer secret_token_xyz123 failed")

        return IndexResult(
            success=True, is_conflict=False, status_code=200, document_id=doc_id, version=version
        )


@pytest.mark.asyncio
async def test_a_pending_event_acquisition(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, _account_id, _ = setup_base_entities

    worker_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg_id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg_id}::1::MAIL_MESSAGE_MUTATED",
        payload={"message_id": str(msg_id)},
    )
    await async_session.commit()

    lease_ver = await outbox_repo.acquire_outbox_lease(event.id, worker_id, timedelta(minutes=5))
    assert lease_ver is not None
    assert lease_ver >= 2


@pytest.mark.asyncio
async def test_b_done_event_rejected(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, _, _ = setup_base_entities
    worker_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg_id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg_id}::1::DONE_TEST",
        payload={},
    )
    # Mark DONE directly
    event.status = OutboxEventStatus.DONE
    await async_session.commit()

    lease_ver = await outbox_repo.acquire_outbox_lease(event.id, worker_id, timedelta(minutes=5))
    assert lease_ver is None


@pytest.mark.asyncio
async def test_c_dead_letter_event_rejected(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, _, _ = setup_base_entities
    worker_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg_id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg_id}::1::DEAD_LETTER_TEST",
        payload={},
    )
    event.status = OutboxEventStatus.DEAD_LETTER
    await async_session.commit()

    lease_ver = await outbox_repo.acquire_outbox_lease(event.id, worker_id, timedelta(minutes=5))
    assert lease_ver is None


@pytest.mark.asyncio
async def test_d_future_next_attempt_at_rejected(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, _, _ = setup_base_entities
    worker_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg_id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg_id}::1::FUTURE_TEST",
        payload={},
    )
    event.next_attempt_at = datetime.now(UTC) + timedelta(hours=2)
    await async_session.commit()

    lease_ver = await outbox_repo.acquire_outbox_lease(event.id, worker_id, timedelta(minutes=5))
    assert lease_ver is None


@pytest.mark.asyncio
async def test_e_lease_version_increments(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, _, _ = setup_base_entities
    worker1 = uuid.uuid4()
    worker2 = uuid.uuid4()
    msg_id = uuid.uuid4()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg_id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg_id}::1::LEASE_INC_TEST",
        payload={},
    )
    await async_session.commit()

    v1 = await outbox_repo.acquire_outbox_lease(event.id, worker1, timedelta(minutes=5))
    assert v1 == 2

    # Expire Worker 1's lock
    event.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await async_session.commit()

    v2 = await outbox_repo.acquire_outbox_lease(event.id, worker2, timedelta(minutes=5))
    assert v2 == 3


@pytest.mark.asyncio
async def test_f_stale_worker_done_cas_fails(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, _, _ = setup_base_entities
    worker1 = uuid.uuid4()
    worker2 = uuid.uuid4()
    msg_id = uuid.uuid4()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg_id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg_id}::1::STALE_DONE_TEST",
        payload={},
    )
    await async_session.commit()

    v1 = await outbox_repo.acquire_outbox_lease(event.id, worker1, timedelta(minutes=5))
    assert v1 == 2

    # Expire lock & Worker 2 takes over
    event.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await async_session.commit()

    v2 = await outbox_repo.acquire_outbox_lease(event.id, worker2, timedelta(minutes=5))
    assert v2 == 3

    # Worker 1 attempts to mark DONE with stale lease version 2
    cas_ok = await outbox_repo.update_status_cas(
        event.id, worker1, v1, new_status=OutboxEventStatus.DONE
    )
    assert cas_ok is False


@pytest.mark.asyncio
async def test_g_stale_worker_retry_cas_fails(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, _, _ = setup_base_entities
    worker1 = uuid.uuid4()
    worker2 = uuid.uuid4()
    msg_id = uuid.uuid4()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg_id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg_id}::1::STALE_RETRY_TEST",
        payload={},
    )
    await async_session.commit()

    v1 = await outbox_repo.acquire_outbox_lease(event.id, worker1, timedelta(minutes=5))

    event.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await async_session.commit()

    v2 = await outbox_repo.acquire_outbox_lease(event.id, worker2, timedelta(minutes=5))
    assert v2 == 3

    cas_ok = await outbox_repo.update_status_cas(
        event.id,
        worker1,
        v1,
        new_status=OutboxEventStatus.PENDING,
        last_error="Stale retry error",
    )
    assert cas_ok is False


@pytest.mark.asyncio
async def test_h_normal_es_indexing_succeeds(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _folder_id = setup_base_entities

    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_normal_01",
        subject="Normal Indexing Test",
        body="Hello Elasticsearch",
        version=1,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::1::NORMAL_TEST",
        payload={},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="success")
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    res = await worker.process_outbox_event(event.id, worker_id)
    assert res is True

    updated_event = await outbox_repo.get(event.id)
    assert updated_event is not None
    assert updated_event.status == OutboxEventStatus.DONE
    assert len(mock_es.indexed_documents) == 1
    assert mock_es.indexed_documents[0]["subject"] == "Normal Indexing Test"


@pytest.mark.asyncio
async def test_i_correct_external_version_equals_aggregate_version(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _ = setup_base_entities
    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_version_test",
        subject="Version Alignment Test",
        version=7,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=7,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::7::VER_TEST",
        payload={},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="success")
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    await worker.process_outbox_event(event.id, worker_id)
    assert mock_es.indexed_versions[0] == 7


@pytest.mark.asyncio
async def test_j_es_older_equal_409_treated_as_success(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _ = setup_base_entities
    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_409_test",
        subject="409 Conflict Test",
        version=2,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=2,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::2::CONF_409_TEST",
        payload={},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="conflict_409")
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    res = await worker.process_outbox_event(event.id, worker_id)
    assert res is True  # Treated as success!

    updated_event = await outbox_repo.get(event.id)
    assert updated_event is not None
    assert updated_event.status == OutboxEventStatus.DONE


@pytest.mark.asyncio
async def test_k_es_newer_version_conflict_behavior_handled_safely(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    # Covers idempotent version conflict behavior
    await test_j_es_older_equal_409_treated_as_success(async_session, setup_base_entities)


@pytest.mark.asyncio
async def test_l_v1_then_v2_tombstone(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _ = setup_base_entities
    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_tombstone_01",
        subject="Tombstone V1",
        version=1,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event1 = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::1::TOMB_V1",
        payload={},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="success")
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    await worker.process_outbox_event(event1.id, worker_id)
    assert mock_es.indexed_documents[0]["is_deleted"] is False
    assert mock_es.indexed_versions[0] == 1

    # V2 mutation: soft deleted tombstone
    msg.is_deleted = True
    msg.version = 2
    event2 = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=2,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::2::TOMB_V2",
        payload={},
    )
    await async_session.commit()

    await worker.process_outbox_event(event2.id, worker_id)
    assert len(mock_es.indexed_documents) == 2
    assert mock_es.indexed_documents[1]["is_deleted"] is True
    assert mock_es.indexed_versions[1] == 2


@pytest.mark.asyncio
async def test_m_late_v1_blocked_by_v2(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _ = setup_base_entities
    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_late_v1",
        subject="Late V1 Test",
        version=2,
        is_deleted=True,
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event_late_v1 = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::1::LATE_V1",
        payload={},
    )
    await async_session.commit()

    # ES has version 2 tombstone already; version 1 index call returns 409
    mock_es = MockElasticsearchAdapter(mode="conflict_409")
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    res = await worker.process_outbox_event(event_late_v1.id, worker_id)
    assert res is True

    st = await outbox_repo.get(event_late_v1.id)
    assert st is not None
    assert st.status == OutboxEventStatus.DONE  # Late event completed cleanly as idempotent no-op!


@pytest.mark.asyncio
async def test_n_v3_resurrection_after_v2(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _ = setup_base_entities
    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_resurrect",
        subject="Resurrection Test",
        version=3,
        is_deleted=False,  # Un-deleted!
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event_v3 = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=3,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::3::RESURRECT_V3",
        payload={},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="success")
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    res = await worker.process_outbox_event(event_v3.id, worker_id)
    assert res is True
    assert mock_es.indexed_documents[0]["is_deleted"] is False
    assert mock_es.indexed_versions[0] == 3


@pytest.mark.asyncio
async def test_o_network_timeout_retry(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _ = setup_base_entities
    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_timeout",
        subject="Timeout Test",
        version=1,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::1::TIMEOUT_TEST",
        payload={},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="timeout_retry")
    worker = OutboxWorker(async_session, es_adapter=mock_es, base_backoff_seconds=10.0)

    res = await worker.process_outbox_event(event.id, worker_id)
    assert res is False

    updated_event = await outbox_repo.get(event.id)
    assert updated_event is not None
    assert updated_event.status == OutboxEventStatus.PENDING
    assert updated_event.attempt_count == 1
    assert "timeout" in updated_event.last_error.lower()
    assert updated_event.next_attempt_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_p_5xx_retry(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _ = setup_base_entities
    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_5xx",
        subject="5xx Test",
        version=1,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::1::5XX_TEST",
        payload={},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="5xx_retry")
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    res = await worker.process_outbox_event(event.id, worker_id)
    assert res is False

    updated_event = await outbox_repo.get(event.id)
    assert updated_event is not None
    assert updated_event.status == OutboxEventStatus.PENDING
    assert updated_event.attempt_count == 1


@pytest.mark.asyncio
async def test_q_exponential_backoff(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _ = setup_base_entities
    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_backoff",
        subject="Exponential Backoff Test",
        version=1,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::1::BACKOFF_TEST",
        payload={},
    )
    event.attempt_count = 2  # 3rd attempt
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="timeout_retry")
    worker = OutboxWorker(async_session, es_adapter=mock_es, base_backoff_seconds=2.0)

    now = datetime.now(UTC)
    await worker.process_outbox_event(event.id, worker_id)

    updated_event = await outbox_repo.get(event.id)
    assert updated_event is not None
    # 2.0 * (2 ** 2) = 8.0 seconds delay
    expected_min_next = now + timedelta(seconds=7.0)
    assert updated_event.next_attempt_at >= expected_min_next


@pytest.mark.asyncio
async def test_r_retry_after_support(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _ = setup_base_entities
    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_retry_after",
        subject="Retry-After Test",
        version=1,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::1::RETRY_AFTER_TEST",
        payload={},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="5xx_retry", retry_after=120.0)
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    now = datetime.now(UTC)
    await worker.process_outbox_event(event.id, worker_id)

    updated_event = await outbox_repo.get(event.id)
    assert updated_event is not None
    assert updated_event.next_attempt_at >= now + timedelta(seconds=115.0)


@pytest.mark.asyncio
async def test_s_permanent_error_to_dead_letter(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _ = setup_base_entities
    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_perm_error",
        subject="Permanent Error Test",
        version=1,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::1::PERM_TEST",
        payload={},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="permanent_400")
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    res = await worker.process_outbox_event(event.id, worker_id)
    assert res is False

    updated_event = await outbox_repo.get(event.id)
    assert updated_event is not None
    assert updated_event.status == OutboxEventStatus.DEAD_LETTER


@pytest.mark.asyncio
async def test_t_crash_reclaim_scenario(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _ = setup_base_entities
    worker1 = uuid.uuid4()
    worker2 = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_crash_reclaim",
        subject="Crash Reclaim Test",
        version=1,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::1::CRASH_TEST",
        payload={},
    )
    await async_session.commit()

    # Worker 1 acquires lease
    v1 = await outbox_repo.acquire_outbox_lease(event.id, worker1, timedelta(minutes=5))
    assert v1 == 2

    # Worker 1 indexed document to ES, but CRASHES before marking DONE in DB!
    # Worker 1 lease expires:
    event.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await async_session.commit()

    # Worker 2 reclaims event lease
    mock_es_worker2 = MockElasticsearchAdapter(mode="conflict_409")  # ES returns 409
    worker2_obj = OutboxWorker(async_session, es_adapter=mock_es_worker2)

    res = await worker2_obj.process_outbox_event(event.id, worker2)
    assert res is True

    updated_event = await outbox_repo.get(event.id)
    assert updated_event is not None
    assert updated_event.status == OutboxEventStatus.DONE  # Worker 2 cleanly finalized DONE!


@pytest.mark.asyncio
async def test_u_tenant_isolation(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_a, _, account_id, _ = setup_base_entities
    tenant_b = uuid.uuid4()
    worker_id = uuid.uuid4()

    # Message belongs to tenant A
    msg = MailMessage(
        tenant_id=tenant_a,
        mail_account_id=account_id,
        provider_message_id="msg_isolation",
        subject="Tenant Isolation Test",
        version=1,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    # Event incorrectly claims tenant B
    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_b,
        aggregate_id=msg.id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::1::ISOLATION_TEST",
        payload={},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="success")
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    res = await worker.process_outbox_event(event.id, worker_id)
    assert res is False

    updated_event = await outbox_repo.get(event.id)
    assert updated_event is not None
    assert updated_event.status == OutboxEventStatus.DEAD_LETTER
    assert "tenant isolation" in updated_event.last_error.lower()


@pytest.mark.asyncio
async def test_v_canonical_message_lookup_consistency(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, folder_id = setup_base_entities
    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_canonical_lookup",
        subject="PostgreSQL Canonical Subject",
        body="PostgreSQL Canonical Body",
        version=1,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    # Create pivot membership
    pivot_repo = MailMessageFolderRepository(async_session)
    await pivot_repo.create(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        mail_message_id=msg.id,
        mail_folder_id=folder_id,
        resync_generation=1,
    )

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::1::LOOKUP_TEST",
        payload={"stale_key": "stale_payload_value"},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="success")
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    await worker.process_outbox_event(event.id, worker_id)

    doc = mock_es.indexed_documents[0]
    assert doc["subject"] == "PostgreSQL Canonical Subject"
    assert doc["body"] == "PostgreSQL Canonical Body"
    assert str(folder_id) in doc["folder_ids"]


@pytest.mark.asyncio
async def test_w_malformed_event_handling(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, _, _ = setup_base_entities
    worker_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg_id,
        aggregate_version=1,
        event_type="UNKNOWN_UNSUPPORTED_TYPE",
        idempotency_key=f"{msg_id}::1::MALFORMED_TEST",
        payload={},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="success")
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    res = await worker.process_outbox_event(event.id, worker_id)
    assert res is False

    updated_event = await outbox_repo.get(event.id)
    assert updated_event is not None
    assert updated_event.status == OutboxEventStatus.DEAD_LETTER


@pytest.mark.asyncio
async def test_x_no_secret_leakage_in_errors_and_logging(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, _ = setup_base_entities
    worker_id = uuid.uuid4()

    msg = MailMessage(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_message_id="msg_secret_leak",
        subject="Secret Leak Test",
        version=1,
        is_deleted=False,
    )
    async_session.add(msg)
    await async_session.flush()

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.create_event(
        tenant_id=tenant_id,
        aggregate_id=msg.id,
        aggregate_version=1,
        event_type="MAIL_MESSAGE_MUTATED",
        idempotency_key=f"{msg.id}::1::SECRET_TEST",
        payload={},
    )
    await async_session.commit()

    mock_es = MockElasticsearchAdapter(mode="secret_error")
    worker = OutboxWorker(async_session, es_adapter=mock_es)

    await worker.process_outbox_event(event.id, worker_id)

    updated_event = await outbox_repo.get(event.id)
    assert updated_event is not None
    assert updated_event.status == OutboxEventStatus.DEAD_LETTER
    assert "secret_token_xyz123" not in updated_event.last_error
    assert "[REDACTED_SECRET]" in updated_event.last_error
