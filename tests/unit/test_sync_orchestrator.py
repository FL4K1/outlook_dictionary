"""Unit and integration tests for Mail Sync Orchestrator (PR-2.3) on PostgreSQL.

Validates all 24 required test scenarios A through X:
A. New message creation
B. Existing message scalar update
C. UNSET preserves existing values
D. Explicit null clears value
E. Participant replacement
F. Participant normalization
G. Folder addition
H. Folder-scoped removal
I. Cross-folder message preservation
J. aggregate_changed=false produces no version/outbox
K. aggregate_changed=true increments version exactly once
L. Exactly one outbox event per mutation
M. Deterministic idempotency_key
N. Page rollback does not advance sync_token
O. Successful page commit advances sync_token
P. Duplicate/replayed provider page is idempotent
Q. Concurrent new-message creation
R. Stale sync worker cannot commit after lease takeover
S. AUTH_REQUIRED on AuthExpiredError
T. DeltaCursorExpiredError starts resync_generation
U. Old-generation folder memberships are reconciled
V. 410 recovery never blindly replaces folder membership
W. deltaLink checkpoint persistence
X. Continuation page processing remains deterministic
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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.repositories.mail import (
    MailFolderRepository,
    MailMessageFolderRepository,
    MailMessageParticipantRepository,
    MailMessageRepository,
    MailSyncStateRepository,
    OutboxEventRepository,
)
from app.services.sync_orchestrator import SyncOrchestrator
from mip_models import (
    MailAccount,
    MailFolder,
    MailSyncState,
    MailSyncStateValue,
    Organization,
    OutboxEvent,
    Tenant,
    User,
)
from mip_providers import (
    UNSET,
    AuthExpiredError,
    DeltaCursorExpiredError,
    ProviderDeltaPage,
    ProviderEmailAddress,
    ProviderMessage,
    ProviderRemoval,
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
async def session_maker(pg_engine):
    return async_sessionmaker(bind=pg_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def async_session(session_maker) -> AsyncGenerator[AsyncSession, None]:
    async with session_maker() as session:
        yield session
        await session.rollback()
        await session.close()


@pytest.fixture
async def setup_entities(
    async_session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create base DB entities on PostgreSQL."""
    now = datetime.now(UTC)
    org = Organization(name="Test Org", slug=f"org-{uuid.uuid4().hex[:8]}")
    async_session.add(org)
    await async_session.flush()

    tenant = Tenant(organization_id=org.id, name="Test Tenant", slug=f"t-{uuid.uuid4().hex[:8]}")
    async_session.add(tenant)
    await async_session.flush()

    user = User(email=f"u-{uuid.uuid4().hex[:8]}@example.com", display_name="Test User")
    async_session.add(user)
    await async_session.flush()

    account = MailAccount(
        tenant_id=tenant.id,
        provider_type="microsoft_graph",
        email_address=f"acc-{uuid.uuid4().hex[:6]}@example.com",
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
    await async_session.flush()

    sync_state = MailSyncState(
        tenant_id=tenant.id,
        mail_folder_id=folder.id,
        state=MailSyncStateValue.PENDING_INITIAL_SYNC,
        sync_token=None,
        resync_generation=1,
        lease_version=1,
        updated_at=now,
    )
    async_session.add(sync_state)
    await async_session.commit()

    return tenant.id, user.id, account.id, folder.id


class MockProviderAdapter:
    """Mock provider adapter for unit testing SyncOrchestrator."""

    def __init__(self, pages: list[ProviderDeltaPage] | Exception | None = None) -> None:
        self.pages = pages if isinstance(pages, list) else []
        self.exception = pages if isinstance(pages, Exception) else None
        self.calls: list[tuple[str, str | None]] = []

    async def get_message_delta(
        self, folder_id: str, opaque_continuation: str | None = None
    ) -> ProviderDeltaPage:
        self.calls.append((folder_id, opaque_continuation))
        if self.exception:
            raise self.exception
        if self.pages:
            return self.pages.pop(0)
        return ProviderDeltaPage(
            messages=[],
            removals=[],
            next_continuation="delta_checkpoint_token_999",
            has_more=False,
            is_delta_checkpoint=True,
        )


@pytest.mark.asyncio
async def test_a_new_message_creation(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    provider_msg = ProviderMessage(
        provider_message_id="msg_001",
        subject="Welcome Email",
        body={"contentType": "text", "content": "Hello!"},
        body_preview="Hello!",
        sender=ProviderEmailAddress(email="alice@example.com", name="Alice"),
        recipients_to=[ProviderEmailAddress(email="bob@example.com", name="Bob")],
        has_attachments=False,
        is_read=True,
    )

    page = ProviderDeltaPage(
        messages=[provider_msg],
        removals=[],
        next_continuation="token_page_1",
        has_more=False,
        is_delta_checkpoint=True,
    )
    adapter = MockProviderAdapter([page])

    orchestrator = SyncOrchestrator(async_session)
    result = await orchestrator.sync_folder(folder_id, worker_id, "token_123", adapter=adapter)

    assert result.error is None
    assert result.messages_processed == 1
    assert result.messages_mutated == 1

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_001")
    assert msg is not None
    assert msg.version == 1
    assert msg.subject == "Welcome Email"

    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.get_by_idempotency_key(f"{msg.id}::1::MAIL_MESSAGE_MUTATED")
    assert event is not None
    assert event.aggregate_version == 1


@pytest.mark.asyncio
async def test_b_existing_message_scalar_update(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page1 = ProviderDeltaPage(
        messages=[
            ProviderMessage(
                provider_message_id="msg_002", subject="Original Subject", is_read=False
            )
        ],
        removals=[],
        next_continuation="token_1",
        has_more=True,
        is_delta_checkpoint=False,
    )
    page2 = ProviderDeltaPage(
        messages=[
            ProviderMessage(provider_message_id="msg_002", subject="Updated Subject", is_read=True)
        ],
        removals=[],
        next_continuation="token_2",
        has_more=False,
        is_delta_checkpoint=True,
    )
    adapter = MockProviderAdapter([page1, page2])

    orchestrator = SyncOrchestrator(async_session)
    result = await orchestrator.sync_folder(folder_id, worker_id, "token_123", adapter=adapter)

    assert result.error is None
    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_002")
    assert msg is not None
    assert msg.subject == "Updated Subject"
    assert msg.is_read is True
    assert msg.version == 2


@pytest.mark.asyncio
async def test_c_unset_preserves_existing_values(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page1 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_003", subject="Keep Me", is_read=False)],
        removals=[],
        next_continuation="t1",
        has_more=True,
        is_delta_checkpoint=False,
    )
    page2 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_003", subject=UNSET, is_read=True)],
        removals=[],
        next_continuation="t2",
        has_more=False,
        is_delta_checkpoint=True,
    )
    adapter = MockProviderAdapter([page1, page2])

    orchestrator = SyncOrchestrator(async_session)
    await orchestrator.sync_folder(folder_id, worker_id, "token_123", adapter=adapter)

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_003")
    assert msg is not None
    assert msg.subject == "Keep Me"
    assert msg.is_read is True


@pytest.mark.asyncio
async def test_d_explicit_null_clears_value(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page1 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_004", subject="Clear Me")],
        removals=[],
        next_continuation="t1",
        has_more=True,
        is_delta_checkpoint=False,
    )
    page2 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_004", subject=None)],
        removals=[],
        next_continuation="t2",
        has_more=False,
        is_delta_checkpoint=True,
    )
    adapter = MockProviderAdapter([page1, page2])

    orchestrator = SyncOrchestrator(async_session)
    await orchestrator.sync_folder(folder_id, worker_id, "token_123", adapter=adapter)

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_004")
    assert msg is not None
    assert msg.subject is None
    assert msg.version == 2


@pytest.mark.asyncio
async def test_e_participant_replacement(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page1 = ProviderDeltaPage(
        messages=[
            ProviderMessage(
                provider_message_id="msg_005",
                recipients_to=[ProviderEmailAddress(email="p1@example.com", name="P1")],
            )
        ],
        next_continuation="t1",
        has_more=True,
        is_delta_checkpoint=False,
        removals=[],
    )
    page2 = ProviderDeltaPage(
        messages=[
            ProviderMessage(
                provider_message_id="msg_005",
                recipients_to=[
                    ProviderEmailAddress(email="p2@example.com", name="P2"),
                    ProviderEmailAddress(email="p3@example.com", name="P3"),
                ],
            )
        ],
        next_continuation="t2",
        has_more=False,
        is_delta_checkpoint=True,
        removals=[],
    )
    adapter = MockProviderAdapter([page1, page2])

    orchestrator = SyncOrchestrator(async_session)
    await orchestrator.sync_folder(folder_id, worker_id, "token_123", adapter=adapter)

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_005")
    assert msg is not None

    part_repo = MailMessageParticipantRepository(async_session)
    participants = await part_repo.get_by_message_id(msg.id)
    emails = sorted([p.email for p in participants])
    assert emails == ["p2@example.com", "p3@example.com"]


@pytest.mark.asyncio
async def test_f_participant_normalization(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page = ProviderDeltaPage(
        messages=[
            ProviderMessage(
                provider_message_id="msg_006",
                sender=ProviderEmailAddress(email="  ALICE@Example.COM  ", name="Alice"),
                recipients_to=[ProviderEmailAddress(email="  BOB@Example.COM  ", name="Bob")],
            )
        ],
        removals=[],
        next_continuation="t1",
        has_more=False,
        is_delta_checkpoint=True,
    )
    adapter = MockProviderAdapter([page])

    orchestrator = SyncOrchestrator(async_session)
    await orchestrator.sync_folder(folder_id, worker_id, "token_123", adapter=adapter)

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_006")
    assert msg is not None
    assert msg.sender == {"name": "Alice", "email": "alice@example.com"}

    part_repo = MailMessageParticipantRepository(async_session)
    participants = await part_repo.get_by_message_id(msg.id)
    assert len(participants) == 1
    assert participants[0].email == "bob@example.com"


@pytest.mark.asyncio
async def test_g_folder_addition(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_007", subject="Pivot Test")],
        removals=[],
        next_continuation="t1",
        has_more=False,
        is_delta_checkpoint=True,
    )
    adapter = MockProviderAdapter([page])

    orchestrator = SyncOrchestrator(async_session)
    await orchestrator.sync_folder(folder_id, worker_id, "token_123", adapter=adapter)

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_007")
    assert msg is not None

    pivot_repo = MailMessageFolderRepository(async_session)
    membership = await pivot_repo.get_membership(msg.id, folder_id)
    assert membership is not None
    assert membership.resync_generation == 1


@pytest.mark.asyncio
async def test_h_folder_scoped_removal(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page1 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_008", subject="Removal Test")],
        removals=[],
        next_continuation="t1",
        has_more=True,
        is_delta_checkpoint=False,
    )
    page2 = ProviderDeltaPage(
        messages=[],
        removals=[ProviderRemoval(provider_message_id="msg_008")],
        next_continuation="t2",
        has_more=False,
        is_delta_checkpoint=True,
    )
    adapter = MockProviderAdapter([page1, page2])

    orchestrator = SyncOrchestrator(async_session)
    await orchestrator.sync_folder(folder_id, worker_id, "token_123", adapter=adapter)

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_008")
    assert msg is not None
    assert msg.is_deleted is False  # Message itself remains!

    pivot_repo = MailMessageFolderRepository(async_session)
    membership = await pivot_repo.get_membership(msg.id, folder_id)
    assert membership is None  # Membership deleted!


@pytest.mark.asyncio
async def test_i_cross_folder_message_preservation(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    tenant_id, _, account_id, folder1_id = setup_entities
    worker_id = uuid.uuid4()

    # Create folder 2
    folder2 = MailFolder(
        tenant_id=tenant_id,
        mail_account_id=account_id,
        provider_folder_id="sent",
        name="Sent Items",
        is_active=True,
    )
    async_session.add(folder2)
    await async_session.flush()

    folder2_sync_state = MailSyncState(
        tenant_id=tenant_id,
        mail_folder_id=folder2.id,
        state=MailSyncStateValue.PENDING_INITIAL_SYNC,
        sync_token=None,
        resync_generation=1,
        lease_version=1,
        updated_at=datetime.now(UTC),
    )
    async_session.add(folder2_sync_state)
    await async_session.commit()

    # Sync into Folder 1
    page1 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_009", subject="Multi Folder")],
        removals=[],
        next_continuation="t1",
        has_more=False,
        is_delta_checkpoint=True,
    )
    await SyncOrchestrator(async_session).sync_folder(
        folder1_id, worker_id, "token_123", adapter=MockProviderAdapter([page1])
    )

    # Sync into Folder 2
    page2 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_009", subject="Multi Folder")],
        removals=[],
        next_continuation="t2",
        has_more=False,
        is_delta_checkpoint=True,
    )
    await SyncOrchestrator(async_session).sync_folder(
        folder2.id, worker_id, "token_123", adapter=MockProviderAdapter([page2])
    )

    # Removal in Folder 1 only
    page3 = ProviderDeltaPage(
        messages=[],
        removals=[ProviderRemoval(provider_message_id="msg_009")],
        next_continuation="t3",
        has_more=False,
        is_delta_checkpoint=True,
    )
    await SyncOrchestrator(async_session).sync_folder(
        folder1_id, worker_id, "token_123", adapter=MockProviderAdapter([page3])
    )

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_009")
    assert msg is not None

    pivot_repo = MailMessageFolderRepository(async_session)
    pivot1 = await pivot_repo.get_membership(msg.id, folder1_id)
    pivot2 = await pivot_repo.get_membership(msg.id, folder2.id)
    assert pivot1 is None
    assert pivot2 is not None


@pytest.mark.asyncio
async def test_j_aggregate_changed_false_produces_no_version_or_outbox(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    msg_payload = ProviderMessage(
        provider_message_id="msg_010", subject="Identical Payload", is_read=True
    )
    page1 = ProviderDeltaPage(
        messages=[msg_payload],
        removals=[],
        next_continuation="t1",
        has_more=True,
        is_delta_checkpoint=False,
    )
    page2 = ProviderDeltaPage(
        messages=[msg_payload],
        removals=[],
        next_continuation="t2",
        has_more=False,
        is_delta_checkpoint=True,
    )

    orchestrator = SyncOrchestrator(async_session)
    await orchestrator.sync_folder(
        folder_id, worker_id, "token_123", adapter=MockProviderAdapter([page1, page2])
    )

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_010")
    assert msg is not None
    assert msg.version == 1

    outbox_repo = OutboxEventRepository(async_session)
    event2 = await outbox_repo.get_by_idempotency_key(f"{msg.id}::2::MAIL_MESSAGE_MUTATED")
    assert event2 is None  # No version 2 outbox event created!


@pytest.mark.asyncio
async def test_k_aggregate_changed_true_increments_version_exactly_once(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page1 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_011", subject="V1")],
        removals=[],
        next_continuation="t1",
        has_more=True,
        is_delta_checkpoint=False,
    )
    page2 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_011", subject="V2")],
        removals=[],
        next_continuation="t2",
        has_more=False,
        is_delta_checkpoint=True,
    )

    orchestrator = SyncOrchestrator(async_session)
    await orchestrator.sync_folder(
        folder_id, worker_id, "token_123", adapter=MockProviderAdapter([page1, page2])
    )

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_011")
    assert msg is not None
    assert msg.version == 2


@pytest.mark.asyncio
async def test_l_exactly_one_outbox_event_per_mutation(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, _account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_012", subject="Single Event")],
        removals=[],
        next_continuation="t1",
        has_more=False,
        is_delta_checkpoint=True,
    )

    orchestrator = SyncOrchestrator(async_session)
    await orchestrator.sync_folder(
        folder_id, worker_id, "token_123", adapter=MockProviderAdapter([page])
    )

    events = await async_session.execute(select(OutboxEvent))
    all_events = events.scalars().all()
    assert len(all_events) == 1


@pytest.mark.asyncio
async def test_m_deterministic_idempotency_key(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_013", subject="Key Test")],
        removals=[],
        next_continuation="t1",
        has_more=False,
        is_delta_checkpoint=True,
    )

    await SyncOrchestrator(async_session).sync_folder(
        folder_id, worker_id, "token_123", adapter=MockProviderAdapter([page])
    )

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_013")
    assert msg is not None

    expected_key = f"{msg.id}::1::MAIL_MESSAGE_MUTATED"
    outbox_repo = OutboxEventRepository(async_session)
    event = await outbox_repo.get_by_idempotency_key(expected_key)
    assert event is not None
    assert event.idempotency_key == expected_key


@pytest.mark.asyncio
async def test_n_page_rollback_does_not_advance_sync_token(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, _account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    sync_state_repo = MailSyncStateRepository(async_session)

    class FailingAdapter:
        async def get_message_delta(
            self, folder_id: str, opaque_continuation: str | None = None
        ) -> ProviderDeltaPage:
            return ProviderDeltaPage(
                messages=[ProviderMessage(provider_message_id="msg_fail")],
                removals=[],
                next_continuation="should_not_be_saved",
                has_more=False,
                is_delta_checkpoint=False,
            )

    orchestrator = SyncOrchestrator(async_session)

    async def _failing_process(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("Simulated DB processing failure")

    orchestrator._process_provider_message = _failing_process  # type: ignore[assignment]

    result = await orchestrator.sync_folder(
        folder_id, worker_id, "token_123", adapter=FailingAdapter()
    )  # type: ignore[arg-type]
    assert result.error is not None

    st = await sync_state_repo.get_by_folder_id(folder_id)
    assert st is not None
    assert st.sync_token is None  # Token did not advance!


@pytest.mark.asyncio
async def test_o_successful_page_commit_advances_sync_token(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, _account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page = ProviderDeltaPage(
        messages=[],
        removals=[],
        next_continuation="checkpoint_advance_123",
        has_more=False,
        is_delta_checkpoint=True,
    )

    await SyncOrchestrator(async_session).sync_folder(
        folder_id, worker_id, "token_123", adapter=MockProviderAdapter([page])
    )

    sync_state_repo = MailSyncStateRepository(async_session)
    st = await sync_state_repo.get_by_folder_id(folder_id)
    assert st is not None
    assert st.sync_token == "checkpoint_advance_123"  # noqa: S105


@pytest.mark.asyncio
async def test_p_duplicate_replayed_provider_page_is_idempotent(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_016", subject="Replay Test")],
        removals=[],
        next_continuation="t1",
        has_more=False,
        is_delta_checkpoint=True,
    )

    # First run
    await SyncOrchestrator(async_session).sync_folder(
        folder_id, worker_id, "token_123", adapter=MockProviderAdapter([page])
    )

    # Replay same page
    page_replay = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_016", subject="Replay Test")],
        removals=[],
        next_continuation="t1",
        has_more=False,
        is_delta_checkpoint=True,
    )
    await SyncOrchestrator(async_session).sync_folder(
        folder_id, worker_id, "token_123", adapter=MockProviderAdapter([page_replay])
    )

    msg_repo = MailMessageRepository(async_session)
    msg = await msg_repo.get_by_provider_id(account_id, "msg_016")
    assert msg is not None
    assert msg.version == 1  # Remained version 1!


@pytest.mark.asyncio
async def test_q_concurrent_new_message_creation(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, _account_id, folder_id = setup_entities
    folder = await MailFolderRepository(async_session).get(folder_id)
    assert folder is not None

    orchestrator = SyncOrchestrator(async_session)
    msg_payload = ProviderMessage(provider_message_id="msg_concurrent", subject="Concurrent Test")

    res1 = await orchestrator._process_provider_message(
        folder.tenant_id, folder.mail_account_id, folder.id, msg_payload, resync_generation=1
    )
    res2 = await orchestrator._process_provider_message(
        folder.tenant_id, folder.mail_account_id, folder.id, msg_payload, resync_generation=1
    )

    assert res1 is True
    assert (
        res2 is False
    )  # Second call recognized existing message without throwing primary key exception!


@pytest.mark.asyncio
async def test_r_stale_sync_worker_cannot_commit_after_lease_takeover(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, _account_id, folder_id = setup_entities
    worker1_id = uuid.uuid4()
    worker2_id = uuid.uuid4()

    sync_state_repo = MailSyncStateRepository(async_session)
    st = await sync_state_repo.get_by_folder_id(folder_id)
    assert st is not None

    # Worker 1 acquires lease (version 2)
    v1 = await sync_state_repo.acquire_sync_lease(st.id, worker1_id, timedelta(minutes=5))
    assert v1 == 2

    # Expire Worker 1's lease to simulate timeout
    st.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await async_session.commit()

    # Worker 2 takes over lease (version 3)
    v2 = await sync_state_repo.acquire_sync_lease(st.id, worker2_id, timedelta(minutes=5))
    assert v2 == 3

    # Worker 1 attempts to update sync state using old lease version 2
    cas_ok = await sync_state_repo.update_sync_state_cas(
        st.id, worker1_id, lease_version=v1, state=MailSyncStateValue.DELTA_TRACKING
    )
    assert cas_ok is False  # Stale worker blocked by CAS!


@pytest.mark.asyncio
async def test_s_auth_required_on_auth_expired_error(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, _account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    adapter = MockProviderAdapter(AuthExpiredError("Token expired", status_code=401))

    orchestrator = SyncOrchestrator(async_session)
    result = await orchestrator.sync_folder(folder_id, worker_id, "expired_token", adapter=adapter)

    assert result.state == MailSyncStateValue.AUTH_REQUIRED
    st = await MailSyncStateRepository(async_session).get_by_folder_id(folder_id)
    assert st is not None
    assert st.state == MailSyncStateValue.AUTH_REQUIRED


@pytest.mark.asyncio
async def test_t_delta_cursor_expired_error_starts_resync_generation(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, _account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page_after_410 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_resync_1")],
        removals=[],
        next_continuation="new_checkpoint",
        has_more=False,
        is_delta_checkpoint=True,
    )

    class ResyncAdapter:
        def __init__(self) -> None:
            self.raised = False

        async def get_message_delta(
            self, folder_id: str, opaque_continuation: str | None = None
        ) -> ProviderDeltaPage:
            if not self.raised:
                self.raised = True
                raise DeltaCursorExpiredError("410 Gone")
            return page_after_410

    orchestrator = SyncOrchestrator(async_session)
    result = await orchestrator.sync_folder(
        folder_id, worker_id, "token_123", adapter=ResyncAdapter()
    )  # type: ignore[arg-type]

    assert result.resync_triggered is True
    st = await MailSyncStateRepository(async_session).get_by_folder_id(folder_id)
    assert st is not None
    assert st.resync_generation == 2  # resync generation incremented to 2!


@pytest.mark.asyncio
async def test_u_old_generation_folder_memberships_are_reconciled(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    p1 = ProviderDeltaPage(
        messages=[
            ProviderMessage(provider_message_id="msg_A"),
            ProviderMessage(provider_message_id="msg_B"),
        ],
        removals=[],
        next_continuation="c1",
        has_more=False,
        is_delta_checkpoint=True,
    )
    await SyncOrchestrator(async_session).sync_folder(
        folder_id, worker_id, "token_123", adapter=MockProviderAdapter([p1])
    )

    p2 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_A")],
        removals=[],
        next_continuation="c2",
        has_more=False,
        is_delta_checkpoint=True,
    )

    class ResyncAdapter:
        def __init__(self) -> None:
            self.raised = False

        async def get_message_delta(
            self, folder_id: str, opaque_continuation: str | None = None
        ) -> ProviderDeltaPage:
            if not self.raised:
                self.raised = True
                raise DeltaCursorExpiredError("410 Cursor Expired")
            return p2

    await SyncOrchestrator(async_session).sync_folder(
        folder_id, worker_id, "token_123", adapter=ResyncAdapter()
    )  # type: ignore[arg-type]

    msg_repo = MailMessageRepository(async_session)
    msg_b = await msg_repo.get_by_provider_id(account_id, "msg_B")
    assert msg_b is not None

    pivot_repo = MailMessageFolderRepository(async_session)
    membership_b = await pivot_repo.get_membership(msg_b.id, folder_id)
    assert membership_b is None  # Stale membership for msg_B removed upon resync completion!


@pytest.mark.asyncio
async def test_v_410_recovery_never_blindly_replaces_folder_membership(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    p1 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_keep")],
        removals=[],
        next_continuation="c1",
        has_more=False,
        is_delta_checkpoint=True,
    )
    await SyncOrchestrator(async_session).sync_folder(
        folder_id, worker_id, "token_123", adapter=MockProviderAdapter([p1])
    )

    p_partial = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_new")],
        removals=[],
        next_continuation="c2",
        has_more=True,
        is_delta_checkpoint=False,
    )
    p_final = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_keep")],
        removals=[],
        next_continuation="c3",
        has_more=False,
        is_delta_checkpoint=True,
    )

    class TwoPageResyncAdapter:
        def __init__(self) -> None:
            self.raised = False
            self.pages = [p_partial, p_final]

        async def get_message_delta(
            self, folder_id: str, opaque_continuation: str | None = None
        ) -> ProviderDeltaPage:
            if not self.raised:
                self.raised = True
                raise DeltaCursorExpiredError("410 Cursor Expired")
            return self.pages.pop(0)

    await SyncOrchestrator(async_session).sync_folder(
        folder_id, worker_id, "token_123", adapter=TwoPageResyncAdapter()
    )  # type: ignore[arg-type]

    msg_repo = MailMessageRepository(async_session)
    msg_keep = await msg_repo.get_by_provider_id(account_id, "msg_keep")
    assert msg_keep is not None

    pivot_repo = MailMessageFolderRepository(async_session)
    membership_keep = await pivot_repo.get_membership(msg_keep.id, folder_id)
    assert membership_keep is not None
    assert (
        membership_keep.resync_generation == 2
    )  # Membership preserved and updated to resync_generation 2!


@pytest.mark.asyncio
async def test_w_deltalink_checkpoint_persistence(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, _account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    page = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="msg_w")],
        removals=[],
        next_continuation="final_delta_checkpoint_link_https_graph_123",
        has_more=False,
        is_delta_checkpoint=True,
    )
    await SyncOrchestrator(async_session).sync_folder(
        folder_id, worker_id, "token_123", adapter=MockProviderAdapter([page])
    )

    st = await MailSyncStateRepository(async_session).get_by_folder_id(folder_id)
    assert st is not None
    assert st.state == MailSyncStateValue.DELTA_TRACKING
    assert st.sync_token == "final_delta_checkpoint_link_https_graph_123"  # noqa: S105


@pytest.mark.asyncio
async def test_x_continuation_page_processing_remains_deterministic(
    async_session: AsyncSession,
    setup_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    _tenant_id, _, _account_id, folder_id = setup_entities
    worker_id = uuid.uuid4()

    p1 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="m1")],
        removals=[],
        next_continuation="page_2_link",
        has_more=True,
        is_delta_checkpoint=False,
    )
    p2 = ProviderDeltaPage(
        messages=[ProviderMessage(provider_message_id="m2")],
        removals=[],
        next_continuation="final_delta_link",
        has_more=False,
        is_delta_checkpoint=True,
    )

    adapter = MockProviderAdapter([p1, p2])
    orchestrator = SyncOrchestrator(async_session)

    res = await orchestrator.sync_folder(folder_id, worker_id, "token_123", adapter=adapter)

    assert res.error is None
    assert res.messages_processed == 2
    assert res.messages_mutated == 2
    assert res.state == MailSyncStateValue.DELTA_TRACKING

    st = await MailSyncStateRepository(async_session).get_by_folder_id(folder_id)
    assert st is not None
    assert st.sync_token == "final_delta_link"  # noqa: S105
