"""Session management service.

Handles stateful session lifecycle, refresh rotation, revocation, idle timeout,
absolute timeout, and refresh token reuse detection.

SessionService is the single owner of session lifecycle behavior. Repositories
expose persistence primitives only. All business rules, transaction orchestration,
timeout decisions, replay detection, and event emission live here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from app.auth.events import (
    SecurityEvent,
    SecurityEventType,
    SecurityOutcome,
    security_event_emitter,
)
from app.auth.exceptions import (
    RefreshTokenReusedError,
    SessionExpiredError,
    TokenInvalidError,
)
from app.common.logging import get_logger

if TYPE_CHECKING:
    import uuid

    from app.auth.tokens import RefreshTokenPair, TokenService
    from app.common.config import Settings
    from app.repositories.auth import (
        DeviceSessionRepository,
        RefreshTokenFamilyRepository,
    )
    from mip_models.auth import DeviceSession

logger = get_logger(__name__)


class SessionService:
    """Manages stateful database sessions and refresh token rotation.

    All public methods correspond to business capabilities defined in the
    PR-1.2.3 Implementation Contract. Private helpers encapsulate reusable
    internal logic.
    """

    def __init__(
        self,
        device_session_repo: DeviceSessionRepository,
        refresh_token_family_repo: RefreshTokenFamilyRepository,
        token_service: TokenService,
        settings: Settings,
    ) -> None:
        self.device_session_repo = device_session_repo
        self.refresh_token_family_repo = refresh_token_family_repo
        self.token_service = token_service
        self.settings = settings

    async def create_session(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
        remember_me: bool = False,
    ) -> tuple[DeviceSession, RefreshTokenPair]:
        """Create a new device session and issue a refresh token.

        Transaction scope: single transaction creating the DeviceSession
        and its initial RefreshTokenFamily epoch.

        Args:
            user_id: The platform user UUID.
            tenant_id: The tenant UUID.
            ip_address: Optional client IP address.
            user_agent: Optional client user agent string.
            remember_me: If True, use the extended absolute timeout.

        Returns:
            Tuple of (DeviceSession, RefreshTokenPair). The plaintext
            refresh token is returned once and must be stored securely
            by the caller.
        """
        now = datetime.now(UTC)
        absolute_days = (
            self.settings.session_remember_me_days
            if remember_me
            else self.settings.session_absolute_timeout_days
        )
        expires_at = now + timedelta(days=absolute_days)

        token_pair = self.token_service.generate_refresh_token()

        # Single transaction: create DeviceSession + create initial RefreshTokenFamily epoch.
        session = await self.device_session_repo.create(
            user_id=user_id,
            tenant_id=tenant_id,
            current_refresh_token_hash=token_pair.hash_val,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at,
            last_active_at=now,
        )

        await self.refresh_token_family_repo.create_epoch(
            device_session_id=session.id,
            token_hash=token_pair.hash_val,
        )

        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.SESSION_CREATED,
                outcome=SecurityOutcome.SUCCESS,
                user_id=user_id,
                tenant_id=tenant_id,
                session_id=session.id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        )

        return session, token_pair

    async def refresh_session(
        self,
        plaintext_refresh_token: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[DeviceSession, RefreshTokenPair]:
        """Rotate a refresh token and return the next session epoch.

        Concurrency control: SELECT FOR UPDATE is used on both the
        RefreshTokenFamily row and the parent DeviceSession row within
        a single database transaction.

        Locking rationale:
        - RefreshTokenFamily lock: Prevents two concurrent refresh requests
          from both marking the same token epoch as unconsumed (TM-015).
          The first request to acquire the lock succeeds; the second finds
          the epoch already consumed and raises RefreshTokenReusedError.
        - DeviceSession lock: Prevents concurrent refresh requests from
          both revoking and replacing the same session (TM-015).

        Both locks are released when the transaction commits or rolls back.

        Args:
            plaintext_refresh_token: The opaque refresh token presented
                by the client.
            ip_address: Optional client IP address.
            user_agent: Optional client user agent string.

        Returns:
            Tuple of (DeviceSession, RefreshTokenPair). The existing
            DeviceSession is updated in-place with a new refresh token;
            its identity remains stable across refreshes.

        Raises:
            TokenInvalidError: The refresh token hash does not map to any
                known RefreshTokenFamily epoch.
            RefreshTokenReusedError: The token epoch is already consumed,
                or the parent session has been revoked.
            SessionExpiredError: The session has exceeded its absolute or
                idle timeout.
        """
        now = datetime.now(UTC)
        hash_val = self.token_service.hash_refresh_token(plaintext_refresh_token)

        # Lock the RefreshTokenFamily row first.
        # Why: Prevents concurrent refresh race conditions (TM-015).
        # Lock released: At transaction commit/rollback.
        family = await self.refresh_token_family_repo.get_by_token_hash(hash_val, for_update=True)
        if family is None:
            raise TokenInvalidError("Invalid refresh token.")

        # If the family epoch is already consumed, this token has been
        # rotated or replayed. Fail fast without triggering session-wide
        # revocation (idempotency rule: Section 10.1).
        if family.consumed_at is not None:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.SESSION_REUSE_DETECTED,
                    outcome=SecurityOutcome.FAILURE,
                    session_id=family.device_session_id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    reason="Consumed refresh token was presented",
                )
            )
            raise RefreshTokenReusedError()

        # Lock the parent DeviceSession row.
        # Why: Prevents concurrent refresh race conditions (TM-015).
        # Lock released: At transaction commit/rollback.
        session = await self.device_session_repo.get_by_refresh_token_hash(
            hash_val, for_update=True
        )
        if session is None:
            raise TokenInvalidError("Invalid refresh token.")

        if session.revoked_at is not None:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.SESSION_REUSE_DETECTED,
                    outcome=SecurityOutcome.FAILURE,
                    user_id=session.user_id,
                    tenant_id=session.tenant_id,
                    session_id=session.id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    reason="Revoked session refresh token was presented",
                )
            )
            await self.device_session_repo.revoke_all_for_user(session.user_id, revoked_at=now)
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.ALL_SESSIONS_REVOKED,
                    outcome=SecurityOutcome.SUCCESS,
                    user_id=session.user_id,
                    tenant_id=session.tenant_id,
                    reason="Compromised refresh token detected",
                )
            )
            raise RefreshTokenReusedError()

        if session.expires_at < now:
            await self._revoke_and_raise_expired(session, "Absolute timeout exceeded")

        idle_limit = now - timedelta(hours=self.settings.session_idle_timeout_hours)
        if session.last_active_at < idle_limit:
            await self._revoke_and_raise_expired(session, "Idle timeout exceeded")

        new_token_pair = self.token_service.generate_refresh_token()

        # Atomic transaction: mark old family consumed + update existing DeviceSession
        # in-place + create new family epoch.
        # Caller MUST ensure these operations execute within a single
        # database transaction (Implementation Contract Section 9).
        await self.refresh_token_family_repo.mark_consumed(family.id, consumed_at=now)
        session = cast(
            "DeviceSession",
            await self.device_session_repo.update(
                session.id,
                current_refresh_token_hash=new_token_pair.hash_val,
                last_active_at=now,
            ),
        )

        await self.refresh_token_family_repo.create_epoch(
            device_session_id=session.id,
            token_hash=new_token_pair.hash_val,
        )

        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.TOKEN_REFRESHED,
                outcome=SecurityOutcome.SUCCESS,
                user_id=session.user_id,
                tenant_id=session.tenant_id,
                session_id=session.id,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata={"previous_session_id": str(session.id)},
            )
        )

        return session, new_token_pair

    async def _revoke_and_raise_expired(self, session: DeviceSession, reason: str) -> None:
        """Revoke an expired session and raise a domain error.

        This is a private helper. It emits SESSION_EXPIRED and revokes
        the session atomically within the caller's transaction scope.

        Args:
            session: The expired DeviceSession.
            reason: Human-readable explanation for the audit log.

        Raises:
            SessionExpiredError: Always raised after revocation.
        """
        now = datetime.now(UTC)
        await self.device_session_repo.revoke(session.id, revoked_at=now)
        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.SESSION_EXPIRED,
                outcome=SecurityOutcome.SUCCESS,
                user_id=session.user_id,
                tenant_id=session.tenant_id,
                session_id=session.id,
                reason=reason,
            )
        )
        raise SessionExpiredError(reason)
