"""Session management service.

Handles stateful session lifecycle, refresh rotation, revocation, idle timeout,
absolute timeout, and refresh token reuse detection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

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
    from app.repositories.auth import SessionRepository
    from mip_models.auth import Session

logger = get_logger(__name__)


class SessionService:
    """Manages stateful database sessions and refresh token rotation."""

    def __init__(
        self,
        repo: SessionRepository,
        token_service: TokenService,
        settings: Settings,
    ) -> None:
        self.repo = repo
        self.token_service = token_service
        self.settings = settings

    async def create_session(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
        remember_me: bool = False,
    ) -> tuple[Session, RefreshTokenPair]:
        """Create a new session for a user."""
        now = datetime.now(UTC)
        absolute_days = (
            self.settings.session_remember_me_days
            if remember_me
            else self.settings.session_absolute_timeout_days
        )
        expires_at = now + timedelta(days=absolute_days)

        token_pair = self.token_service.generate_refresh_token()
        session = await self.repo.create(
            user_id=user_id,
            tenant_id=tenant_id,
            refresh_token_hash=token_pair.hash_val,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at,
            last_active_at=now,
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
    ) -> tuple[Session, RefreshTokenPair]:
        """Rotate a refresh token and return the next session epoch.

        The current schema preserves consumed refresh token hashes as revoked
        session rows. That keeps refresh-token reuse detection possible without
        introducing a token-history table in PR-1.2. A future migration can split
        user-visible device sessions from refresh-token epochs.
        """
        now = datetime.now(UTC)
        hash_val = self.token_service.hash_refresh_token(plaintext_refresh_token)

        old_session = await self.repo.get_by_refresh_token_hash(hash_val)
        if old_session is None:
            raise TokenInvalidError("Invalid refresh token.")

        if old_session.revoked_at is not None:
            await self.repo.revoke_all_for_user(old_session.user_id, revoked_at=now)
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.SESSION_REUSE_DETECTED,
                    outcome=SecurityOutcome.FAILURE,
                    user_id=old_session.user_id,
                    tenant_id=old_session.tenant_id,
                    session_id=old_session.id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    reason="Previously revoked refresh token was presented",
                )
            )
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.ALL_SESSIONS_REVOKED,
                    outcome=SecurityOutcome.SUCCESS,
                    user_id=old_session.user_id,
                    reason="Compromised refresh token detected",
                )
            )
            raise RefreshTokenReusedError()

        if old_session.expires_at < now:
            await self._revoke_and_raise_expired(old_session, "Absolute timeout exceeded")

        idle_limit = now - timedelta(hours=self.settings.session_idle_timeout_hours)
        if old_session.last_active_at < idle_limit:
            await self._revoke_and_raise_expired(old_session, "Idle timeout exceeded")

        await self.repo.revoke(old_session.id, revoked_at=now)
        new_token_pair = self.token_service.generate_refresh_token()
        new_session = await self.repo.create(
            user_id=old_session.user_id,
            tenant_id=old_session.tenant_id,
            refresh_token_hash=new_token_pair.hash_val,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=old_session.expires_at,
            last_active_at=now,
        )

        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.TOKEN_REFRESHED,
                outcome=SecurityOutcome.SUCCESS,
                user_id=new_session.user_id,
                tenant_id=new_session.tenant_id,
                session_id=new_session.id,
                ip_address=ip_address,
                user_agent=user_agent,
                metadata={"previous_session_id": str(old_session.id)},
            )
        )

        return new_session, new_token_pair

    async def _revoke_and_raise_expired(self, session: Session, reason: str) -> None:
        """Revoke an expired session and raise a domain error."""
        now = datetime.now(UTC)
        await self.repo.revoke(session.id, revoked_at=now)
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
