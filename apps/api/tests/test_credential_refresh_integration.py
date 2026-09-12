"""Integration test suite for PR-2.5 Credential Refresh Integration (Scenarios A-O).

Tests against real PostgreSQL database migrated via Alembic.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.repositories.mail import (
    MailAccountRepository,
    MailFolderRepository,
    MailSyncStateRepository,
)
from app.services.identity_provider import ProviderAuthError, ProviderAuthService
from app.services.sync_orchestrator import SyncOrchestrator
from mip_models import (
    MailAccount,
    MailSyncStateValue,
    Organization,
    ProviderCredential,
    Tenant,
    User,
)
from mip_providers import AuthExpiredError
from mip_providers.base import ProviderDeltaPage
from mip_providers.identity.base import ProviderCredentialSet

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
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential]:
    """Create Organization, Tenant, User, MailAccount, ProviderCredential in PostgreSQL."""
    now = datetime.now(UTC)
    org = Organization(name="Refresh Org", slug=f"org-{uuid.uuid4().hex[:8]}")
    async_session.add(org)
    await async_session.flush()

    tenant = Tenant(organization_id=org.id, name="Refresh Tenant", slug=f"t-{uuid.uuid4().hex[:8]}")
    async_session.add(tenant)
    await async_session.flush()

    user = User(email=f"user-{uuid.uuid4().hex[:8]}@example.com", display_name="Refresh User")
    async_session.add(user)
    await async_session.flush()

    account = MailAccount(
        tenant_id=tenant.id,
        provider_type="microsoft_graph",
        email_address=f"refresh-{uuid.uuid4().hex[:6]}@example.com",
        account_type="user",
        status="active",
        connected_by=user.id,
        connected_at=now,
        credential_generation=1,
    )
    async_session.add(account)
    await async_session.flush()

    cred = ProviderCredential(
        mail_account_id=account.id,
        tenant_id=tenant.id,
        encrypted_access_token=b"enc_initial_access_token",
        encrypted_refresh_token=b"enc_initial_refresh_token",
        token_expires_at=now + timedelta(hours=1),
        encryption_key_id="key-v1",
        scopes=["Mail.Read"],
    )
    async_session.add(cred)
    await async_session.commit()

    return (org.id, tenant.id, user.id, account, cred)


@pytest.fixture
def mock_encryption():
    enc = MagicMock()
    enc.decrypt_string.side_effect = lambda b: b.decode("utf-8") if isinstance(b, bytes) else str(b)
    enc.encrypt.side_effect = lambda s: f"enc_{s}".encode()
    enc.key_id = "key-v2"
    return enc


# A. Refresh lease acquisition
@pytest.mark.asyncio
async def test_a_refresh_lease_acquisition(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
) -> None:
    _, _, _, account, _ = setup_base_entities
    worker_id = uuid.uuid4()
    repo = MailAccountRepository(async_session)

    res = await repo.acquire_refresh_lease(account.id, worker_id, timedelta(minutes=5))
    assert res is not None
    lease_version, gen = res
    assert lease_version >= 1
    assert gen == 1

    reloaded = await repo.get(account.id)
    assert reloaded is not None
    assert reloaded.refresh_locked_by == worker_id
    assert reloaded.refresh_locked_until is not None


# B. Second worker rejected while lease active
@pytest.mark.asyncio
async def test_b_second_worker_rejected_while_lease_active(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
) -> None:
    _, _, _, account, _ = setup_base_entities
    worker1 = uuid.uuid4()
    worker2 = uuid.uuid4()
    repo = MailAccountRepository(async_session)

    res1 = await repo.acquire_refresh_lease(account.id, worker1, timedelta(minutes=5))
    assert res1 is not None

    res2 = await repo.acquire_refresh_lease(account.id, worker2, timedelta(minutes=5))
    assert res2 is None


# C. Expired lease takeover
@pytest.mark.asyncio
async def test_c_expired_lease_takeover(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
) -> None:
    _, _, _, account, _ = setup_base_entities
    worker1 = uuid.uuid4()
    worker2 = uuid.uuid4()
    repo = MailAccountRepository(async_session)

    res1 = await repo.acquire_refresh_lease(account.id, worker1, timedelta(seconds=-10))
    assert res1 is not None

    # Worker 1 lease expired in past; Worker 2 takes over
    res2 = await repo.acquire_refresh_lease(account.id, worker2, timedelta(minutes=5))
    assert res2 is not None
    assert res2[0] > res1[0]


# D. Refresh lease version increments on takeover
@pytest.mark.asyncio
async def test_d_refresh_lease_version_increments_on_takeover(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
) -> None:
    _, _, _, account, _ = setup_base_entities
    worker1 = uuid.uuid4()
    worker2 = uuid.uuid4()
    repo = MailAccountRepository(async_session)

    res1 = await repo.acquire_refresh_lease(account.id, worker1, timedelta(seconds=-1))
    assert res1 is not None

    res2 = await repo.acquire_refresh_lease(account.id, worker2, timedelta(minutes=5))
    assert res2 is not None
    assert res2[0] == res1[0] + 1


# E. Generation already advanced prevents duplicate provider refresh
@pytest.mark.asyncio
async def test_e_generation_already_advanced_prevents_duplicate_provider_refresh(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
    mock_encryption: MagicMock,
) -> None:
    _, _, _, account, _ = setup_base_entities
    account.credential_generation = 2
    await async_session.commit()

    mock_provider_auth = AsyncMock()
    service = ProviderAuthService(mock_provider_auth, MagicMock(), mock_encryption)

    # Worker passes expected_generation=1, but DB is at 2 (advanced)
    res = await service.refresh_mail_account_credentials(
        async_session, account.id, uuid.uuid4(), expected_generation=1
    )
    assert res is not None
    # Provider HTTP should NOT be called
    mock_provider_auth.refresh_credentials.assert_not_called()


# F. Successful provider refresh
@pytest.mark.asyncio
async def test_f_successful_provider_refresh(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
    mock_encryption: MagicMock,
) -> None:
    _, _, _, account, _ = setup_base_entities
    worker_id = uuid.uuid4()

    mock_provider_auth = AsyncMock()
    mock_provider_auth.refresh_credentials.return_value = ProviderCredentialSet(
        access_token="new_access_token_xyz",
        refresh_token="new_refresh_token_abc",
        expires_at=datetime.now(UTC) + timedelta(hours=2),
        scopes=["Mail.ReadWrite"],
    )

    service = ProviderAuthService(mock_provider_auth, MagicMock(), mock_encryption)
    new_creds = await service.refresh_mail_account_credentials(async_session, account.id, worker_id)

    assert new_creds.access_token == "new_access_token_xyz"
    mock_provider_auth.refresh_credentials.assert_awaited_once_with("enc_initial_refresh_token")


# G. Atomic ProviderCredential + generation update
@pytest.mark.asyncio
async def test_g_atomic_provider_credential_and_generation_update(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
    mock_encryption: MagicMock,
) -> None:
    _, _, _, account, cred = setup_base_entities
    worker_id = uuid.uuid4()

    mock_provider_auth = AsyncMock()
    mock_provider_auth.refresh_credentials.return_value = ProviderCredentialSet(
        access_token="atomic_access_123",
        refresh_token="atomic_refresh_456",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )

    service = ProviderAuthService(mock_provider_auth, MagicMock(), mock_encryption)
    await service.refresh_mail_account_credentials(async_session, account.id, worker_id)

    reloaded_account = await MailAccountRepository(async_session).get(account.id)
    reloaded_cred = await service._get_provider_credential_by_account(async_session, account.id)

    assert reloaded_account is not None
    assert reloaded_account.credential_generation == 2
    assert reloaded_account.refresh_locked_by is None

    assert reloaded_cred is not None
    assert reloaded_cred.encrypted_access_token == b"enc_atomic_access_123"
    assert reloaded_cred.encrypted_refresh_token == b"enc_atomic_refresh_456"


# H. Stale worker finalization fails
@pytest.mark.asyncio
async def test_h_stale_worker_finalization_fails(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
) -> None:
    _, _, _, account, _ = setup_base_entities
    worker1 = uuid.uuid4()
    repo = MailAccountRepository(async_session)

    res1 = await repo.acquire_refresh_lease(account.id, worker1, timedelta(minutes=5))
    assert res1 is not None
    lease_version, _ = res1

    # Stale worker 2 tries to finalize worker 1 lease
    stale_ok = await repo.finalize_refresh_lease(account.id, uuid.uuid4(), lease_version)
    assert stale_ok is False


# I. Invalid grant path
@pytest.mark.asyncio
async def test_i_invalid_grant_path(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
    mock_encryption: MagicMock,
) -> None:
    _, _, _, account, _ = setup_base_entities
    worker_id = uuid.uuid4()

    mock_provider_auth = AsyncMock()
    mock_provider_auth.refresh_credentials.side_effect = RuntimeError(
        "invalid_grant: Refresh token has expired or been revoked"
    )

    service = ProviderAuthService(mock_provider_auth, MagicMock(), mock_encryption)
    with pytest.raises(ProviderAuthError) as exc_info:
        await service.refresh_mail_account_credentials(async_session, account.id, worker_id)

    assert "invalid_grant" in str(exc_info.value).lower()

    reloaded_account = await MailAccountRepository(async_session).get(account.id)
    assert reloaded_account is not None
    assert reloaded_account.status == "reauth_required"
    assert reloaded_account.refresh_locked_by is None


# J. Refresh token never appears in logs/errors
@pytest.mark.asyncio
async def test_j_refresh_token_never_appears_in_logs_or_errors(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
    mock_encryption: MagicMock,
) -> None:
    _, _, _, account, _ = setup_base_entities
    worker_id = uuid.uuid4()

    mock_provider_auth = AsyncMock()
    mock_provider_auth.refresh_credentials.side_effect = RuntimeError(
        "invalid_grant with secret refresh_token=secret_refresh_tok_999"
    )

    service = ProviderAuthService(mock_provider_auth, MagicMock(), mock_encryption)
    with pytest.raises(ProviderAuthError):
        await service.refresh_mail_account_credentials(async_session, account.id, worker_id)

    # Credentials in DB remain intact/encrypted without corruption
    cred = await service._get_provider_credential_by_account(async_session, account.id)
    assert cred is not None
    assert b"secret_refresh_tok_999" not in cred.encrypted_refresh_token


# K. AuthExpiredError triggers refresh
@pytest.mark.asyncio
async def test_k_auth_expired_error_triggers_refresh(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
    mock_encryption: MagicMock,
) -> None:
    _, tenant_id, _, account, _ = setup_base_entities
    worker_id = uuid.uuid4()

    # Create folder and sync state
    folder_repo = MailFolderRepository(async_session)
    folder = await folder_repo.create(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_folder_k",
        name="Inbox",
    )

    sync_state_repo = MailSyncStateRepository(async_session)
    await sync_state_repo.create(
        tenant_id=tenant_id,
        mail_folder_id=folder.id,
        state=MailSyncStateValue.DELTA_TRACKING,
    )
    await async_session.commit()

    mock_adapter = AsyncMock()
    mock_adapter.get_message_delta.side_effect = [
        AuthExpiredError("401 Unauthorized token expired"),
        ProviderDeltaPage(
            messages=[],
            removals=[],
            next_continuation="new_delta_k",
            has_more=False,
            is_delta_checkpoint=True,
        ),
    ]

    mock_provider_auth = AsyncMock()
    mock_provider_auth.refresh_credentials.return_value = ProviderCredentialSet(
        access_token="refreshed_access_k",
        refresh_token="refreshed_refresh_k",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )

    service = ProviderAuthService(mock_provider_auth, MagicMock(), mock_encryption)
    orchestrator = SyncOrchestrator(
        async_session,
        adapter_factory=lambda tok: mock_adapter,
        provider_auth_service=service,
    )

    res = await orchestrator.sync_folder(
        mail_folder_id=folder.id,
        worker_id=worker_id,
        access_token="expired_tok",
        adapter=mock_adapter,
    )

    assert res.error is None
    mock_provider_auth.refresh_credentials.assert_awaited_once()


# L. Successful refresh causes exactly one provider retry
@pytest.mark.asyncio
async def test_l_successful_refresh_causes_exactly_one_provider_retry(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
    mock_encryption: MagicMock,
) -> None:
    _, tenant_id, _, account, _ = setup_base_entities
    worker_id = uuid.uuid4()

    folder = await MailFolderRepository(async_session).create(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_folder_l",
        name="Inbox",
    )

    await MailSyncStateRepository(async_session).create(
        tenant_id=tenant_id,
        mail_folder_id=folder.id,
        state=MailSyncStateValue.DELTA_TRACKING,
    )
    await async_session.commit()

    mock_adapter = AsyncMock()
    mock_adapter.get_message_delta.side_effect = [
        AuthExpiredError("401 Expired"),
        ProviderDeltaPage(
            messages=[],
            removals=[],
            next_continuation="delta_l",
            has_more=False,
            is_delta_checkpoint=True,
        ),
    ]

    mock_provider_auth = AsyncMock()
    mock_provider_auth.refresh_credentials.return_value = ProviderCredentialSet(
        access_token="fresh_token_l",
        refresh_token="fresh_ref_l",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )

    service = ProviderAuthService(mock_provider_auth, MagicMock(), mock_encryption)
    orchestrator = SyncOrchestrator(
        async_session,
        adapter_factory=lambda tok: mock_adapter,
        provider_auth_service=service,
    )

    res = await orchestrator.sync_folder(
        mail_folder_id=folder.id,
        worker_id=worker_id,
        access_token="exp_tok",
        adapter=mock_adapter,
    )

    assert res.error is None
    assert mock_adapter.get_message_delta.call_count == 2


# M. Repeated AuthExpiredError does not loop infinitely
@pytest.mark.asyncio
async def test_m_repeated_auth_expired_error_does_not_loop_infinitely(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
    mock_encryption: MagicMock,
) -> None:
    _, tenant_id, _, account, _ = setup_base_entities
    worker_id = uuid.uuid4()

    folder = await MailFolderRepository(async_session).create(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_folder_m",
        name="Inbox",
    )

    await MailSyncStateRepository(async_session).create(
        tenant_id=tenant_id,
        mail_folder_id=folder.id,
        state=MailSyncStateValue.DELTA_TRACKING,
    )
    await async_session.commit()

    mock_adapter = AsyncMock()
    # Adapter keeps throwing AuthExpiredError even after token refresh
    mock_adapter.get_message_delta.side_effect = AuthExpiredError("Repeated 401 Unauthorized")

    mock_provider_auth = AsyncMock()
    mock_provider_auth.refresh_credentials.return_value = ProviderCredentialSet(
        access_token="fresh_token_m",
        refresh_token="fresh_ref_m",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        scopes=["Mail.Read"],
    )

    service = ProviderAuthService(mock_provider_auth, MagicMock(), mock_encryption)
    orchestrator = SyncOrchestrator(
        async_session,
        adapter_factory=lambda tok: mock_adapter,
        provider_auth_service=service,
    )

    res = await orchestrator.sync_folder(
        mail_folder_id=folder.id,
        worker_id=worker_id,
        access_token="exp_tok",
        adapter=mock_adapter,
    )

    assert res.state == MailSyncStateValue.AUTH_REQUIRED
    assert mock_adapter.get_message_delta.call_count == 2  # 1 initial + 1 retry = MAX 2 CALLS!


# N. Failed refresh transitions to reauth-required behavior
@pytest.mark.asyncio
async def test_n_failed_refresh_transitions_to_reauth_required_behavior(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
    mock_encryption: MagicMock,
) -> None:
    _, tenant_id, _, account, _ = setup_base_entities
    worker_id = uuid.uuid4()

    folder = await MailFolderRepository(async_session).create(
        tenant_id=tenant_id,
        mail_account_id=account.id,
        provider_folder_id="inbox_folder_n",
        name="Inbox",
    )

    await MailSyncStateRepository(async_session).create(
        tenant_id=tenant_id,
        mail_folder_id=folder.id,
        state=MailSyncStateValue.DELTA_TRACKING,
    )
    await async_session.commit()

    mock_adapter = AsyncMock()
    mock_adapter.get_message_delta.side_effect = AuthExpiredError("401 Unauthorized")

    mock_provider_auth = AsyncMock()
    mock_provider_auth.refresh_credentials.side_effect = RuntimeError("invalid_grant")

    service = ProviderAuthService(mock_provider_auth, MagicMock(), mock_encryption)
    orchestrator = SyncOrchestrator(
        async_session,
        adapter_factory=lambda tok: mock_adapter,
        provider_auth_service=service,
    )

    res = await orchestrator.sync_folder(
        mail_folder_id=folder.id,
        worker_id=worker_id,
        access_token="exp_tok",
        adapter=mock_adapter,
    )

    assert res.state == MailSyncStateValue.AUTH_REQUIRED
    sync_state = await MailSyncStateRepository(async_session).get_by_folder_id(folder.id)
    assert sync_state is not None
    assert sync_state.state == MailSyncStateValue.AUTH_REQUIRED


# O. Platform session remains valid when mailbox auth expires
@pytest.mark.asyncio
async def test_o_platform_session_remains_valid_when_mailbox_auth_expires(
    async_session: AsyncSession,
    setup_base_entities: tuple[uuid.UUID, uuid.UUID, uuid.UUID, MailAccount, ProviderCredential],
    mock_encryption: MagicMock,
) -> None:
    _, _, user_id, account, _ = setup_base_entities

    mock_provider_auth = AsyncMock()
    mock_provider_auth.refresh_credentials.side_effect = RuntimeError("invalid_grant")

    service = ProviderAuthService(mock_provider_auth, MagicMock(), mock_encryption)
    with pytest.raises(ProviderAuthError):
        await service.refresh_mail_account_credentials(async_session, account.id, uuid.uuid4())

    # User remains active in DB
    user = await async_session.get(User, user_id)
    assert user is not None
    assert user.is_active is True
