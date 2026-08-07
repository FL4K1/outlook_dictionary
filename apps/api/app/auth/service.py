"""Provider-agnostic authentication orchestration service.

PR-1.2 owns platform authentication core behavior only: issuing access
credentials for already-established platform sessions, refreshing sessions,
and revoking sessions. External IdP callbacks, user provisioning, and identity
linking are intentionally deferred to PR-1.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.auth.events import (
    SecurityEvent,
    SecurityEventType,
    SecurityOutcome,
    security_event_emitter,
)
from app.auth.tokens import AccessTokenSubject

if TYPE_CHECKING:
    import uuid

    from app.auth.sessions import SessionService
    from app.auth.tokens import TokenService
    from mip_models.auth import Session


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    """Result returned when platform tokens are issued."""

    access_token: str
    refresh_token: str
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID


class AuthenticationService:
    """Provider-agnostic orchestration for platform sessions and tokens.

    This service deliberately does not know how Microsoft, Google, Okta, or
    another provider authenticates a user. PR-1.3 provider adapters will call
    into this service after they have resolved a valid platform user, tenant,
    and organization.
    """

    def __init__(
        self,
        session_service: SessionService,
        token_service: TokenService,
    ) -> None:
        self.session_service = session_service
        self.token_service = token_service

    async def create_session_tokens(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        organization_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
        remember_me: bool = False,
    ) -> AuthenticationResult:
        """Create a platform session and issue access/refresh tokens.

        Provider-specific login flows are responsible for resolving the IDs
        passed here. This method only establishes platform session state.
        """
        session, refresh_token = await self.session_service.create_session(
            user_id=user_id,
            tenant_id=tenant_id,
            ip_address=ip_address,
            user_agent=user_agent,
            remember_me=remember_me,
        )
        access_token = self._create_access_token(
            user_id=user_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            session=session,
        )

        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.LOGIN_SUCCEEDED,
                outcome=SecurityOutcome.SUCCESS,
                user_id=user_id,
                tenant_id=tenant_id,
                session_id=session.id,
                ip_address=ip_address,
                user_agent=user_agent,
            )
        )

        return AuthenticationResult(
            access_token=access_token,
            refresh_token=refresh_token.plaintext,
            user_id=user_id,
            tenant_id=tenant_id,
            organization_id=organization_id,
            session_id=session.id,
        )

    async def refresh_session_tokens(
        self,
        plaintext_refresh_token: str,
        organization_id: uuid.UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuthenticationResult:
        """Rotate a refresh token and issue a new access token."""
        session, refresh_token = await self.session_service.refresh_session(
            plaintext_refresh_token=plaintext_refresh_token,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        access_token = self._create_access_token(
            user_id=session.user_id,
            tenant_id=session.tenant_id,
            organization_id=organization_id,
            session=session,
        )

        return AuthenticationResult(
            access_token=access_token,
            refresh_token=refresh_token.plaintext,
            user_id=session.user_id,
            tenant_id=session.tenant_id,
            organization_id=organization_id,
            session_id=session.id,
        )

    def _create_access_token(
        self,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        organization_id: uuid.UUID,
        session: Session,
    ) -> str:
        return self.token_service.create_access_token(
            AccessTokenSubject(
                user_id=user_id,
                tenant_id=tenant_id,
                organization_id=organization_id,
                session_id=session.id,
            )
        )
