"""Authentication API router — PR-1.2.5.

Phase 4 implements /auth/logout and /auth/logout-all.
POST /auth/refresh and POST /auth/token are implemented in Phase 2/3.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, Response

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth.schemas import (
    LogoutAllRequest,
    LogoutRequest,
    RefreshTokenRequest,
    TokenRequest,
    TokenResponse,
)
from app.auth.events import (
    SecurityEvent,
    SecurityEventType,
    SecurityOutcome,
    security_event_emitter,
)
from app.auth.exceptions import (
    RefreshTokenReusedError,
    SessionExpiredError,
    TenantAccessDeniedError,
    TokenInvalidError,
)
from app.auth.sessions import SessionService
from app.auth.tokens import AccessTokenSubject, TokenService
from app.common.config import Settings, get_settings
from app.common.dependencies import get_db
from app.repositories.auth import (
    DeviceSessionRepository,
    RefreshTokenFamilyRepository,
)
from app.repositories.core import TenantRepository

router = APIRouter(prefix="/auth", tags=["auth"])


async def _execute_refresh(
    request: Request,
    body: RefreshTokenRequest | TokenRequest,
    settings: Settings,
    db: AsyncSession,
) -> TokenResponse:
    """Shared refresh orchestration for /auth/refresh and /auth/token.

    Raises:
        TokenInvalidError: The refresh token is invalid.
        RefreshTokenReusedError: The token has been reused or the session is revoked.
        SessionExpiredError: The session has exceeded its timeout.
        TenantAccessDeniedError: The tenant is missing or inactive.
    """
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    token_service = TokenService(settings)
    device_session_repo = DeviceSessionRepository(db)
    refresh_token_family_repo = RefreshTokenFamilyRepository(db)
    session_service = SessionService(
        device_session_repo=device_session_repo,
        refresh_token_family_repo=refresh_token_family_repo,
        token_service=token_service,
        settings=settings,
    )

    session, new_refresh_token = await session_service.refresh_session(
        plaintext_refresh_token=body.refresh_token,
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=request.state.request_id,
    )

    tenant_repo = TenantRepository(db)
    tenant = await tenant_repo.get(session.tenant_id)
    if tenant is None or not tenant.is_active:
        raise TenantAccessDeniedError("Tenant is not active.")

    access_token = token_service.create_access_token(
        AccessTokenSubject(
            user_id=session.user_id,
            tenant_id=session.tenant_id,
            organization_id=tenant.organization_id,
            session_id=session.id,
        )
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token.plaintext,
        token_type="bearer",  # noqa: S106
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


def _oauth2_error_response(
    request: Request,
    status_code: int,
    error: str,
    description: str,
) -> JSONResponse:
    """Build an OAuth2-formatted error response with WWW-Authenticate header."""
    response = JSONResponse(
        status_code=status_code,
        content={
            "error": error,
            "error_description": description,
        },
    )
    if status_code == status.HTTP_401_UNAUTHORIZED:
        response.headers["WWW-Authenticate"] = 'Bearer realm="platform"'
    return response


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Refresh access token",
    description="Exchange a valid refresh token for a new access token and refresh token pair.",
)
async def refresh(
    request: Request,
    body: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """Refresh an access token using a valid refresh token."""
    return await _execute_refresh(request, body, settings, db)


@router.post(
    "/token",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Issue tokens",
    description="OAuth2-compatible token endpoint. Phase 3 supports grant_type=refresh_token.",
)
async def token(
    request: Request,
    body: TokenRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Issue tokens via the OAuth2 token endpoint."""
    if body.grant_type != "refresh_token":
        return _oauth2_error_response(
            request,
            status.HTTP_400_BAD_REQUEST,
            "unsupported_grant_type",
            "The grant type is not supported.",
        )

    if not body.refresh_token.strip():
        return _oauth2_error_response(
            request,
            status.HTTP_400_BAD_REQUEST,
            "invalid_request",
            "The refresh token is required.",
        )

    try:
        token_response = await _execute_refresh(request, body, settings, db)
        return token_response
    except TokenInvalidError:
        return _oauth2_error_response(
            request,
            status.HTTP_401_UNAUTHORIZED,
            "invalid_grant",
            "The refresh token is invalid.",
        )
    except RefreshTokenReusedError:
        return _oauth2_error_response(
            request,
            status.HTTP_401_UNAUTHORIZED,
            "invalid_grant",
            "The refresh token has been reused.",
        )
    except SessionExpiredError:
        return _oauth2_error_response(
            request,
            status.HTTP_401_UNAUTHORIZED,
            "invalid_grant",
            "The session has expired.",
        )
    except TenantAccessDeniedError:
        raise


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke refresh token",
    description="Revoke a single refresh token.",
    response_class=Response,
)
async def logout(
    request: Request,
    body: LogoutRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Revoke a single refresh token."""
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    token_service = TokenService(settings)
    device_session_repo = DeviceSessionRepository(db)
    hash_val = token_service.hash_refresh_token(body.refresh_token)

    session = await device_session_repo.get_by_refresh_token_hash(hash_val)
    if session is None:
        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.TOKEN_INVALID,
                outcome=SecurityOutcome.FAILURE,
                reason="Invalid refresh token for logout",
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request.state.request_id,
            )
        )
        raise TokenInvalidError("Invalid refresh token.")

    now = datetime.now(UTC)
    await device_session_repo.revoke(session.id, revoked_at=now)

    security_event_emitter.emit(
        SecurityEvent(
            event_type=SecurityEventType.SESSION_REVOKED,
            outcome=SecurityOutcome.SUCCESS,
            user_id=session.user_id,
            tenant_id=session.tenant_id,
            session_id=session.id,
            reason="Session revoked via logout endpoint",
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request.state.request_id,
        )
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke all refresh tokens for a session",
    description=("Revoke all refresh tokens belonging to the same session as the provided token."),
    response_class=Response,
)
async def logout_all(
    request: Request,
    body: LogoutAllRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Revoke all refresh tokens for the session identified by the provided token."""
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    token_service = TokenService(settings)
    device_session_repo = DeviceSessionRepository(db)
    hash_val = token_service.hash_refresh_token(body.refresh_token)

    session = await device_session_repo.get_by_refresh_token_hash(hash_val)
    if session is None:
        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.TOKEN_INVALID,
                outcome=SecurityOutcome.FAILURE,
                reason="Invalid refresh token for logout-all",
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request.state.request_id,
            )
        )
        raise TokenInvalidError("Invalid refresh token.")

    sessions = await device_session_repo.get_active_sessions_for_user(session.user_id)
    now = datetime.now(UTC)
    revoked_count = await device_session_repo.revoke_all_for_user(session.user_id, revoked_at=now)

    for revoked_session in sessions:
        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.SESSION_REVOKED,
                outcome=SecurityOutcome.SUCCESS,
                user_id=revoked_session.user_id,
                tenant_id=revoked_session.tenant_id,
                session_id=revoked_session.id,
                reason="Session revoked via logout-all endpoint",
                ip_address=ip_address,
                user_agent=user_agent,
                request_id=request.state.request_id,
            )
        )

    security_event_emitter.emit(
        SecurityEvent(
            event_type=SecurityEventType.ALL_SESSIONS_REVOKED,
            outcome=SecurityOutcome.SUCCESS,
            user_id=session.user_id,
            tenant_id=session.tenant_id,
            reason="All sessions revoked via logout-all endpoint",
            ip_address=ip_address,
            user_agent=user_agent,
            metadata={"revoked_count": str(revoked_count)},
            request_id=request.state.request_id,
        )
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
