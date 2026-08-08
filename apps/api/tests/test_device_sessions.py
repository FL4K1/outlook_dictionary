"""Unit tests for DeviceSessionRepository and RefreshTokenFamilyRepository."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.auth import (
    DeviceSessionRepository,
    RefreshTokenFamilyRepository,
)
from mip_models.auth import DeviceSession, RefreshTokenFamily

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session() -> MagicMock:
    session = MagicMock(spec=AsyncSession)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def device_session_repo(mock_session: MagicMock) -> DeviceSessionRepository:
    return DeviceSessionRepository(mock_session)


@pytest.fixture
def refresh_token_family_repo(mock_session: MagicMock) -> RefreshTokenFamilyRepository:
    return RefreshTokenFamilyRepository(mock_session)


def _setup_execute_result(mock_session: AsyncSession, result: object) -> None:
    """Helper to configure session.execute to return a result with .scalars().first() or .all()."""
    mock_result = MagicMock()
    if isinstance(result, list):
        mock_result.scalars.return_value.all.return_value = result
        mock_result.scalars.return_value.first.return_value = result[0] if result else None
    else:
        mock_result.scalars.return_value.first.return_value = result
        mock_result.scalars.return_value.all.return_value = [result] if result else []
    mock_session.execute.return_value = mock_result  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# DeviceSessionRepository tests
# ---------------------------------------------------------------------------


class TestDeviceSessionRepository:
    """Tests for DeviceSessionRepository."""

    async def test_get_by_refresh_token_hash(
        self,
        device_session_repo: DeviceSessionRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test lookup by refresh token hash without locking."""
        expected = DeviceSession(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            current_refresh_token_hash="abc123",
        )
        _setup_execute_result(mock_session, expected)

        result = await device_session_repo.get_by_refresh_token_hash("abc123")

        assert result == expected
        # Verify no locking was used
        executed_stmt = mock_session.execute.call_args[0][0]
        assert "FOR UPDATE" not in str(executed_stmt).upper()

    async def test_get_by_refresh_token_hash_with_lock(
        self,
        device_session_repo: DeviceSessionRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test lookup by refresh token hash with row-level locking."""
        expected = DeviceSession(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            current_refresh_token_hash="abc123",
        )
        _setup_execute_result(mock_session, expected)

        result = await device_session_repo.get_by_refresh_token_hash("abc123", for_update=True)

        assert result == expected
        # Verify locking was requested
        executed_stmt = mock_session.execute.call_args[0][0]
        assert "FOR UPDATE" in str(executed_stmt).upper()

    async def test_get_by_refresh_token_hash_not_found(
        self,
        device_session_repo: DeviceSessionRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test lookup returns None when no session matches."""
        _setup_execute_result(mock_session, None)

        result = await device_session_repo.get_by_refresh_token_hash("nonexistent")

        assert result is None

    async def test_get_active_sessions_for_user(
        self,
        device_session_repo: DeviceSessionRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test fetching active sessions for a user."""
        user_id = uuid.uuid4()
        expected = [
            DeviceSession(id=uuid.uuid4(), user_id=user_id, tenant_id=uuid.uuid4()),
            DeviceSession(id=uuid.uuid4(), user_id=user_id, tenant_id=uuid.uuid4()),
        ]
        _setup_execute_result(mock_session, expected)

        result = await device_session_repo.get_active_sessions_for_user(user_id)

        assert result == expected
        # Verify revoked_at filter is applied
        executed_stmt = mock_session.execute.call_args[0][0]
        assert "revoked_at" in str(executed_stmt)

    async def test_get_active_sessions_for_tenant(
        self,
        device_session_repo: DeviceSessionRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test fetching active sessions for a tenant."""
        tenant_id = uuid.uuid4()
        expected = [
            DeviceSession(id=uuid.uuid4(), user_id=uuid.uuid4(), tenant_id=tenant_id),
        ]
        _setup_execute_result(mock_session, expected)

        result = await device_session_repo.get_active_sessions_for_tenant(tenant_id)

        assert result == expected
        executed_stmt = mock_session.execute.call_args[0][0]
        assert "tenant_id" in str(executed_stmt)

    async def test_revoke(
        self,
        device_session_repo: DeviceSessionRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test soft-revoking a specific session."""
        session_id = uuid.uuid4()
        revoked_at = datetime.now(UTC)

        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result

        result = await device_session_repo.revoke(session_id, revoked_at)

        assert result is True
        mock_session.flush.assert_called_once()

    async def test_revoke_already_revoked(
        self,
        device_session_repo: DeviceSessionRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test reviving an already-revoked session returns False."""
        session_id = uuid.uuid4()
        revoked_at = datetime.now(UTC)

        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute.return_value = mock_result

        result = await device_session_repo.revoke(session_id, revoked_at)

        assert result is False

    async def test_revoke_all_for_user(
        self,
        device_session_repo: DeviceSessionRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test revoking all sessions for a user."""
        user_id = uuid.uuid4()
        revoked_at = datetime.now(UTC)

        mock_result = MagicMock()
        mock_result.rowcount = 3
        mock_session.execute.return_value = mock_result

        result = await device_session_repo.revoke_all_for_user(user_id, revoked_at)

        assert result == 3
        mock_session.flush.assert_called_once()


# ---------------------------------------------------------------------------
# RefreshTokenFamilyRepository tests
# ---------------------------------------------------------------------------


class TestRefreshTokenFamilyRepository:
    """Tests for RefreshTokenFamilyRepository."""

    async def test_get_by_token_hash(
        self,
        refresh_token_family_repo: RefreshTokenFamilyRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test lookup by token hash without locking."""
        expected = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=uuid.uuid4(),
            token_hash="def456",
        )
        _setup_execute_result(mock_session, expected)

        result = await refresh_token_family_repo.get_by_token_hash("def456")

        assert result == expected
        executed_stmt = mock_session.execute.call_args[0][0]
        assert "FOR UPDATE" not in str(executed_stmt).upper()

    async def test_get_by_token_hash_with_lock(
        self,
        refresh_token_family_repo: RefreshTokenFamilyRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test lookup by token hash with row-level locking."""
        expected = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=uuid.uuid4(),
            token_hash="def456",
        )
        _setup_execute_result(mock_session, expected)

        result = await refresh_token_family_repo.get_by_token_hash("def456", for_update=True)

        assert result == expected
        executed_stmt = mock_session.execute.call_args[0][0]
        assert "FOR UPDATE" in str(executed_stmt).upper()

    async def test_get_by_token_hash_not_found(
        self,
        refresh_token_family_repo: RefreshTokenFamilyRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test lookup returns None when no family epoch matches."""
        _setup_execute_result(mock_session, None)

        result = await refresh_token_family_repo.get_by_token_hash("nonexistent")

        assert result is None

    async def test_get_active_for_session(
        self,
        refresh_token_family_repo: RefreshTokenFamilyRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test fetching the active epoch for a session."""
        session_id = uuid.uuid4()
        expected = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=session_id,
            token_hash="active-token",
            consumed_at=None,
        )
        _setup_execute_result(mock_session, expected)

        result = await refresh_token_family_repo.get_active_for_session(session_id)

        assert result == expected
        executed_stmt = mock_session.execute.call_args[0][0]
        assert "consumed_at" in str(executed_stmt)

    async def test_get_active_for_session_with_lock(
        self,
        refresh_token_family_repo: RefreshTokenFamilyRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test fetching active epoch with row-level locking."""
        session_id = uuid.uuid4()
        expected = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=session_id,
            token_hash="active-token",
            consumed_at=None,
        )
        _setup_execute_result(mock_session, expected)

        result = await refresh_token_family_repo.get_active_for_session(session_id, for_update=True)

        assert result == expected
        executed_stmt = mock_session.execute.call_args[0][0]
        assert "FOR UPDATE" in str(executed_stmt).upper()

    async def test_create_epoch(
        self,
        refresh_token_family_repo: RefreshTokenFamilyRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test creating a new refresh token family epoch."""
        device_session_id = uuid.uuid4()
        token_hash = "new-token-hash"  # noqa: S105

        fake_epoch = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=device_session_id,
            token_hash=token_hash,
        )
        mock_session.flush.return_value = None

        # Simulate BaseRepository.create behavior
        async def mock_create(**kwargs: object) -> RefreshTokenFamily:
            return fake_epoch

        refresh_token_family_repo.create = mock_create  # type: ignore[method-assign]

        result = await refresh_token_family_repo.create_epoch(
            device_session_id=device_session_id,
            token_hash=token_hash,
        )

        assert result == fake_epoch
        assert result.device_session_id == device_session_id
        assert result.token_hash == token_hash
        assert result.consumed_at is None

    async def test_create_epoch_with_consumed_at(
        self,
        refresh_token_family_repo: RefreshTokenFamilyRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test creating a consumed epoch (for backfill)."""
        device_session_id = uuid.uuid4()
        token_hash = "backfill-token"  # noqa: S105
        consumed_at = datetime.now(UTC)

        fake_epoch = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=device_session_id,
            token_hash=token_hash,
            consumed_at=consumed_at,
        )

        async def mock_create(**kwargs: object) -> RefreshTokenFamily:
            return fake_epoch

        refresh_token_family_repo.create = mock_create  # type: ignore[method-assign]

        result = await refresh_token_family_repo.create_epoch(
            device_session_id=device_session_id,
            token_hash=token_hash,
            consumed_at=consumed_at,
        )

        assert result == fake_epoch
        assert result.consumed_at == consumed_at

    async def test_mark_consumed(
        self,
        refresh_token_family_repo: RefreshTokenFamilyRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test marking an epoch as consumed."""
        family_id = uuid.uuid4()
        consumed_at = datetime.now(UTC)

        updated_epoch = RefreshTokenFamily(
            id=family_id,
            device_session_id=uuid.uuid4(),
            token_hash="token-hash",
            consumed_at=consumed_at,
        )

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = updated_epoch
        mock_session.execute.return_value = mock_result

        result = await refresh_token_family_repo.mark_consumed(family_id, consumed_at)

        assert result == updated_epoch
        assert result.consumed_at == consumed_at
        mock_session.flush.assert_called_once()

    async def test_mark_consumed_already_consumed(
        self,
        refresh_token_family_repo: RefreshTokenFamilyRepository,
        mock_session: MagicMock,
    ) -> None:
        """Test marking an already-consumed epoch returns None."""
        family_id = uuid.uuid4()
        consumed_at = datetime.now(UTC)

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = None
        mock_session.execute.return_value = mock_result

        result = await refresh_token_family_repo.mark_consumed(family_id, consumed_at)

        assert result is None
