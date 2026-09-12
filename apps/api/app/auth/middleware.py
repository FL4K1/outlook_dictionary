"""Authentication middleware — JWT verification, session validation, and context injection.

AuthenticationMiddleware is the outermost application middleware. It:
1. Verifies the access token JWT.
2. Validates the backing DeviceSession.
3. Resolves tenant, membership, role, and permission state.
4. Attaches an immutable AuthenticationContext to request.state.
5. Emits security events for audit logging.

Database access is performed per-request using the application-scoped
session factory. No FastAPI dependency injection is used inside dispatch().
"""

from __future__ import annotations

import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.auth.context import AuthenticationContext
from app.auth.events import (
    SecurityEvent,
    SecurityEventType,
    SecurityOutcome,
    security_event_emitter,
)
from app.auth.exceptions import (
    AuthenticationError,
    InsufficientPermissionsError,
    SessionExpiredError,
    SessionRevokedError,
    TenantAccessDeniedError,
    TokenExpiredError,
    TokenInvalidError,
)
from app.auth.public_routes import is_public_route
from app.common.dependencies import get_session_factory
from app.common.logging import get_logger
from app.repositories.auth import DeviceSessionRepository
from app.repositories.core import MembershipRepository, TenantRepository
from mip_models.auth import Role
from mip_models.user import Identity

if TYPE_CHECKING:
    from fastapi import Request
    from starlette.responses import Response

    from app.auth.policy import PolicyEngine
    from app.auth.tokens import TokenService


