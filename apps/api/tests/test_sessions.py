"""Unit tests for SessionService and SessionRepository."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from app.auth.exceptions import RefreshTokenReusedError, SessionExpiredError, TokenInvalidError
from app.auth.sessions import SessionService
from app.auth.tokens import RefreshTokenPair, TokenService
from app.common.config import Environment, Settings
from app.repositories.auth import SessionRepository
from mip_models.auth import Session

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> Settings:
    return Settings(
        app_env=Environment.TESTING,
        jwt_access_token_expire_minutes=15,
        jwt_refresh_token_expire_days=30,
        session_idle_timeout_hours=8,
        postgres_password="test",
        object_storage_secret_key="test",
    )


@pytest.fixture
def mock_repo() -> MagicMock:
    repo = MagicMock(spec=SessionRepository)
    repo.create = AsyncMock()
    repo.get_by_refresh_token_hash = AsyncMock()
    repo.revoke_all_for_user = AsyncMock()
    repo.revoke = AsyncMock()
    return repo


@pytest.fixture
def mock_token_service() -> MagicMock:
    ts = MagicMock(spec=TokenService)
    ts.generate_refresh_token.return_value = RefreshTokenPair(
        plaintext="fake-plaintext-token",
        hash_val="fake-hashed-token",
    )
    ts.hash_refresh_token.side_effect = lambda t: f"hashed-{t}"
    return ts


@pytest.fixture
def session_service(
    mock_repo: MagicMock,
    mock_token_service: MagicMock,
    mock_settings: Settings,
) -> SessionService:
    return SessionService(
        repo=mock_repo,
        token_service=mock_token_service,
        settings=mock_settings,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSessionServiceCreation:
    """Verify session creation logic."""

    async def test_create_session(
        self,
        session_service: SessionService,
        mock_repo: MagicMock,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()

        # Setup mock DB return
        fake_session = Session(id=uuid.uuid4(), user_id=user_id, tenant_id=tenant_id)
        mock_repo.create.return_value = fake_session

        session, token_pair = await session_service.create_session(
            user_id=user_id,
            tenant_id=tenant_id,
            ip_address="192.168.1.1",
            user_agent="TestAgent",
        )

        assert session == fake_session
        assert token_pair.plaintext == "fake-plaintext-token"

        # Verify repo create was called with correct arguments
        mock_repo.create.assert_called_once()
        kwargs = mock_repo.create.call_args.kwargs
        assert kwargs["user_id"] == user_id
        assert kwargs["tenant_id"] == tenant_id
        assert kwargs["refresh_token_hash"] == "fake-hashed-token"  # noqa: S105
        assert kwargs["ip_address"] == "192.168.1.1"
        assert kwargs["user_agent"] == "TestAgent"
        assert isinstance(kwargs["expires_at"], datetime)
        assert isinstance(kwargs["last_active_at"], datetime)


class TestSessionServiceRefresh:
    """Verify session rotation and reuse detection."""

    async def test_refresh_invalid_token(
        self,
        session_service: SessionService,
        mock_repo: MagicMock,
    ) -> None:
        # Repo returns None when looking up the token hash
        mock_repo.get_by_refresh_token_hash.return_value = None

        with pytest.raises(TokenInvalidError, match="Invalid refresh token"):
            await session_service.refresh_session("bad-token")

    async def test_refresh_reuse_detection(
        self,
        session_service: SessionService,
        mock_repo: MagicMock,
    ) -> None:
        # Repo returns a session that has ALREADY been revoked
        revoked_session = Session(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            revoked_at=datetime.now(UTC),
        )
        mock_repo.get_by_refresh_token_hash.return_value = revoked_session

        with pytest.raises(RefreshTokenReusedError):
            await session_service.refresh_session("stolen-token")

        # Verify all sessions were revoked for this user
        mock_repo.revoke_all_for_user.assert_called_once()
        assert mock_repo.revoke_all_for_user.call_args.args[0] == revoked_session.user_id

    async def test_refresh_absolute_timeout(
        self,
        session_service: SessionService,
        mock_repo: MagicMock,
    ) -> None:
        # Repo returns a session that is past its absolute expires_at
        now = datetime.now(UTC)
        expired_session = Session(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            revoked_at=None,
            expires_at=now - timedelta(days=1),  # Expired yesterday
            last_active_at=now,
        )
        mock_repo.get_by_refresh_token_hash.return_value = expired_session

        with pytest.raises(SessionExpiredError, match="Absolute timeout exceeded"):
            await session_service.refresh_session("expired-token")

        # Verify the session was marked as revoked
        mock_repo.revoke.assert_called_once_with(expired_session.id, revoked_at=ANY)

    async def test_refresh_idle_timeout(
        self,
        session_service: SessionService,
        mock_repo: MagicMock,
    ) -> None:
        # Repo returns a session that is past its idle timeout (8 hours default)
        now = datetime.now(UTC)
        idle_session = Session(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            revoked_at=None,
            expires_at=now + timedelta(days=30),
            last_active_at=now - timedelta(hours=9),  # Idle for 9 hours
        )
        mock_repo.get_by_refresh_token_hash.return_value = idle_session

        with pytest.raises(SessionExpiredError, match="Idle timeout exceeded"):
            await session_service.refresh_session("idle-token")

        mock_repo.revoke.assert_called_once_with(idle_session.id, revoked_at=ANY)

    async def test_refresh_successful_rotation(
        self,
        session_service: SessionService,
        mock_repo: MagicMock,
    ) -> None:
        now = datetime.now(UTC)
        valid_session = Session(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            revoked_at=None,
            expires_at=now + timedelta(days=30),
            last_active_at=now,
        )
        mock_repo.get_by_refresh_token_hash.return_value = valid_session

        # Mock DB creation of the new rotated session
        new_fake_session = Session(id=uuid.uuid4())
        mock_repo.create.return_value = new_fake_session

        new_session, new_token_pair = await session_service.refresh_session(
            "valid-token",
            ip_address="10.0.0.1",
            user_agent="NewAgent",
        )

        assert new_session == new_fake_session
        assert new_token_pair.plaintext == "fake-plaintext-token"

        # 1. The old session must have been revoked
        mock_repo.revoke.assert_called_once_with(valid_session.id, revoked_at=ANY)

        # 2. A new session must have been created with the SAME absolute expiration
        mock_repo.create.assert_called_once()
        create_kwargs = mock_repo.create.call_args.kwargs
        assert create_kwargs["user_id"] == valid_session.user_id
        assert create_kwargs["tenant_id"] == valid_session.tenant_id
        assert create_kwargs["refresh_token_hash"] == "fake-hashed-token"  # noqa: S105
        assert create_kwargs["expires_at"] == valid_session.expires_at
        assert create_kwargs["ip_address"] == "10.0.0.1"
