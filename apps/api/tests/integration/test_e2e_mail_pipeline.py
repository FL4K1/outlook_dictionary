"""End-to-End Mail Pipeline Integration Test Suite (PR-2.6).

Validates the complete vertical slice:
Graph Provider -> SyncOrchestrator -> PostgreSQL -> OutboxEvent -> Worker -> ES.

Requires real PostgreSQL, real Redis, real Elasticsearch, and in-process ASGI Graph Harness.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from alembic import command
from alembic.config import Config
from mip_workers.es_adapter import ElasticsearchMailAdapter, RetryableElasticsearchError
from mip_workers.outbox_worker import OutboxWorker
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from tests.integration.fakes.fake_graph_harness import (
    app as fake_graph_app,
)
from tests.integration.fakes.fake_graph_harness import (
    register_delta_pages,
    reset_fake_graph_harness,
    set_fail_auth_attempts,
)

# Imports from app and mip packages
from app.repositories.mail import (
    MailFolderRepository,
    MailMessageFolderRepository,
    MailMessageRepository,
    MailSyncStateRepository,
)
from app.services.identity_provider import ProviderAuthService
from app.services.sync_orchestrator import SyncOrchestrator
from mip_models import (
    MailAccount,
    MailFolder,
    MailSyncStateValue,
    Organization,
    OutboxEvent,
    OutboxEventStatus,
    Tenant,
    User,
)
from mip_providers import MicrosoftGraphMailAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

POSTGRES_TEST_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://mip:mip_dev_password@localhost:5433/mail_intelligence",
)
ES_TEST_URL = os.getenv("TEST_ELASTICSEARCH_URL", "http://localhost:9200")


def _sync_apply_alembic_migrations(db_url: str) -> None:
    alembic_cfg = Config("apps/api/alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)
    alembic_cfg.set_main_option("script_location", "apps/api/alembic")
    command.upgrade(alembic_cfg, "head")


async def apply_alembic_migrations(db_url: str) -> None:
    await asyncio.to_thread(_sync_apply_alembic_migrations, db_url)


async def get_outbox_events(session: AsyncSession) -> list[OutboxEvent]:
    """Helper to query all outbox events ordered by created_at."""
    stmt = select(OutboxEvent).order_by(OutboxEvent.created_at.asc())
    res = await session.execute(stmt)
    return list(res.scalars().all())


@pytest.fixture
async def pg_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create async engine connected to real PostgreSQL migrated via Alembic."""
    engine = create_async_engine(POSTGRES_TEST_URL, poolclass=NullPool, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE;"))
            await conn.execute(text("CREATE SCHEMA public;"))
        await engine.dispose()
    except Exception as err:
        await engine.dispose()
        if os.getenv("CI") == "true":
            msg = f"PostgreSQL database required by CI unavailable at {POSTGRES_TEST_URL}: {err}"
            pytest.fail(msg)
        pytest.skip(f"PostgreSQL database not available at {POSTGRES_TEST_URL}: {err}")

    await apply_alembic_migrations(POSTGRES_TEST_URL)

    engine = create_async_engine(POSTGRES_TEST_URL, poolclass=NullPool, echo=False)
    yield engine
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE;"))
        await conn.execute(text("CREATE SCHEMA public;"))
    await engine.dispose()


@pytest.fixture
def session_maker(pg_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=pg_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def async_session(
    session_maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_maker() as session:
        yield session
        await session.rollback()
        await session.close()


@pytest.fixture(autouse=True)
def reset_harness() -> AsyncGenerator[None, None]:
    reset_fake_graph_harness()
    yield
    reset_fake_graph_harness()


@pytest.fixture
async def es_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(base_url=ES_TEST_URL, timeout=5.0) as client:
        yield client


@pytest.fixture
def graph_adapter() -> MicrosoftGraphMailAdapter:
    """Create MicrosoftGraphMailAdapter backed by in-process ASGI fake Graph harness."""
    transport = httpx.ASGITransport(app=fake_graph_app)
    asgi_client = httpx.AsyncClient(
        transport=transport, base_url="https://graph.microsoft.com/v1.0"
    )
    return MicrosoftGraphMailAdapter(access_token="test_e2e_access_token", client=asgi_client)


@pytest.fixture
async def setup_e2e_entities(
    async_session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount]:
    """Provision Organization, Tenant, User, MailAccount in PostgreSQL."""
    now = datetime.now(UTC)
    org = Organization(name="E2E Org", slug=f"org-{uuid.uuid4().hex[:8]}")
    async_session.add(org)
    await async_session.flush()

    tenant = Tenant(organization_id=org.id, name="E2E Tenant", slug=f"t-{uuid.uuid4().hex[:8]}")
    async_session.add(tenant)
    await async_session.flush()

    user = User(email=f"e2e-{uuid.uuid4().hex[:8]}@example.com", display_name="E2E User")
    async_session.add(user)
    await async_session.flush()

    account = MailAccount(
        tenant_id=tenant.id,
        provider_type="microsoft_graph",
        email_address=f"e2e-{uuid.uuid4().hex[:6]}@example.com",
        account_type="user",
        status="active",
        connected_by=user.id,
        connected_at=now,
    )
    async_session.add(account)
    await async_session.commit()

    return org.id, tenant.id, user.id, account


# ============================================================================
# SCENARIO 1 — ACCOUNT + FOLDER DISCOVERY
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_1_account_and_folder_discovery(
    async_session: AsyncSession,
    graph_adapter: MicrosoftGraphMailAdapter,
    setup_e2e_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount],
) -> None:
    _, tenant_id, _, account = setup_e2e_entities

    folders = await graph_adapter.get_folders()
    assert len(folders) >= 2
    assert any(f.name == "Inbox" for f in folders)

    folder_repo = MailFolderRepository(async_session)
    for pf in folders:
        f = MailFolder(
            tenant_id=tenant_id,
            mail_account_id=account.id,
            provider_folder_id=pf.provider_folder_id,
            name=pf.name,
            is_active=pf.is_active,
        )
        async_session.add(f)

    await async_session.commit()

    db_folders = await folder_repo.get_by_account_id(account.id)
    assert len(db_folders) == len(folders)
    for db_f in db_folders:
        assert db_f.tenant_id == tenant_id
        assert db_f.mail_account_id == account.id


# ============================================================================
# SCENARIO 2 — INITIAL DELTA SYNC
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_2_initial_delta_sync(
    async_session: AsyncSession,
    graph_adapter: MicrosoftGraphMailAdapter,
    setup_e2e_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount],
) -> None:
    _, tenant_id, _, account = setup_e2e_entities
    worker_id = uuid.uuid4()

    folder = MailFolder(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_id_123",
        name="Inbox",
    )
    async_session.add(folder)
    await async_session.commit()

    orchestrator = SyncOrchestrator(async_session, adapter_factory=lambda _: graph_adapter)
    res = await orchestrator.sync_folder(
        mail_folder_id=folder.id,
        worker_id=worker_id,
        access_token="test_token",
        adapter=graph_adapter,
    )

    assert res.state == MailSyncStateValue.DELTA_TRACKING
    assert res.messages_processed == 1
    assert res.messages_mutated == 1

    # Transaction Boundary check: Session must NOT be in active transaction post-sync
    assert async_session.in_transaction() is False

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account.id, "msg_default_inbox_id_123_1")
    assert msg is not None
    assert msg.tenant_id == tenant_id
    assert msg.subject == "Default Integration Subject"
    assert msg.version == 1

    sync_repo = MailSyncStateRepository(async_session)
    st = await sync_repo.get_by_folder_id(folder.id)
    assert st is not None
    assert st.sync_token is not None
    assert "delta_token_final_1" in st.sync_token