logger = get_logger(__name__)


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Verify access tokens and inject AuthenticationContext into every request.

    Registered outermost in the middleware stack (after CORS, before
    RequestIdMiddleware). Reads X-Request-ID directly from headers for
    security events because RequestIdMiddleware has not yet populated
    request.state.request_id at this point in execution.
    """

    def __init__(
        self,
        app: RequestResponseEndpoint,
        policy_engine: PolicyEngine,
        token_service: TokenService,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.policy_engine = policy_engine
        self.token_service = token_service

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID")
        if request_id is None:
            request_id = str(uuid.uuid4())

        path = request.url.path
        method = request.method

        if is_public_route(method, path):
            return await call_next(request)

        factory = get_session_factory()
        if factory is None:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.TOKEN_INVALID,
                    outcome=SecurityOutcome.FAILURE,
                    reason="Authentication service unavailable",
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("User-Agent"),
                    metadata={"request_id": request_id},
                )
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error_code": "AUTHENTICATION_SERVICE_UNAVAILABLE",
                    "message": "Authentication service unavailable.",
                },
            )

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.TOKEN_INVALID,
                    outcome=SecurityOutcome.FAILURE,
                    reason="Missing or malformed Authorization header",
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("User-Agent"),
                    metadata={"request_id": request_id},
                )
            )
            raise AuthenticationError("Missing access token.")

        token = auth_header[7:]

        try:
            payload = self.token_service.verify_access_token(token)
        except TokenExpiredError:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.TOKEN_INVALID,
                    outcome=SecurityOutcome.FAILURE,
                    reason="Token expired",
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("User-Agent"),
                    metadata={"request_id": request_id},
                )
            )
            raise
        except TokenInvalidError:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.TOKEN_INVALID,
                    outcome=SecurityOutcome.FAILURE,
                    reason="Token invalid",
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("User-Agent"),
                    metadata={"request_id": request_id},
                )
            )
            raise

        jti = payload.get("jti")
        sid = payload.get("sid")
        tid = payload.get("tid")
        oid = payload.get("oid")
        sub = payload.get("sub")

        if not jti or not sid or not tid or not oid or not sub:
            raise TokenInvalidError("Token missing required claims.")

        user_id = uuid.UUID(sub)
        session_id = uuid.UUID(sid)
        tenant_id = uuid.UUID(tid)
        organization_id = uuid.UUID(oid)

        settings = request.app.state.settings
        session_gen = factory.get_session()
        session = await session_gen.__anext__()
        try:
            device_session_repo = DeviceSessionRepository(session)
            tenant_repo = TenantRepository(session)
            membership_repo = MembershipRepository(session)

            device_session = await device_session_repo.get(session_id)
            if device_session is None:
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.TOKEN_INVALID,
                        outcome=SecurityOutcome.FAILURE,
                        reason="Session not found",
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("User-Agent"),
                        metadata={"request_id": request_id},
                    )
                )
                raise TokenInvalidError("Session not found.")

            if device_session.user_id != user_id:
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.TOKEN_INVALID,
                        outcome=SecurityOutcome.FAILURE,
                        reason="Session user mismatch",
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("User-Agent"),
                        metadata={"request_id": request_id},
                    )
                )
                raise TokenInvalidError("Session user mismatch.")

            now = datetime.now(UTC)
            if device_session.revoked_at is not None:
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.SESSION_REVOKED,
                        outcome=SecurityOutcome.FAILURE,
                        user_id=device_session.user_id,
                        tenant_id=device_session.tenant_id,
                        session_id=device_session.id,
                        reason="Session has been revoked",
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("User-Agent"),
                        metadata={"request_id": request_id},
                    )
                )
                raise SessionRevokedError()

            if device_session.expires_at < now:
                await device_session_repo.revoke(device_session.id, revoked_at=now)
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.SESSION_EXPIRED,
                        outcome=SecurityOutcome.FAILURE,
                        user_id=device_session.user_id,
                        tenant_id=device_session.tenant_id,
                        session_id=device_session.id,
                        reason="Absolute timeout exceeded",
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("User-Agent"),
                        metadata={"request_id": request_id},
                    )
                )
                raise SessionExpiredError("Session expired.")

            idle_limit = now - timedelta(hours=settings.session_idle_timeout_hours)
            if device_session.last_active_at < idle_limit:
                await device_session_repo.revoke(device_session.id, revoked_at=now)
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.SESSION_EXPIRED,
                        outcome=SecurityOutcome.FAILURE,
                        user_id=device_session.user_id,
                        tenant_id=device_session.tenant_id,
                        session_id=device_session.id,
                        reason="Idle timeout exceeded",
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("User-Agent"),
                        metadata={"request_id": request_id},
                    )
                )
                raise SessionExpiredError("Session idle timeout exceeded.")

            if device_session.tenant_id != tenant_id:
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.PERMISSION_DENIED,
                        outcome=SecurityOutcome.FAILURE,
                        user_id=device_session.user_id,
                        tenant_id=tenant_id,
                        session_id=device_session.id,
                        reason="Tenant mismatch",
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("User-Agent"),
                        metadata={"request_id": request_id},
                    )
                )
                raise TenantAccessDeniedError()

            tenant = await tenant_repo.get(tenant_id)
            if tenant is None or not tenant.is_active:
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.PERMISSION_DENIED,
                        outcome=SecurityOutcome.FAILURE,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        reason="Tenant not found or inactive",
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("User-Agent"),
                        metadata={"request_id": request_id},
                    )
                )
                raise TenantAccessDeniedError()

            if tenant.organization_id != organization_id:
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.PERMISSION_DENIED,
                        outcome=SecurityOutcome.FAILURE,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        reason="Organization mismatch",
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("User-Agent"),
                        metadata={"request_id": request_id},
                    )
                )
                raise TenantAccessDeniedError()

            membership = await membership_repo.get_by_user_and_tenant(user_id, tenant_id)
            if membership is None or not membership.is_active:
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.PERMISSION_DENIED,
                        outcome=SecurityOutcome.FAILURE,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        reason="No active membership",
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("User-Agent"),
                        metadata={"request_id": request_id},
                    )
                )
                raise TenantAccessDeniedError()

            # --- AD-PR13-010: Derive provider from persisted DeviceSession → Identity ---
            if device_session.identity_id is not None:
                identity_stmt = select(Identity).where(Identity.id == device_session.identity_id)
                identity_result = await session.execute(identity_stmt)
                identity = identity_result.scalars().first()
                if identity is None:
                    security_event_emitter.emit(
                        SecurityEvent(
                            event_type=SecurityEventType.TOKEN_INVALID,
                            outcome=SecurityOutcome.FAILURE,
                            reason="Session identity not found",
                            user_id=user_id,
                            tenant_id=tenant_id,
                            session_id=device_session.id,
                            ip_address=request.client.host if request.client else None,
                            user_agent=request.headers.get("User-Agent"),
                            metadata={"request_id": request_id},
                        )
                    )
                    raise TokenInvalidError("Session identity not found.")
                provider = identity.provider
            else:
                provider = None

            stmt = (
                select(Role)
                .where(Role.id == membership.role_id)
                .options(selectinload(Role.permissions))
            )
            result = await session.execute(stmt)
            role = result.scalars().first()
            if role is None:
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.PERMISSION_DENIED,
                        outcome=SecurityOutcome.FAILURE,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        reason="Role not found",
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("User-Agent"),
                        metadata={"request_id": request_id},
                    )
                )
                raise InsufficientPermissionsError()

            permissions = {p.codename for p in role.permissions}
            role_names = {role.name for role in [role] if role.name}

            context = AuthenticationContext(
                request_id=request_id,
                correlation_id=request.headers.get("X-Correlation-ID", request_id),
                user_id=user_id,
                tenant_id=tenant_id,
                organization_id=organization_id,
                session_id=device_session.id,
                membership_id=membership.id,
                role_ids=frozenset({role.id}),
                role_names=frozenset(role_names),
                permissions=frozenset(permissions),
                authentication_method="session",
                provider=provider,
                authenticated_at=datetime.now(UTC),
                request_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("User-Agent"),
                is_service_account=False,
            )

            request.state.auth_context = context

            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.TOKEN_VALIDATED,
                    outcome=SecurityOutcome.SUCCESS,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    session_id=device_session.id,
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("User-Agent"),
                    metadata={"request_id": request_id},
                )
            )

            response = await call_next(request)
            await session.commit()
            await session_gen.aclose()
            return response

        except Exception:
            with suppress(Exception):
                await session.rollback()
            with suppress(Exception):
                await session_gen.aclose()
            raise
