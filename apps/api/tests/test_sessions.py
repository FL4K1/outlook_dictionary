"""Unit tests for SessionService public methods."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.auth.events import SecurityEventType, SecurityOutcome
from app.auth.exceptions import RefreshTokenReusedError, SessionExpiredError, TokenInvalidError
from app.auth.sessions import SessionService
from app.auth.tokens import RefreshTokenPair, TokenService
from app.common.config import Environment, Settings
from app.repositories.auth import (
    DeviceSessionRepository,
    RefreshTokenFamilyRepository,
)
from mip_models.auth import DeviceSession, RefreshTokenFamily

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
        session_absolute_timeout_days=30,
        session_remember_me_days=90,
        postgres_password="test",
        object_storage_secret_key="test",
    )


@pytest.fixture
def mock_device_session_repo() -> MagicMock:
    repo = MagicMock(spec=DeviceSessionRepository)
    repo.create = AsyncMock()
    repo.get_by_refresh_token_hash = AsyncMock()
    repo.revoke = AsyncMock()
    repo.revoke_all_for_user = AsyncMock()
    return repo


@pytest.fixture
def mock_refresh_token_family_repo() -> MagicMock:
    repo = MagicMock(spec=RefreshTokenFamilyRepository)
    repo.create_epoch = AsyncMock()
    repo.get_by_token_hash = AsyncMock()
    repo.mark_consumed = AsyncMock()
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
    mock_device_session_repo: MagicMock,
    mock_refresh_token_family_repo: MagicMock,
    mock_token_service: MagicMock,
    mock_settings: Settings,
) -> SessionService:
    return SessionService(
        device_session_repo=mock_device_session_repo,
        refresh_token_family_repo=mock_refresh_token_family_repo,
        token_service=mock_token_service,
        settings=mock_settings,
    )


# ---------------------------------------------------------------------------
# create_session tests
# ---------------------------------------------------------------------------


class TestCreateSession:
    """Verify session creation logic."""

    async def test_create_session(
        self,
        session_service: SessionService,
        mock_device_session_repo: MagicMock,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()

        fake_session = DeviceSession(
            id=uuid.uuid4(),
            user_id=user_id,
            tenant_id=tenant_id,
            current_refresh_token_hash="fake-hashed-token",
        )
        mock_device_session_repo.create.return_value = fake_session

        session, token_pair = await session_service.create_session(
            user_id=user_id,
            tenant_id=tenant_id,
            ip_address="192.168.1.1",
            user_agent="TestAgent",
            remember_me=True,
        )

        assert session == fake_session
        assert token_pair.plaintext == "fake-plaintext-token"

        # Verify DeviceSession creation
        mock_device_session_repo.create.assert_called_once()
        ds_kwargs = mock_device_session_repo.create.call_args.kwargs
        assert ds_kwargs["user_id"] == user_id
        assert ds_kwargs["tenant_id"] == tenant_id
        assert ds_kwargs["current_refresh_token_hash"] == "fake-hashed-token"  # noqa: S105
        assert ds_kwargs["ip_address"] == "192.168.1.1"
        assert ds_kwargs["user_agent"] == "TestAgent"
        assert isinstance(ds_kwargs["expires_at"], datetime)
        assert isinstance(ds_kwargs["last_active_at"], datetime)

        # Verify RefreshTokenFamily epoch creation
        mock_refresh_token_family_repo.create_epoch.assert_called_once_with(
            device_session_id=fake_session.id,
            token_hash="fake-hashed-token",
        )

    async def test_create_session_emits_event(
        self,
        session_service: SessionService,
        mock_device_session_repo: MagicMock,
    ) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()

        fake_session = DeviceSession(
            id=uuid.uuid4(),
            user_id=user_id,
            tenant_id=tenant_id,
            current_refresh_token_hash="fake-hashed-token",
        )
        mock_device_session_repo.create.return_value = fake_session

        with patch("app.auth.sessions.security_event_emitter.emit") as emit_mock:
            await session_service.create_session(
                user_id=user_id,
                tenant_id=tenant_id,
                ip_address="10.0.0.1",
                user_agent="pytest",
            )

            emit_mock.assert_called_once()
            event = emit_mock.call_args[0][0]
            assert event.event_type == SecurityEventType.SESSION_CREATED
            assert event.outcome == SecurityOutcome.SUCCESS
            assert event.user_id == user_id
            assert event.tenant_id == tenant_id
            assert event.session_id == fake_session.id
            assert event.ip_address == "10.0.0.1"
            assert event.user_agent == "pytest"


# ---------------------------------------------------------------------------
# refresh_session tests
# ---------------------------------------------------------------------------


class TestRefreshSession:
    """Verify refresh rotation, timeout enforcement, and reuse detection."""

    async def test_refresh_invalid_token(
        self,
        session_service: SessionService,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        mock_refresh_token_family_repo.get_by_token_hash.return_value = None

        with pytest.raises(TokenInvalidError, match="Invalid refresh token"):
            await session_service.refresh_session("bad-token")

        # Verify locking was requested
        call_kwargs = mock_refresh_token_family_repo.get_by_token_hash.call_args.kwargs
        assert call_kwargs["for_update"] is True

    async def test_refresh_invalid_token_emits_token_refresh_failed(
        self,
        session_service: SessionService,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        mock_refresh_token_family_repo.get_by_token_hash.return_value = None

        with patch("app.auth.sessions.security_event_emitter.emit") as emit_mock:
            with pytest.raises(TokenInvalidError, match="Invalid refresh token"):
                await session_service.refresh_session(
                    "bad-token",
                    ip_address="10.0.0.1",
                    user_agent="TestAgent",
                    request_id="req-123",
                )

            emit_mock.assert_called_once()
            event = emit_mock.call_args[0][0]
            assert event.event_type == SecurityEventType.TOKEN_REFRESH_FAILED
            assert event.outcome == SecurityOutcome.FAILURE
            assert event.reason == "Invalid refresh token."
            assert event.ip_address == "10.0.0.1"
            assert event.user_agent == "TestAgent"
            assert event.request_id == "req-123"
            assert event.user_id is None
            assert event.tenant_id is None
            assert event.session_id is None
            assert event.metadata == {}
            log_dict = event.to_log_dict()
            assert "token" not in log_dict
            assert "refresh_token" not in log_dict
            assert "jti" not in log_dict
            assert "user_id" not in log_dict

    async def test_refresh_successful_event_includes_request_id(
        self,
        session_service: SessionService,
        mock_device_session_repo: MagicMock,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        now = datetime.now(UTC)
        family = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=uuid.uuid4(),
            token_hash="fake-hashed-token",
            consumed_at=None,
        )
        mock_refresh_token_family_repo.get_by_token_hash.return_value = family

        valid_session = DeviceSession(
            id=family.device_session_id,
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            current_refresh_token_hash="fake-hashed-token",
            revoked_at=None,
            expires_at=now + timedelta(days=30),
            last_active_at=now,
        )
        mock_device_session_repo.get_by_refresh_token_hash.return_value = valid_session
        mock_device_session_repo.update.return_value = valid_session

        with patch("app.auth.sessions.security_event_emitter.emit") as emit_mock:
            await session_service.refresh_session(
                "valid-token",
                ip_address="10.0.0.1",
                user_agent="TestAgent",
                request_id="req-success",
            )

            refresh_events = [
                c
                for c in emit_mock.call_args_list
                if c.args[0].event_type == SecurityEventType.TOKEN_REFRESHED
            ]
            assert len(refresh_events) == 1
            assert refresh_events[0].args[0].request_id == "req-success"

    async def test_refresh_token_already_consumed(
        self,
        session_service: SessionService,
        mock_device_session_repo: MagicMock,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        now = datetime.now(UTC)
        family = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=uuid.uuid4(),
            token_hash="fake-hashed-token",
            consumed_at=now - timedelta(minutes=5),
        )
        mock_refresh_token_family_repo.get_by_token_hash.return_value = family

        with patch("app.auth.sessions.security_event_emitter.emit") as emit_mock:
            with pytest.raises(RefreshTokenReusedError):
                await session_service.refresh_session("replayed-token")

            # Verify SESSION_REUSE_DETECTED was emitted
            reuse_calls = [
                c
                for c in emit_mock.call_args_list
                if c.args[0].event_type == SecurityEventType.SESSION_REUSE_DETECTED
            ]
            assert len(reuse_calls) == 1
            event = reuse_calls[0].args[0]
            assert event.session_id == family.device_session_id
            assert event.reason == "Consumed refresh token was presented"

        # Verify revoke_all_for_user was NOT called (idempotency rule)
        mock_device_session_repo.revoke_all_for_user.assert_not_called()

    async def test_refresh_session_already_revoked(
        self,
        session_service: SessionService,
        mock_device_session_repo: MagicMock,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        now = datetime.now(UTC)
        family = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=uuid.uuid4(),
            token_hash="fake-hashed-token",
            consumed_at=None,
        )
        mock_refresh_token_family_repo.get_by_token_hash.return_value = family

        revoked_session = DeviceSession(
            id=family.device_session_id,
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            current_refresh_token_hash="fake-hashed-token",
            revoked_at=now - timedelta(hours=1),
        )
        mock_device_session_repo.get_by_refresh_token_hash.return_value = revoked_session

        with patch("app.auth.sessions.security_event_emitter.emit") as emit_mock:
            with pytest.raises(RefreshTokenReusedError):
                await session_service.refresh_session("stolen-token")

            # Verify both repositories used locking
            family_call = mock_refresh_token_family_repo.get_by_token_hash.call_args
            session_call = mock_device_session_repo.get_by_refresh_token_hash.call_args
            assert family_call.kwargs["for_update"] is True
            assert session_call.kwargs["for_update"] is True

            # Verify revoke_all_for_user was called
            mock_device_session_repo.revoke_all_for_user.assert_called_once_with(
                revoked_session.user_id, revoked_at=ANY
            )

            # Verify events
            reuse_calls = [
                c
                for c in emit_mock.call_args_list
                if c.args[0].event_type == SecurityEventType.SESSION_REUSE_DETECTED
            ]
            assert len(reuse_calls) == 1
            revoke_calls = [
                c
                for c in emit_mock.call_args_list
                if c.args[0].event_type == SecurityEventType.ALL_SESSIONS_REVOKED
            ]
            assert len(revoke_calls) == 1

    async def test_refresh_absolute_timeout(
        self,
        session_service: SessionService,
        mock_device_session_repo: MagicMock,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        now = datetime.now(UTC)
        family = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=uuid.uuid4(),
            token_hash="fake-hashed-token",
            consumed_at=None,
        )
        mock_refresh_token_family_repo.get_by_token_hash.return_value = family

        expired_session = DeviceSession(
            id=family.device_session_id,
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            current_refresh_token_hash="fake-hashed-token",
            revoked_at=None,
            expires_at=now - timedelta(days=1),
            last_active_at=now,
        )
        mock_device_session_repo.get_by_refresh_token_hash.return_value = expired_session

        with patch("app.auth.sessions.security_event_emitter.emit") as emit_mock:
            with pytest.raises(SessionExpiredError, match="Absolute timeout exceeded"):
                await session_service.refresh_session("expired-token")

            # Verify session was revoked
            mock_device_session_repo.revoke.assert_called_once_with(
                expired_session.id, revoked_at=ANY
            )

            # Verify SESSION_EXPIRED event
            expired_events = [
                c
                for c in emit_mock.call_args_list
                if c.args[0].event_type == SecurityEventType.SESSION_EXPIRED
            ]
            assert len(expired_events) == 1
            assert expired_events[0].args[0].reason == "Absolute timeout exceeded"

    async def test_refresh_idle_timeout(
        self,
        session_service: SessionService,
        mock_device_session_repo: MagicMock,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        now = datetime.now(UTC)
        family = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=uuid.uuid4(),
            token_hash="fake-hashed-token",
            consumed_at=None,
        )
        mock_refresh_token_family_repo.get_by_token_hash.return_value = family

        idle_session = DeviceSession(
            id=family.device_session_id,
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            current_refresh_token_hash="fake-hashed-token",
            revoked_at=None,
            expires_at=now + timedelta(days=30),
            last_active_at=now - timedelta(hours=9),
        )
        mock_device_session_repo.get_by_refresh_token_hash.return_value = idle_session

        with patch("app.auth.sessions.security_event_emitter.emit") as emit_mock:
            with pytest.raises(SessionExpiredError, match="Idle timeout exceeded"):
                await session_service.refresh_session("idle-token")

            mock_device_session_repo.revoke.assert_called_once_with(idle_session.id, revoked_at=ANY)

            expired_events = [
                c
                for c in emit_mock.call_args_list
                if c.args[0].event_type == SecurityEventType.SESSION_EXPIRED
            ]
            assert len(expired_events) == 1
            assert expired_events[0].args[0].reason == "Idle timeout exceeded"

    async def test_refresh_successful_rotation(
        self,
        session_service: SessionService,
        mock_device_session_repo: MagicMock,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        now = datetime.now(UTC)
        family = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=uuid.uuid4(),
            token_hash="fake-hashed-token",
            consumed_at=None,
        )
        mock_refresh_token_family_repo.get_by_token_hash.return_value = family

        valid_session = DeviceSession(
            id=family.device_session_id,
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            current_refresh_token_hash="fake-hashed-token",
            revoked_at=None,
            expires_at=now + timedelta(days=30),
            last_active_at=now,
        )
        mock_device_session_repo.get_by_refresh_token_hash.return_value = valid_session

        # update() returns the updated session (same ID)
        mock_device_session_repo.update.return_value = valid_session

        with patch("app.auth.sessions.security_event_emitter.emit") as emit_mock:
            new_session, new_token_pair = await session_service.refresh_session(
                "valid-token",
                ip_address="10.0.0.1",
                user_agent="NewAgent",
            )

            # DeviceSession identity is stable (same ID)
            assert new_session.id == valid_session.id
            assert new_token_pair.plaintext == "fake-plaintext-token"

            # 1. Old family marked consumed
            mock_refresh_token_family_repo.mark_consumed.assert_called_once_with(
                family.id, consumed_at=ANY
            )

            # 2. Existing DeviceSession updated in-place
            mock_device_session_repo.update.assert_called_once_with(
                valid_session.id,
                current_refresh_token_hash="fake-hashed-token",
                last_active_at=ANY,
            )

            # 3. New family epoch created
            mock_refresh_token_family_repo.create_epoch.assert_called_once_with(
                device_session_id=valid_session.id,
                token_hash="fake-hashed-token",
            )

            # 4. Both repositories used locking
            family_call = mock_refresh_token_family_repo.get_by_token_hash.call_args
            session_call = mock_device_session_repo.get_by_refresh_token_hash.call_args
            assert family_call.kwargs["for_update"] is True
            assert session_call.kwargs["for_update"] is True

            # 5. TOKEN_REFRESHED event emitted
            refresh_events = [
                c
                for c in emit_mock.call_args_list
                if c.args[0].event_type == SecurityEventType.TOKEN_REFRESHED
            ]
            assert len(refresh_events) == 1
            assert refresh_events[0].args[0].session_id == valid_session.id
            assert refresh_events[0].args[0].metadata == {
                "previous_session_id": str(valid_session.id)
            }

    async def test_refresh_preserves_device_session_id(
        self,
        session_service: SessionService,
        mock_device_session_repo: MagicMock,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        now = datetime.now(UTC)
        family = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=uuid.uuid4(),
            token_hash="fake-hashed-token",
            consumed_at=None,
        )
        mock_refresh_token_family_repo.get_by_token_hash.return_value = family

        valid_session = DeviceSession(
            id=family.device_session_id,
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            current_refresh_token_hash="fake-hashed-token",
            revoked_at=None,
            expires_at=now + timedelta(days=30),
            last_active_at=now,
        )
        mock_device_session_repo.get_by_refresh_token_hash.return_value = valid_session
        mock_device_session_repo.update.return_value = valid_session

        new_session, _ = await session_service.refresh_session("valid-token")

        assert new_session.id == valid_session.id
        assert new_session.id == family.device_session_id

    async def test_refresh_updates_current_refresh_token_hash(
        self,
        session_service: SessionService,
        mock_device_session_repo: MagicMock,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        now = datetime.now(UTC)
        family = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=uuid.uuid4(),
            token_hash="old-hash",
            consumed_at=None,
        )
        mock_refresh_token_family_repo.get_by_token_hash.return_value = family

        valid_session = DeviceSession(
            id=family.device_session_id,
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            current_refresh_token_hash="old-hash",
            revoked_at=None,
            expires_at=now + timedelta(days=30),
            last_active_at=now - timedelta(hours=1),
        )
        mock_device_session_repo.get_by_refresh_token_hash.return_value = valid_session
        mock_device_session_repo.update.return_value = valid_session

        _, new_token_pair = await session_service.refresh_session("valid-token")

        mock_device_session_repo.update.assert_called_once_with(
            valid_session.id,
            current_refresh_token_hash=new_token_pair.hash_val,
            last_active_at=ANY,
        )

    async def test_refresh_updates_last_active_at(
        self,
        session_service: SessionService,
        mock_device_session_repo: MagicMock,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        now = datetime.now(UTC)
        family = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=uuid.uuid4(),
            token_hash="fake-hashed-token",
            consumed_at=None,
        )
        mock_refresh_token_family_repo.get_by_token_hash.return_value = family

        valid_session = DeviceSession(
            id=family.device_session_id,
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            current_refresh_token_hash="fake-hashed-token",
            revoked_at=None,
            expires_at=now + timedelta(days=30),
            last_active_at=now - timedelta(hours=1),
        )
        mock_device_session_repo.get_by_refresh_token_hash.return_value = valid_session
        mock_device_session_repo.update.return_value = valid_session

        _, _ = await session_service.refresh_session("valid-token")

        update_kwargs = mock_device_session_repo.update.call_args.kwargs
        assert "last_active_at" in update_kwargs
        assert update_kwargs["last_active_at"] >= now - timedelta(seconds=5)

    async def test_refresh_creates_new_family_epoch(
        self,
        session_service: SessionService,
        mock_device_session_repo: MagicMock,
        mock_refresh_token_family_repo: MagicMock,
    ) -> None:
        now = datetime.now(UTC)
        family = RefreshTokenFamily(
            id=uuid.uuid4(),
            device_session_id=uuid.uuid4(),
            token_hash="fake-hashed-token",
            consumed_at=None,
        )
        mock_refresh_token_family_repo.get_by_token_hash.return_value = family

        valid_session = DeviceSession(
            id=family.device_session_id,
            user_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            current_refresh_token_hash="fake-hashed-token",
            revoked_at=None,
            expires_at=now + timedelta(days=30),
            last_active_at=now,
        )
        mock_device_session_repo.get_by_refresh_token_hash.return_value = valid_session
        mock_device_session_repo.update.return_value = valid_session

        _, new_token_pair = await session_service.refresh_session("valid-token")

        mock_refresh_token_family_repo.create_epoch.assert_called_once_with(
            device_session_id=valid_session.id,
            token_hash=new_token_pair.hash_val,
        )