# ============================================================================
# SCENARIO 3 — MULTI-PAGE DELTA
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_3_multi_page_delta(
    async_session: AsyncSession,
    graph_adapter: MicrosoftGraphMailAdapter,
    setup_e2e_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount],
) -> None:
    _, tenant_id, _, account = setup_e2e_entities
    worker_id = uuid.uuid4()

    folder = MailFolder(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_multi_page",
        name="Inbox",
    )
    async_session.add(folder)
    await async_session.commit()

    page_1 = {
        "value": [
            {
                "id": "msg_mp_1",
                "subject": "Multi Page 1",
                "body": {"contentType": "text", "content": "P1"},
                "receivedDateTime": "2026-09-12T10:00:00Z",
            }
        ],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox_multi_page/messages/delta?$skiptoken=page_1",
    }
    page_2 = {
        "value": [
            {
                "id": "msg_mp_2",
                "subject": "Multi Page 2",
                "body": {"contentType": "text", "content": "P2"},
                "receivedDateTime": "2026-09-12T10:05:00Z",
            }
        ],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox_multi_page/messages/delta?$deltatoken=delta_mp_final",
    }
    register_delta_pages("inbox_multi_page", [page_1, page_2])

    orchestrator = SyncOrchestrator(async_session, adapter_factory=lambda _: graph_adapter)
    res = await orchestrator.sync_folder(
        mail_folder_id=folder.id,
        worker_id=worker_id,
        access_token="test_token",
        adapter=graph_adapter,
    )

    assert res.messages_processed == 2
    assert res.messages_mutated == 2

    msg_repo = MailMessageRepository(async_session)
    m1 = await msg_repo.get_by_provider_id(account.id, "msg_mp_1")
    m2 = await msg_repo.get_by_provider_id(account.id, "msg_mp_2")
    assert m1 is not None and m2 is not None


