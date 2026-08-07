"""Unit tests for the SecurityEvent pipeline (ADR-012)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.auth.events import (
    SecurityEvent,
    SecurityEventEmitter,
    SecurityEventType,
    SecurityOutcome,
)

# ---------------------------------------------------------------------------
# SecurityEvent Immutability
# ---------------------------------------------------------------------------


class TestSecurityEventImmutability:
    """Verify that SecurityEvent instances are immutable."""

    def test_cannot_modify_event_type(self) -> None:
        event = SecurityEvent(
            event_type=SecurityEventType.LOGIN_SUCCEEDED,
            outcome=SecurityOutcome.SUCCESS,
        )
        with pytest.raises(AttributeError, match="cannot assign"):
            event.event_type = SecurityEventType.LOGIN_FAILED  # type: ignore[misc]

    def test_cannot_modify_outcome(self) -> None:
        event = SecurityEvent(
            event_type=SecurityEventType.LOGIN_SUCCEEDED,
            outcome=SecurityOutcome.SUCCESS,
        )
        with pytest.raises(AttributeError, match="cannot assign"):
            event.outcome = SecurityOutcome.FAILURE  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SecurityEvent Serialization
# ---------------------------------------------------------------------------


class TestSecurityEventSerialization:
    """Verify to_log_dict produces correct structured output."""

    def test_minimal_event_serialization(self) -> None:
        event = SecurityEvent(
            event_type=SecurityEventType.LOGIN_STARTED,
            outcome=SecurityOutcome.SUCCESS,
        )
        log = event.to_log_dict()

        assert log["event_type"] == "login_started"
        assert log["outcome"] == "success"
        assert "event_id" in log
        assert "timestamp" in log
        # Optional fields should NOT be present
        assert "user_id" not in log
        assert "tenant_id" not in log
        assert "session_id" not in log
        assert "ip_address" not in log
        assert "reason" not in log

    def test_full_event_serialization(self) -> None:
        user_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        session_id = uuid.uuid4()
        ts = datetime(2026, 7, 27, 12, 0, 0, tzinfo=UTC)

        event = SecurityEvent(
            event_type=SecurityEventType.SESSION_REUSE_DETECTED,
            outcome=SecurityOutcome.FAILURE,
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            ip_address="192.168.1.100",
            user_agent="Mozilla/5.0",
            reason="Refresh token presented after rotation",
            metadata={"provider": "microsoft"},
            timestamp=ts,
        )
        log = event.to_log_dict()

        assert log["event_type"] == "session_reuse_detected"
        assert log["outcome"] == "failure"
        assert log["user_id"] == str(user_id)
        assert log["tenant_id"] == str(tenant_id)
        assert log["session_id"] == str(session_id)
        assert log["ip_address"] == "192.168.1.100"
        assert log["user_agent"] == "Mozilla/5.0"
        assert log["reason"] == "Refresh token presented after rotation"
        assert log["metadata"] == {"provider": "microsoft"}
        assert log["timestamp"] == "2026-07-27T12:00:00+00:00"

    def test_empty_metadata_is_excluded(self) -> None:
        event = SecurityEvent(
            event_type=SecurityEventType.TOKEN_ISSUED,
            outcome=SecurityOutcome.SUCCESS,
        )
        log = event.to_log_dict()
        assert "metadata" not in log


# ---------------------------------------------------------------------------
# SecurityEvent Default Values
# ---------------------------------------------------------------------------


class TestSecurityEventDefaults:
    """Verify correct default values on construction."""

    def test_event_id_is_generated(self) -> None:
        event = SecurityEvent(
            event_type=SecurityEventType.LOGIN_STARTED,
            outcome=SecurityOutcome.SUCCESS,
        )
        assert isinstance(event.event_id, uuid.UUID)

    def test_timestamp_is_utc(self) -> None:
        event = SecurityEvent(
            event_type=SecurityEventType.LOGIN_STARTED,
            outcome=SecurityOutcome.SUCCESS,
        )
        assert event.timestamp.tzinfo is not None

    def test_each_event_gets_unique_id(self) -> None:
        e1 = SecurityEvent(
            event_type=SecurityEventType.LOGIN_STARTED,
            outcome=SecurityOutcome.SUCCESS,
        )
        e2 = SecurityEvent(
            event_type=SecurityEventType.LOGIN_STARTED,
            outcome=SecurityOutcome.SUCCESS,
        )
        assert e1.event_id != e2.event_id


# ---------------------------------------------------------------------------
# SecurityEventEmitter
# ---------------------------------------------------------------------------


class TestSecurityEventEmitter:
    """Verify the emitter routes events to the correct log level."""

    @patch("app.auth.events._security_logger")
    def test_success_events_use_info_level(self, mock_logger: MagicMock) -> None:
        emitter = SecurityEventEmitter()
        event = SecurityEvent(
            event_type=SecurityEventType.LOGIN_SUCCEEDED,
            outcome=SecurityOutcome.SUCCESS,
            user_id=uuid.uuid4(),
        )
        emitter.emit(event)
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args
        assert call_args[0][0] == "security_event"

    @patch("app.auth.events._security_logger")
    def test_failure_events_use_warning_level(self, mock_logger: MagicMock) -> None:
        emitter = SecurityEventEmitter()
        event = SecurityEvent(
            event_type=SecurityEventType.LOGIN_FAILED,
            outcome=SecurityOutcome.FAILURE,
            reason="Invalid authorization code",
        )
        emitter.emit(event)
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert call_args[0][0] == "security_event"

    @patch("app.auth.events._security_logger")
    def test_reuse_detection_emits_warning(self, mock_logger: MagicMock) -> None:
        emitter = SecurityEventEmitter()
        event = SecurityEvent(
            event_type=SecurityEventType.SESSION_REUSE_DETECTED,
            outcome=SecurityOutcome.FAILURE,
            user_id=uuid.uuid4(),
            reason="Stolen refresh token reused",
        )
        emitter.emit(event)
        mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# SecurityEventType Exhaustiveness
# ---------------------------------------------------------------------------


class TestSecurityEventTypeCoverage:
    """Verify the event type enum has expected members."""

    def test_all_auth_events_exist(self) -> None:
        assert SecurityEventType.LOGIN_STARTED
        assert SecurityEventType.LOGIN_SUCCEEDED
        assert SecurityEventType.LOGIN_FAILED

    def test_all_token_events_exist(self) -> None:
        assert SecurityEventType.TOKEN_ISSUED
        assert SecurityEventType.TOKEN_REFRESHED
        assert SecurityEventType.TOKEN_REFRESH_FAILED
        assert SecurityEventType.TOKEN_EXPIRED
        assert SecurityEventType.TOKEN_INVALID

    def test_all_session_events_exist(self) -> None:
        assert SecurityEventType.SESSION_CREATED
        assert SecurityEventType.SESSION_REVOKED
        assert SecurityEventType.SESSION_EXPIRED
        assert SecurityEventType.SESSION_REUSE_DETECTED
        assert SecurityEventType.ALL_SESSIONS_REVOKED
