"""Security event pipeline — centralized security audit logging (ADR-012).

Every authentication, authorization, and session lifecycle event flows
through this pipeline. Events are structured, typed, and emitted via
structlog so they can be routed to:

- Application logs (structured JSON in production)
- Future: dedicated audit log table (AuditLog model)
- Future: SIEM / external security event collectors

Security events NEVER contain:
- Raw tokens or secrets
- Email content or subject lines
- Personally identifiable information beyond user/tenant IDs

Events ALWAYS contain:
- Event type
- Timestamp
- User ID (if available)
- Tenant ID (if available)
- Session ID (if available)
- IP address
- User agent
- Request ID (from middleware)
- Outcome (success / failure)
- Reason (for failures)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum, unique

from app.common.logging import get_logger

_security_logger = get_logger("security")


@unique
class SecurityEventType(StrEnum):
    """Exhaustive catalogue of security-relevant events.

    Grouped by subsystem for clarity. Each event maps to a specific
    point in the authentication/authorization lifecycle.
    """

    # --- Authentication ---
    LOGIN_STARTED = "login_started"
    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    CALLBACK_RECEIVED = "callback_received"
    CALLBACK_FAILED = "callback_failed"

    # --- Token ---
    TOKEN_VALIDATED = "token_validated"  # noqa: S105
    TOKEN_ISSUED = "token_issued"  # noqa: S105
    TOKEN_REFRESHED = "token_refreshed"  # noqa: S105
    TOKEN_REFRESH_FAILED = "token_refresh_failed"  # noqa: S105
    TOKEN_EXPIRED = "token_expired"  # noqa: S105
    TOKEN_INVALID = "token_invalid"  # noqa: S105

    # --- Session ---
    SESSION_CREATED = "session_created"
    SESSION_REVOKED = "session_revoked"
    SESSION_EXPIRED = "session_expired"
    SESSION_REUSE_DETECTED = "session_reuse_detected"
    ALL_SESSIONS_REVOKED = "all_sessions_revoked"

    # --- Authorization ---
    PERMISSION_DENIED = "permission_denied"
    AUTHORIZATION_SUCCESS = "authorization_success"
    AUTHORIZATION_FAILURE = "authorization_failure"
    ROLE_ESCALATION_BLOCKED = "role_escalation_blocked"

    # --- Account ---
    USER_PROVISIONED = "user_provisioned"
    IDENTITY_LINKED = "identity_linked"

    # --- API Key ---
    API_KEY_AUTHENTICATED = "api_key_authenticated"
    API_KEY_INVALID = "api_key_invalid"
    API_KEY_REVOKED = "api_key_revoked"


@unique
class SecurityOutcome(StrEnum):
    """Whether the security event represents a success or failure."""

    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class SecurityEvent:
    """An immutable, structured security event.

    Every security-relevant action produces one of these. The emitter
    serializes it to structured logs and (in future) to the audit table.

    Attributes:
        event_type: The specific type of security event.
        outcome: Whether the event was successful or a failure.
        user_id: The user involved, if known.
        tenant_id: The tenant context, if known.
        session_id: The session involved, if known.
        ip_address: Client IP address from the request.
        user_agent: Client user agent string.
        reason: Human-readable reason, primarily for failures.
        metadata: Additional structured context (provider name, etc.).
        timestamp: When the event occurred (defaults to UTC now).
        event_id: Unique identifier for this event instance.
    """

    event_type: SecurityEventType
    outcome: SecurityOutcome
    user_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    reason: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    request_id: str | None = None

    def to_log_dict(self) -> dict[str, object]:
        """Serialize the event to a flat dictionary for structured logging.

        Returns only non-None values to keep log lines clean.
        """
        data: dict[str, object] = {
            "event_id": str(self.event_id),
            "event_type": self.event_type.value,
            "outcome": self.outcome.value,
            "timestamp": self.timestamp.isoformat(),
        }
        if self.user_id is not None:
            data["user_id"] = str(self.user_id)
        if self.tenant_id is not None:
            data["tenant_id"] = str(self.tenant_id)
        if self.session_id is not None:
            data["session_id"] = str(self.session_id)
        if self.ip_address is not None:
            data["ip_address"] = self.ip_address
        if self.user_agent is not None:
            data["user_agent"] = self.user_agent
        if self.reason is not None:
            data["reason"] = self.reason
        if self.request_id is not None:
            data["request_id"] = self.request_id
        if self.metadata:
            data["metadata"] = self.metadata
        return data


class SecurityEventEmitter:
    """Centralized emitter for security events (ADR-012).

    Currently emits to structured logs. Designed to be extended with
    additional sinks (audit table, SIEM) without changing callers.

    Usage::

        emitter = SecurityEventEmitter()
        emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.LOGIN_SUCCEEDED,
                outcome=SecurityOutcome.SUCCESS,
                user_id=user.id,
                tenant_id=tenant.id,
            )
        )
    """

    def emit(self, event: SecurityEvent) -> None:
        """Emit a security event to all configured sinks.

        Currently: structured log only.
        Future: audit table write, SIEM forwarding.
        """
        log_data = event.to_log_dict()

        if event.outcome == SecurityOutcome.FAILURE:
            _security_logger.warning("security_event", **log_data)
        else:
            _security_logger.info("security_event", **log_data)


# Module-level singleton — used across the auth subsystem
security_event_emitter = SecurityEventEmitter()