# ============================================================================
# SCENARIO 4 — OUTBOX EVENT CREATION
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_4_outbox_event_creation(
    async_session: AsyncSession,
    graph_adapter: MicrosoftGraphMailAdapter,
    setup_e2e_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount],
) -> None:
    _, tenant_id, _, account = setup_e2e_entities
    worker_id = uuid.uuid4()

    folder = MailFolder(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_outbox_test",
        name="Inbox",
    )
    async_session.add(folder)
    await async_session.commit()

    orchestrator = SyncOrchestrator(async_session, adapter_factory=lambda _: graph_adapter)
    await orchestrator.sync_folder(
        mail_folder_id=folder.id,
        worker_id=worker_id,
        access_token="test_token",
        adapter=graph_adapter,
    )

    events = [e for e in (await get_outbox_events(async_session)) if e.tenant_id == tenant_id]
    assert len(events) >= 1
    ev = events[0]
    assert ev.tenant_id == tenant_id
    assert ev.event_type == "MAIL_MESSAGE_MUTATED"
    assert ev.status == OutboxEventStatus.PENDING
    assert ev.aggregate_version == 1


# ============================================================================
# SCENARIO 5 & 6 — ARQ WORKER & ELASTICSEARCH INDEXING
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_5_and_6_arq_worker_and_es_indexing(
    async_session: AsyncSession,
    es_client: httpx.AsyncClient,
    graph_adapter: MicrosoftGraphMailAdapter,
    setup_e2e_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount],
) -> None:
    _, tenant_id, _, account = setup_e2e_entities
    worker_id = uuid.uuid4()

    folder = MailFolder(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_es_test",
        name="Inbox",
    )
    async_session.add(folder)
    await async_session.commit()

    orchestrator = SyncOrchestrator(async_session, adapter_factory=lambda _: graph_adapter)
    await orchestrator.sync_folder(
        mail_folder_id=folder.id,
        worker_id=worker_id,
        access_token="test_token",
        adapter=graph_adapter,
    )

    events = [e for e in (await get_outbox_events(async_session)) if e.tenant_id == tenant_id]
    assert len(events) == 1
    event_id = events[0].id

    es_adapter = ElasticsearchMailAdapter(base_url=ES_TEST_URL)
    worker = OutboxWorker(async_session, es_adapter=es_adapter)
    success = await worker.process_outbox_event(event_id, worker_id)
    assert success is True

    reloaded_event = (
        await async_session.execute(select(OutboxEvent).where(OutboxEvent.id == event_id))
    ).scalar_one_or_none()
    assert reloaded_event is not None
    assert reloaded_event.status == OutboxEventStatus.DONE

    msg_id = events[0].aggregate_id
    index_name = f"mail_messages_{tenant_id}"
    resp = await es_client.get(f"/{index_name}/_doc/{msg_id}")
    assert resp.status_code == 200
    doc = resp.json()
    assert doc["_source"]["tenant_id"] == str(tenant_id)
    assert doc["_source"]["subject"] == "Default Integration Subject"
    assert doc["_version"] == 1


# ============================================================================
# SCENARIO 7 & 12 — SEARCH QUERY & TENANT ISOLATION
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_7_and_12_search_query_tenant_isolation(
    async_session: AsyncSession,
    es_client: httpx.AsyncClient,
    graph_adapter: MicrosoftGraphMailAdapter,
    setup_e2e_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount],
) -> None:
    org_id, tenant_a_id, user_id, account_a = setup_e2e_entities
    worker_id = uuid.uuid4()

    tenant_b = Tenant(organization_id=org_id, name="Tenant B", slug=f"t-b-{uuid.uuid4().hex[:8]}")
    async_session.add(tenant_b)
    await async_session.flush()

    account_b = MailAccount(
        tenant_id=tenant_b.id,
        provider_type="microsoft_graph",
        email_address=f"tenantb-{uuid.uuid4().hex[:6]}@example.com",
        account_type="user",
        status="active",
        connected_by=user_id,
        connected_at=datetime.now(UTC),
    )
    async_session.add(account_b)
    await async_session.commit()

    folder_a = MailFolder(
        tenant_id=tenant_a_id,
        mail_account_id=account_a.id,
        provider_folder_id="inbox_tenant_a",
        name="Inbox",
    )
    async_session.add(folder_a)
    await async_session.commit()

    orchestrator = SyncOrchestrator(async_session, adapter_factory=lambda _: graph_adapter)
    await orchestrator.sync_folder(folder_a.id, worker_id, "token", adapter=graph_adapter)

    events = await get_outbox_events(async_session)
    ev_a = next(e for e in events if e.tenant_id == tenant_a_id)

    es_adapter = ElasticsearchMailAdapter(base_url=ES_TEST_URL)
    worker = OutboxWorker(async_session, es_adapter=es_adapter)
    await worker.process_outbox_event(ev_a.id, worker_id)

    index_a = f"mail_messages_{tenant_a_id}"
    index_b = f"mail_messages_{tenant_b.id}"
    await es_client.post(f"/{index_a}/_refresh")

    search_res_a = await es_client.post(f"/{index_a}/_search", json={"query": {"match_all": {}}})
    assert search_res_a.status_code == 200
    hits_a = search_res_a.json()["hits"]["hits"]
    assert len(hits_a) >= 1

    search_res_b = await es_client.post(f"/{index_b}/_search", json={"query": {"match_all": {}}})
    if search_res_b.status_code == 200:
        hits_b = search_res_b.json()["hits"]["hits"]
        assert len(hits_b) == 0
    else:
        assert search_res_b.status_code == 404


# ============================================================================
# SCENARIO 8 — MESSAGE UPDATE DELTA
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_8_message_update_delta(
    async_session: AsyncSession,
    es_client: httpx.AsyncClient,
    graph_adapter: MicrosoftGraphMailAdapter,
    setup_e2e_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount],
) -> None:
    _, tenant_id, _, account = setup_e2e_entities
    worker_id = uuid.uuid4()

    folder = MailFolder(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_update_test",
        name="Inbox",
    )
    async_session.add(folder)
    await async_session.commit()

    orchestrator = SyncOrchestrator(async_session, adapter_factory=lambda _: graph_adapter)
    await orchestrator.sync_folder(folder.id, worker_id, "token", adapter=graph_adapter)

    events = [e for e in (await get_outbox_events(async_session)) if e.tenant_id == tenant_id]
    es_adapter = ElasticsearchMailAdapter(base_url=ES_TEST_URL)
    worker = OutboxWorker(async_session, es_adapter=es_adapter)
    await worker.process_outbox_event(events[0].id, worker_id)

    update_page = {
        "value": [
            {
                "id": "msg_default_inbox_update_test_1",
                "subject": "UPDATED SUBJECT",
                "body": {"contentType": "text", "content": "Updated Body"},
                "receivedDateTime": "2026-09-12T10:00:00Z",
            }
        ],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox_update_test/messages/delta?$deltatoken=delta_v2",
    }
    register_delta_pages("inbox_update_test", [update_page])

    await orchestrator.sync_folder(folder.id, worker_id, "token", adapter=graph_adapter)

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account.id, "msg_default_inbox_update_test_1")
    assert msg is not None
    assert msg.version == 2
    assert msg.subject == "UPDATED SUBJECT"

    new_events = [e for e in (await get_outbox_events(async_session)) if e.tenant_id == tenant_id]
    ev_v2 = next(e for e in new_events if e.aggregate_version == 2)
    await worker.process_outbox_event(ev_v2.id, worker_id)

    index_name = f"mail_messages_{tenant_id}"
    resp = await es_client.get(f"/{index_name}/_doc/{msg.id}")
    assert resp.status_code == 200
    assert resp.json()["_version"] == 2
    assert resp.json()["_source"]["subject"] == "UPDATED SUBJECT"


# ============================================================================
# SCENARIO 9 — REMOVAL / TOMBSTONE
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_9_message_removal_tombstone(
    async_session: AsyncSession,
    es_client: httpx.AsyncClient,
    graph_adapter: MicrosoftGraphMailAdapter,
    setup_e2e_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount],
) -> None:
    _, tenant_id, _, account = setup_e2e_entities
    worker_id = uuid.uuid4()

    folder = MailFolder(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_remove_test",
        name="Inbox",
    )
    async_session.add(folder)
    await async_session.commit()

    orchestrator = SyncOrchestrator(async_session, adapter_factory=lambda _: graph_adapter)
    await orchestrator.sync_folder(folder.id, worker_id, "token", adapter=graph_adapter)

    events = [e for e in (await get_outbox_events(async_session)) if e.tenant_id == tenant_id]
    es_adapter = ElasticsearchMailAdapter(base_url=ES_TEST_URL)
    worker = OutboxWorker(async_session, es_adapter=es_adapter)
    await worker.process_outbox_event(events[0].id, worker_id)

    remove_page = {
        "value": [
            {
                "id": "msg_default_inbox_remove_test_1",
                "@removed": {"reason": "deleted"},
            }
        ],
        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox_remove_test/messages/delta?$deltatoken=delta_v_deleted",
    }
    register_delta_pages("inbox_remove_test", [remove_page])

    await orchestrator.sync_folder(folder.id, worker_id, "token", adapter=graph_adapter)

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account.id, "msg_default_inbox_remove_test_1")
    assert msg is not None

    pivot_repo = MailMessageFolderRepository(async_session)
    pivots = await pivot_repo.get_memberships_for_message(msg.id)
    assert len(pivots) == 0

    new_events = [e for e in (await get_outbox_events(async_session)) if e.tenant_id == tenant_id]
    ev_del = next(e for e in new_events if e.aggregate_version == 2)
    await worker.process_outbox_event(ev_del.id, worker_id)

    index_name = f"mail_messages_{tenant_id}"
    resp = await es_client.get(f"/{index_name}/_doc/{msg.id}")
    assert resp.status_code == 200
    assert resp.json()["_source"]["folder_ids"] == []


# ============================================================================
# SCENARIO 10 — REPLAY / IDEMPOTENCY
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_10_replay_idempotency(
    async_session: AsyncSession,
    graph_adapter: MicrosoftGraphMailAdapter,
    setup_e2e_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount],
) -> None:
    _, tenant_id, _, account = setup_e2e_entities
    worker_id = uuid.uuid4()

    folder = MailFolder(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_replay_test",
        name="Inbox",
    )
    async_session.add(folder)
    await async_session.commit()

    orchestrator = SyncOrchestrator(async_session, adapter_factory=lambda _: graph_adapter)
    await orchestrator.sync_folder(folder.id, worker_id, "token", adapter=graph_adapter)

    events = [e for e in (await get_outbox_events(async_session)) if e.tenant_id == tenant_id]
    ev_id = events[0].id

    es_adapter = ElasticsearchMailAdapter(base_url=ES_TEST_URL)
    worker = OutboxWorker(async_session, es_adapter=es_adapter)

    r1 = await worker.process_outbox_event(ev_id, worker_id)
    assert r1 is True

    r2 = await worker.process_outbox_event(ev_id, worker_id)
    assert r2 is False


# ============================================================================
# SCENARIO 11 — FAILURE & RETRY
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_11_failure_and_retry(
    async_session: AsyncSession,
    graph_adapter: MicrosoftGraphMailAdapter,
    setup_e2e_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount],
) -> None:
    _, tenant_id, _, account = setup_e2e_entities
    worker_id = uuid.uuid4()

    folder = MailFolder(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_failure_test",
        name="Inbox",
    )
    async_session.add(folder)
    await async_session.commit()

    orchestrator = SyncOrchestrator(async_session, adapter_factory=lambda _: graph_adapter)
    await orchestrator.sync_folder(folder.id, worker_id, "token", adapter=graph_adapter)

    events = [e for e in (await get_outbox_events(async_session)) if e.tenant_id == tenant_id]
    ev_id = events[0].id

    failing_es_adapter = MagicMock(spec=ElasticsearchMailAdapter)
    failing_es_adapter.index_message = AsyncMock(
        side_effect=RetryableElasticsearchError("ES 503 Service Unavailable", status_code=503)
    )

    worker = OutboxWorker(async_session, es_adapter=failing_es_adapter)
    ok = await worker.process_outbox_event(ev_id, worker_id)
    assert ok is False

    reloaded_event = (
        await async_session.execute(select(OutboxEvent).where(OutboxEvent.id == ev_id))
    ).scalar_one_or_none()
    assert reloaded_event is not None
    assert reloaded_event.status in (OutboxEventStatus.PENDING, "PENDING")
    assert reloaded_event.attempt_count == 1


# ============================================================================
# SCENARIO 13 — AUTH EXPIRATION & RETRY FENCING
# ============================================================================
@pytest.mark.asyncio
async def test_scenario_13_auth_expiration_retry(
    async_session: AsyncSession,
    graph_adapter: MicrosoftGraphMailAdapter,
    setup_e2e_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount],
) -> None:
    _, tenant_id, _, account = setup_e2e_entities
    worker_id = uuid.uuid4()

    folder = MailFolder(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_auth_expired",
        name="Inbox",
    )
    async_session.add(folder)
    await async_session.commit()

    set_fail_auth_attempts(1)

    auth_svc = MagicMock(spec=ProviderAuthService)
    refreshed_creds = MagicMock()
    refreshed_creds.access_token = "new_refreshed_token"  # noqa: S105
    auth_svc.refresh_mail_account_credentials = AsyncMock(return_value=refreshed_creds)

    orchestrator = SyncOrchestrator(
        async_session,
        adapter_factory=lambda _: graph_adapter,
        provider_auth_service=auth_svc,
    )
    res = await orchestrator.sync_folder(
        mail_folder_id=folder.id,
        worker_id=worker_id,
        access_token="old_token",
        adapter=graph_adapter,
    )

    assert res.state == MailSyncStateValue.DELTA_TRACKING
    auth_svc.refresh_mail_account_credentials.assert_called_once()

    set_fail_auth_attempts(2)
    auth_svc.refresh_mail_account_credentials.reset_mock()

    res_fail = await orchestrator.sync_folder(
        mail_folder_id=folder.id,
        worker_id=worker_id,
        access_token="old_token",
        adapter=graph_adapter,
    )

    assert res_fail.state == MailSyncStateValue.AUTH_REQUIRED
    auth_svc.refresh_mail_account_credentials.assert_called_once()
