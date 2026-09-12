"""Microsoft Entra ID authentication API routes.

Provides:
- GET /auth/entra — Initiate Entra ID authorization flow
- POST /auth/callback/entra — OAuth2 callback for Entra ID authorization code exchange
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import ProviderAuthenticationError
from app.common.config import Settings, get_settings
from app.common.dependencies import get_db, get_session_factory
from app.services.identity_provider import ProviderAuthError, ProviderAuthService
from mip_providers.identity.entra import EntraIdentityProviderAuth

router = APIRouter(prefix="/auth", tags=["auth"])


class EntraCallbackRequest(BaseModel):
    """Request body for POST /auth/callback/entra."""

    code: str
    state: str


class EntraCallbackResponse(BaseModel):
    """Response body for successful Entra ID callback."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


def _get_provider_auth_service(settings: Settings) -> ProviderAuthService:
    encryption_service = None
    if settings.encryption_dek:
        from app.common.encryption import EncryptionService

        encryption_service = EncryptionService(dek=settings.encryption_dek)
    provider_auth = EntraIdentityProviderAuth(
        settings=settings,
        encryption_service=encryption_service,
    )
    return ProviderAuthService(
        provider_auth=provider_auth,
        settings=settings,
        encryption_service=encryption_service,
    )


@router.get("/entra", summary="Initiate Entra ID authorization flow")
async def entra_authorize(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Redirect to Microsoft Entra ID authorization endpoint."""
    request_id = getattr(request.state, "request_id", None)

    factory = get_session_factory()
    if factory is None:
        raise ProviderAuthenticationError("Database not available.")

    provider_auth = EntraIdentityProviderAuth(settings=settings)
    async for db in factory.get_session():
        service = ProviderAuthService(
            provider_auth=provider_auth,
            settings=settings,
            encryption_service=provider_auth._encryption,
        )
        authorization_url = await service.initiate_login(db, request_id=request_id)
        break

    return RedirectResponse(url=authorization_url, status_code=status.HTTP_302_FOUND)


@router.post(
    "/callback/entra",
    response_model=EntraCallbackResponse,
    status_code=status.HTTP_200_OK,
    summary="Entra ID OAuth2 callback",
)
async def entra_callback(
    request: Request,
    body: EntraCallbackRequest,
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> EntraCallbackResponse:
    """Handle Entra ID OAuth2 callback and return platform tokens."""
    request_id = getattr(request.state, "request_id", None)
    provider_auth = _get_provider_auth_service(settings)

    try:
        result = await provider_auth.handle_callback(
            db=db,
            code=body.code,
            state=body.state,
            request_id=request_id,
        )
        await db.commit()
    except ProviderAuthError as exc:
        await db.rollback()
        raise ProviderAuthenticationError(str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        raise ProviderAuthenticationError("Callback processing failed.") from exc

    return EntraCallbackResponse(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )
