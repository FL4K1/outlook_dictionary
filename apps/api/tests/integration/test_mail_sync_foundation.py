"""Integration tests for Mail Synchronization Database Foundation (PR-2.1) on PostgreSQL.

Runs against a live PostgreSQL database (mip-postgres container / port 5433)
to validate database-enforced integrity and concurrency semantics.

Validates 12 specific PostgreSQL database scenarios:
1. Tenant isolation (Message tenant != Folder tenant -> DB IntegrityError)
2. Account isolation (Message account != Folder account -> DB IntegrityError)
3. Folder parent isolation (Folder parent account != Folder account -> DB IntegrityError)
4. Message identity uniqueness per mail account
5. Participant role CHECK constraint ('TO', 'CC', 'BCC')
6. Fenced credential refresh lease CAS and finalization
7. Fenced outbox event lease CAS and state updates
8. Outbox event acquisition eligibility (DONE, DEAD_LETTER, next_attempt_at)
9. Outbox idempotency_key uniqueness
10. MailMessageFolder resync_generation persistence
11. Multi-transaction concurrent refresh lease acquisition contention
12. Multi-transaction concurrent outbox lease acquisition contention
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import exc, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.repositories.mail import MailAccountRepository, OutboxEventRepository
from mip_models import (
    MailAccount,
    MailFolder,
    MailMessage,
    MailMessageFolder,
    MailMessageParticipant,
    Organization,
    OutboxEvent,
    OutboxEventStatus,
    Tenant,
    User,
)

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
    """Provide an async sessionmaker for multi-transaction concurrency tests."""
    return async_sessionmaker(bind=pg_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def async_session(session_maker):
    """Provide an isolated database session for single-transaction test cases."""
    async with session_maker() as session:
        yield session
        await session.rollback()
        await session.close()


@pytest.fixture
async def base_entities(async_session: AsyncSession):
    """Seed base Organization, Tenants, User, and MailAccounts on PostgreSQL."""
    org = Organization(name="Postgres Test Org", slug=f"org-{uuid.uuid4().hex[:8]}")
    async_session.add(org)
    await async_session.flush()

    tenant1 = Tenant(organization_id=org.id, name="Tenant 1", slug=f"t1-{uuid.uuid4().hex[:8]}")
    tenant2 = Tenant(organization_id=org.id, name="Tenant 2", slug=f"t2-{uuid.uuid4().hex[:8]}")
    async_session.add_all([tenant1, tenant2])
    await async_session.flush()

    user = User(email=f"user-{uuid.uuid4().hex[:8]}@example.com", display_name="Test User")
    async_session.add(user)
    await async_session.flush()

    acc1_t1 = MailAccount(
        tenant_id=tenant1.id,
        provider_type="microsoft_graph",
        email_address=f"acc1-{uuid.uuid4().hex[:6]}@t1.com",
        account_type="user",
        status="active",
        connected_by=user.id,
        connected_at=datetime.now(UTC),
    )
    acc2_t1 = MailAccount(
        tenant_id=tenant1.id,
        provider_type="microsoft_graph",
        email_address=f"acc2-{uuid.uuid4().hex[:6]}@t1.com",
        account_type="user",
        status="active",
        connected_by=user.id,
        connected_at=datetime.now(UTC),
    )
    acc1_t2 = MailAccount(
        tenant_id=tenant2.id,
        provider_type="microsoft_graph",
        email_address=f"acc1-{uuid.uuid4().hex[:6]}@t2.com",
        account_type="user",
        status="active",
        connected_by=user.id,
        connected_at=datetime.now(UTC),
    )
    async_session.add_all([acc1_t1, acc2_t1, acc1_t2])
    await async_session.commit()

    return {
        "org": org,
        "tenant1": tenant1,
        "tenant2": tenant2,
        "user": user,
        "acc1_t1": acc1_t1,
        "acc2_t1": acc2_t1,
        "acc1_t2": acc1_t2,
    }


@pytest.mark.asyncio
async def test_tenant_isolation_rejects_cross_tenant_pivot(
    async_session: AsyncSession,
    base_entities: dict,
) -> None:
    """Test 1: Rejects pivot linking message in Tenant 1 to folder in Tenant 2."""
    t1, t2 = base_entities["tenant1"], base_entities["tenant2"]
    acc1, acc2 = base_entities["acc1_t1"], base_entities["acc1_t2"]

    msg = MailMessage(
        tenant_id=t1.id,
        mail_account_id=acc1.id,
        provider_message_id=f"msg-{uuid.uuid4().hex[:8]}",
    )
    folder = MailFolder(
        tenant_id=t2.id,
        mail_account_id=acc2.id,
        provider_folder_id=f"folder-{uuid.uuid4().hex[:8]}",
        name="Inbox",
    )
    async_session.add_all([msg, folder])
    await async_session.flush()

    with pytest.raises((exc.IntegrityError, exc.DBAPIError)):
        async with async_session.begin_nested():
            pivot = MailMessageFolder(
                mail_message_id=msg.id,
                mail_folder_id=folder.id,
                tenant_id=t1.id,
                mail_account_id=acc1.id,
                resync_generation=1,
            )
            async_session.add(pivot)
            await async_session.flush()


@pytest.mark.asyncio
async def test_account_isolation_rejects_cross_account_pivot(
    async_session: AsyncSession,
    base_entities: dict,
) -> None:
    """Test 2: Rejects pivot linking Acc 1 message to Acc 2 folder in same tenant."""
    t1 = base_entities["tenant1"]
    acc1, acc2 = base_entities["acc1_t1"], base_entities["acc2_t1"]

    msg = MailMessage(
        tenant_id=t1.id,
        mail_account_id=acc1.id,
        provider_message_id=f"msg-{uuid.uuid4().hex[:8]}",
    )
    folder = MailFolder(
        tenant_id=t1.id,
        mail_account_id=acc2.id,
        provider_folder_id=f"folder-{uuid.uuid4().hex[:8]}",
        name="Archive",
    )
    async_session.add_all([msg, folder])
    await async_session.flush()

    with pytest.raises((exc.IntegrityError, exc.DBAPIError)):
        async with async_session.begin_nested():
            pivot = MailMessageFolder(
                mail_message_id=msg.id,
                mail_folder_id=folder.id,
                tenant_id=t1.id,
                mail_account_id=acc1.id,
                resync_generation=1,
            )
            async_session.add(pivot)
            await async_session.flush()


@pytest.mark.asyncio
async def test_folder_parent_isolation_rejects_cross_account_parent(
    async_session: AsyncSession,
    base_entities: dict,
) -> None:
    """Test 3: Rejects child folder in Acc 2 referencing parent folder in Acc 1."""
    t1 = base_entities["tenant1"]
    acc1, acc2 = base_entities["acc1_t1"], base_entities["acc2_t1"]

    parent_folder = MailFolder(
        tenant_id=t1.id,
        mail_account_id=acc1.id,
        provider_folder_id=f"folder-p-{uuid.uuid4().hex[:8]}",
        name="Parent Folder",
    )
    async_session.add(parent_folder)
    await async_session.flush()

    with pytest.raises((exc.IntegrityError, exc.DBAPIError)):
        async with async_session.begin_nested():
            child_folder = MailFolder(
                tenant_id=t1.id,
                mail_account_id=acc2.id,
                provider_folder_id=f"folder-c-{uuid.uuid4().hex[:8]}",
                name="Child Folder",
                parent_id=parent_folder.id,
            )
            async_session.add(child_folder)
            await async_session.flush()


@pytest.mark.asyncio
async def test_message_identity_uniqueness(
    async_session: AsyncSession,
    base_entities: dict,
) -> None:
    """Test 4: Same provider_message_id allowed across accounts, rejected within same account."""
    t1 = base_entities["tenant1"]
    acc1, acc2 = base_entities["acc1_t1"], base_entities["acc2_t1"]

    prov_id = f"graph-msg-{uuid.uuid4().hex[:8]}"

    # Allowed across different accounts
    msg1 = MailMessage(
        tenant_id=t1.id,
        mail_account_id=acc1.id,
        provider_message_id=prov_id,
    )
    msg2 = MailMessage(
        tenant_id=t1.id,
        mail_account_id=acc2.id,
        provider_message_id=prov_id,
    )
    async_session.add_all([msg1, msg2])
    await async_session.flush()
    assert msg1.id != msg2.id

    # Duplicate in same account -> rejected
    with pytest.raises((exc.IntegrityError, exc.DBAPIError)):
        async with async_session.begin_nested():
            msg3 = MailMessage(
                tenant_id=t1.id,
                mail_account_id=acc1.id,
                provider_message_id=prov_id,
            )
            async_session.add(msg3)
            await async_session.flush()


@pytest.mark.asyncio
async def test_participant_role_constraint(
    async_session: AsyncSession,
    base_entities: dict,
) -> None:
    """Test 5: PostgreSQL CHECK constraint accepts TO, CC, BCC and rejects invalid role."""
    t1 = base_entities["tenant1"]
    acc1 = base_entities["acc1_t1"]

    msg = MailMessage(
        tenant_id=t1.id,
        mail_account_id=acc1.id,
        provider_message_id=f"msg-{uuid.uuid4().hex[:8]}",
    )
    async_session.add(msg)
    await async_session.flush()

    p_to = MailMessageParticipant(mail_message_id=msg.id, email="a@a.com", role="TO")
    p_cc = MailMessageParticipant(mail_message_id=msg.id, email="b@b.com", role="CC")
    p_bcc = MailMessageParticipant(mail_message_id=msg.id, email="c@c.com", role="BCC")
    async_session.add_all([p_to, p_cc, p_bcc])
    await async_session.flush()

    with pytest.raises((exc.IntegrityError, exc.DBAPIError)):
        async with async_session.begin_nested():
            p_invalid = MailMessageParticipant(
                mail_message_id=msg.id,
                email="d@d.com",
                role="INVALID_ROLE",
            )
            async_session.add(p_invalid)
            await async_session.flush()


@pytest.mark.asyncio
async def test_refresh_lease_fencing(
    async_session: AsyncSession,
    base_entities: dict,
) -> None:
    """Test 6: Fenced credential refresh lease CAS acquisition, expiration, and finalization."""
    repo = MailAccountRepository(async_session)
    acc = base_entities["acc1_t1"]
    worker_a = uuid.uuid4()
    worker_b = uuid.uuid4()

    # 1. Worker A acquires lease
    res_a = await repo.acquire_refresh_lease(acc.id, worker_a, timedelta(minutes=5))
    assert res_a is not None
    lease_ver_a, _gen_a = res_a
    assert lease_ver_a == 2

    # 2. Worker B attempts acquisition while active -> None
    res_b1 = await repo.acquire_refresh_lease(acc.id, worker_b, timedelta(minutes=5))
    assert res_b1 is None

    # 3. Simulate lease expiration
    acc.refresh_locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await async_session.flush()

    # 4. Worker B acquires expired lease
    res_b2 = await repo.acquire_refresh_lease(acc.id, worker_b, timedelta(minutes=5))
    assert res_b2 is not None
    lease_ver_b, gen_b = res_b2
    assert lease_ver_b == 3

    # 5. Stale Worker A finalization -> FAILS
    final_a = await repo.finalize_refresh_lease(acc.id, worker_a, lease_ver_a)
    assert final_a is False

    # 6. Active Worker B finalization -> SUCCEEDS
    final_b = await repo.finalize_refresh_lease(acc.id, worker_b, lease_ver_b)
    assert final_b is True

    await async_session.refresh(acc)
    assert acc.credential_generation == gen_b + 1
    assert acc.refresh_locked_by is None


@pytest.mark.asyncio
async def test_outbox_lease_fencing(
    async_session: AsyncSession,
    base_entities: dict,
) -> None:
    """Test 7: Fenced outbox worker lease acquisition and status updates."""
    repo = OutboxEventRepository(async_session)
    t1 = base_entities["tenant1"]
    worker_a = uuid.uuid4()
    worker_b = uuid.uuid4()

    event = OutboxEvent(
        tenant_id=t1.id,
        aggregate_id=uuid.uuid4(),
        aggregate_version=1,
        event_type="mail.folder_added",
        idempotency_key=f"outbox-{uuid.uuid4().hex[:8]}",
        payload={"folder_name": "Inbox"},
        status=OutboxEventStatus.PENDING,
    )
    async_session.add(event)
    await async_session.flush()

    # 1. Worker A acquires lease
    lease_ver_a = await repo.acquire_outbox_lease(event.id, worker_a, timedelta(minutes=5))
    assert lease_ver_a is not None

    # 2. Worker B attempts acquisition -> None
    lease_ver_b1 = await repo.acquire_outbox_lease(event.id, worker_b, timedelta(minutes=5))
    assert lease_ver_b1 is None

    # 3. Expire lease
    event.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await async_session.flush()

    # 4. Worker B acquires expired lease
    lease_ver_b2 = await repo.acquire_outbox_lease(event.id, worker_b, timedelta(minutes=5))
    assert lease_ver_b2 is not None

    # 5. Stale Worker A update -> FAILS
    stale_update = await repo.update_status_cas(
        event.id, worker_a, lease_ver_a, OutboxEventStatus.DONE
    )
    assert stale_update is False

    # 6. Active Worker B update -> SUCCEEDS
    active_update = await repo.update_status_cas(
        event.id, worker_b, lease_ver_b2, OutboxEventStatus.DONE
    )
    assert active_update is True


@pytest.mark.asyncio
async def test_outbox_eligibility(
    async_session: AsyncSession,
    base_entities: dict,
) -> None:
    """Test 8: DONE, DEAD_LETTER, and future next_attempt_at events are ineligible."""
    repo = OutboxEventRepository(async_session)
    t1 = base_entities["tenant1"]
    worker = uuid.uuid4()
    now = datetime.now(UTC)

    done_event = OutboxEvent(
        tenant_id=t1.id,
        aggregate_id=uuid.uuid4(),
        aggregate_version=1,
        event_type="test.event",
        idempotency_key=f"done-{uuid.uuid4().hex[:8]}",
        payload={},
        status=OutboxEventStatus.DONE,
    )
    dead_event = OutboxEvent(
        tenant_id=t1.id,
        aggregate_id=uuid.uuid4(),
        aggregate_version=1,
        event_type="test.event",
        idempotency_key=f"dead-{uuid.uuid4().hex[:8]}",
        payload={},
        status=OutboxEventStatus.DEAD_LETTER,
    )
    future_event = OutboxEvent(
        tenant_id=t1.id,
        aggregate_id=uuid.uuid4(),
        aggregate_version=1,
        event_type="test.event",
        idempotency_key=f"future-{uuid.uuid4().hex[:8]}",
        payload={},
        status=OutboxEventStatus.PENDING,
        next_attempt_at=now + timedelta(hours=1),
    )
    async_session.add_all([done_event, dead_event, future_event])
    await async_session.flush()

    assert await repo.acquire_outbox_lease(done_event.id, worker, timedelta(minutes=5)) is None
    assert await repo.acquire_outbox_lease(dead_event.id, worker, timedelta(minutes=5)) is None
    assert await repo.acquire_outbox_lease(future_event.id, worker, timedelta(minutes=5)) is None


@pytest.mark.asyncio
async def test_outbox_logical_idempotency(
    async_session: AsyncSession,
    base_entities: dict,
) -> None:
    """Test 9: PostgreSQL unique constraint rejects duplicate idempotency_key."""
    t1 = base_entities["tenant1"]
    idemp_key = f"unique-idemp-{uuid.uuid4().hex[:8]}"

    event1 = OutboxEvent(
        tenant_id=t1.id,
        aggregate_id=uuid.uuid4(),
        aggregate_version=1,
        event_type="mail.message_added",
        idempotency_key=idemp_key,
        payload={"msg_id": "123"},
    )
    async_session.add(event1)
    await async_session.flush()

    with pytest.raises((exc.IntegrityError, exc.DBAPIError)):
        async with async_session.begin_nested():
            event2 = OutboxEvent(
                tenant_id=t1.id,
                aggregate_id=uuid.uuid4(),
                aggregate_version=1,
                event_type="mail.message_added",
                idempotency_key=idemp_key,
                payload={"msg_id": "123"},
            )
            async_session.add(event2)
            await async_session.flush()


@pytest.mark.asyncio
async def test_resync_generation_preservation(
    async_session: AsyncSession,
    base_entities: dict,
) -> None:
    """Test 10: MailMessageFolder pivot correctly stores resync_generation on PostgreSQL."""
    t1 = base_entities["tenant1"]
    acc1 = base_entities["acc1_t1"]

    msg = MailMessage(
        tenant_id=t1.id,
        mail_account_id=acc1.id,
        provider_message_id=f"msg-{uuid.uuid4().hex[:8]}",
    )
    folder = MailFolder(
        tenant_id=t1.id,
        mail_account_id=acc1.id,
        provider_folder_id=f"folder-{uuid.uuid4().hex[:8]}",
        name="Inbox",
    )
    async_session.add_all([msg, folder])
    await async_session.flush()

    pivot = MailMessageFolder(
        mail_message_id=msg.id,
        mail_folder_id=folder.id,
        tenant_id=t1.id,
        mail_account_id=acc1.id,
        resync_generation=42,
    )
    async_session.add(pivot)
    await async_session.flush()

    await async_session.refresh(pivot)
    assert pivot.resync_generation == 42


@pytest.mark.asyncio
async def test_concurrent_refresh_lease_contention(
    session_maker,
    base_entities: dict,
) -> None:
    """Test 11: Multi-transaction concurrent contention for the same refresh lease row.

    Launches 10 simultaneous worker tasks, each executing acquire_refresh_lease in its
    own database transaction. Exactly ONE worker must succeed, and 9 must receive None.
    """
    acc_id = base_entities["acc1_t1"].id
    num_workers = 10
    workers = [uuid.uuid4() for _ in range(num_workers)]

    async def worker_acquire(worker_id: uuid.UUID):
        async with session_maker() as session:
            repo = MailAccountRepository(session)
            res = await repo.acquire_refresh_lease(acc_id, worker_id, timedelta(minutes=5))
            await session.commit()
            return res

    results = await asyncio.gather(*[worker_acquire(w) for w in workers])

    successful = [r for r in results if r is not None]
    failed = [r for r in results if r is None]

    assert len(successful) == 1, f"Expected 1 winner, got {len(successful)}"
    assert len(failed) == num_workers - 1, f"Expected {num_workers - 1} failures, got {len(failed)}"


@pytest.mark.asyncio
async def test_concurrent_outbox_lease_contention(
    session_maker,
    base_entities: dict,
) -> None:
    """Test 12: Multi-transaction concurrent contention for the same outbox event lease row.

    Launches 10 simultaneous worker tasks, each executing acquire_outbox_lease in its
    own database transaction. Exactly ONE worker must succeed, and 9 must receive None.
    """
    t1_id = base_entities["tenant1"].id
    event_id = uuid.uuid4()

    # Create outbox event in main session
    async with session_maker() as session:
        event = OutboxEvent(
            id=event_id,
            tenant_id=t1_id,
            aggregate_id=uuid.uuid4(),
            aggregate_version=1,
            event_type="mail.message_added",
            idempotency_key=f"idemp-concurrent-{uuid.uuid4().hex[:8]}",
            payload={"msg": "hello"},
            status=OutboxEventStatus.PENDING,
        )
        session.add(event)
        await session.commit()

    num_workers = 10
    workers = [uuid.uuid4() for _ in range(num_workers)]

    async def worker_acquire(worker_id: uuid.UUID):
        async with session_maker() as session:
            repo = OutboxEventRepository(session)
            res = await repo.acquire_outbox_lease(event_id, worker_id, timedelta(minutes=5))
            await session.commit()
            return res

    results = await asyncio.gather(*[worker_acquire(w) for w in workers])

    successful = [r for r in results if r is not None]
    failed = [r for r in results if r is None]

    assert len(successful) == 1, f"Expected 1 winner, got {len(successful)}"
    assert len(failed) == num_workers - 1, f"Expected {num_workers - 1} failures, got {len(failed)}"
