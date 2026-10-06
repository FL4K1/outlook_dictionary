"""Mail API router — PR-3.4."""

from __future__ import annotations

import collections.abc  # noqa: TC003
import logging
import uuid  # noqa: TC003
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.mail.schemas import (
    DeactivateAccountResponse,
    MailAccountResponse,
    MailFolderResponse,
    MailSyncStatusResponse,
    MailSyncTriggerResponse,
    PaginatedFoldersResponse,
)
from app.api.search.router import get_search_service
from app.api.search.schemas import MailSearchRequest, MailSearchResponse, SearchHit
from app.auth.dependencies import require_tenant_membership
from app.common.config import Settings, get_settings
from app.common.dependencies import get_db
from app.common.encryption import EncryptionService
from app.common.rate_limit import RateLimiter
from app.mail.service import (
    IdentityNotLinkedError,
    MailAccountNotFoundError,
    MailLifecycleService,
    MessageDataService,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.auth.context import AuthenticationContext
    from app.search.service import SearchService


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mail", tags=["mail"])


def get_encryption_service() -> EncryptionService:
    """Dependency for EncryptionService."""
    return EncryptionService()


def get_mail_service(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    encryption: EncryptionService = Depends(get_encryption_service),
) -> MailLifecycleService:
    """Dependency injecting the MailLifecycleService."""
    return MailLifecycleService(db=db, settings=settings, encryption=encryption)


def get_message_data_service(
    search_service: SearchService = Depends(get_search_service),
) -> MessageDataService:
    """Dependency injecting MessageDataService."""
    return MessageDataService(es_adapter=search_service.es_adapter)


async def get_arq_redis(
    settings: Settings = Depends(get_settings),
) -> collections.abc.AsyncGenerator[Any, None]:
    """Provide ARQ Redis connection pool for enqueuing jobs."""
    from arq import create_pool
    from arq.connections import RedisSettings

    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        yield pool
    finally:
        await pool.close()


@router.post(
    "/accounts",
    response_model=MailAccountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or Reconnect Mail Account",
    dependencies=[Depends(RateLimiter(requests=10, window=60))],
)
async def create_mail_account(
    context: AuthenticationContext = Depends(require_tenant_membership()),
    service: MailLifecycleService = Depends(get_mail_service),
    arq_redis: Any = Depends(get_arq_redis),
) -> MailAccountResponse:
    """Create a MailAccount from the authenticated Entra identity and start sync."""
    try:
        account = await service.connect_account(context=context)
        # Attempt to enqueue folder discovery right away
        # But wait, we don't have a specific ARQ job for just discovery,
        # mail_sync_discovery_cron will handle it gracefully.
        return account  # type: ignore[return-value]
    except IdentityNotLinkedError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error connecting mail account")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected internal error occurred.",
        ) from exc


@router.get(
    "/accounts",
    response_model=list[MailAccountResponse],
    status_code=status.HTTP_200_OK,
    summary="List Active Mail Accounts",
)
async def list_mail_accounts(
    context: AuthenticationContext = Depends(require_tenant_membership()),
    service: MailLifecycleService = Depends(get_mail_service),
) -> list[MailAccountResponse]:
    """Retrieve all active mail accounts for the tenant."""
    accounts = await service.get_accounts(context=context)
    return accounts  # type: ignore[return-value]


@router.get(
    "/accounts/{account_id}/sync-status",
    response_model=list[MailSyncStatusResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Sync Status for Mail Account",
)
async def get_sync_status(
    account_id: uuid.UUID,
    context: AuthenticationContext = Depends(require_tenant_membership()),
    service: MailLifecycleService = Depends(get_mail_service),
) -> list[MailSyncStatusResponse]:
    """View the synchronization state of all folders in the account."""
    try:
        states = await service.get_sync_status(context=context, account_id=account_id)
        # Map DB model directly to Pydantic since field names match, with some alias translations
        return [
            MailSyncStatusResponse(
                mail_folder_id=s.mail_folder_id,
                state=s.state,
                last_sync=s.updated_at,
                resync_generation=s.resync_generation,
            )
            for s in states
        ]
    except MailAccountNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Mail account not found"
        ) from e


@router.delete(
    "/accounts/{account_id}",
    response_model=DeactivateAccountResponse,
    status_code=status.HTTP_200_OK,
    summary="Deactivate Mail Account",
)
async def deactivate_mail_account(
    account_id: uuid.UUID,
    context: AuthenticationContext = Depends(require_tenant_membership()),
    service: MailLifecycleService = Depends(get_mail_service),
) -> DeactivateAccountResponse:
    """Soft-deactivate a mail account, stop its sync scheduling, and revoke tokens."""
    try:
        account = await service.deactivate_account(context=context, account_id=account_id)
        return DeactivateAccountResponse(id=account.id, status=account.status)
    except MailAccountNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Mail account not found"
        ) from e


@router.get(
    "/folders",
    response_model=PaginatedFoldersResponse,
    status_code=status.HTTP_200_OK,
    summary="List Synchronized Folders",
)
async def list_mail_folders(
    limit: int = 50,
    offset: int = 0,
    context: AuthenticationContext = Depends(require_tenant_membership()),
    service: MailLifecycleService = Depends(get_mail_service),
) -> PaginatedFoldersResponse:
    """Retrieve a paginated list of synchronized folders within the tenant."""
    folders, total = await service.get_folders(context=context, limit=limit, offset=offset)
    return PaginatedFoldersResponse(
        items=[MailFolderResponse.model_validate(f) for f in folders], total=total
    )


@router.post(
    "/accounts/{account_id}/sync",
    response_model=MailSyncTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger Manual Sync",
    dependencies=[Depends(RateLimiter(requests=20, window=60))],
)
async def trigger_manual_sync(
    account_id: uuid.UUID,
    context: AuthenticationContext = Depends(require_tenant_membership()),
    service: MailLifecycleService = Depends(get_mail_service),
    arq_redis: Any = Depends(get_arq_redis),
) -> MailSyncTriggerResponse:
    """Enqueue synchronization jobs for all active folders in this account immediately."""
    enqueued = await service.enqueue_folder_sync(
        context=context, account_id=account_id, arq_redis=arq_redis
    )
    return MailSyncTriggerResponse(
        mail_folder_id=account_id,  # Reused field for API simplicity, normally we'd omit this
        message=f"Enqueued {enqueued} folder sync jobs.",
    )


@router.get(
    "/messages",
    response_model=MailSearchResponse,
    status_code=status.HTTP_200_OK,
    summary="List Messages",
)
async def list_messages(
    page_size: int = 25,
    search_after: str | None = None,
    folder_id: str | None = None,
    account_id: str | None = None,
    context: AuthenticationContext = Depends(require_tenant_membership()),
    search_service: SearchService = Depends(get_search_service),
) -> MailSearchResponse:
    """Cursor paginated retrieval of mail messages within the tenant bounds."""
    # Convert string search_after to list if provided
    sa_list = None
    if search_after:
        import json

        try:
            sa_list = json.loads(search_after)
            if not isinstance(sa_list, list):
                sa_list = None
        except Exception:
            # Continue executing rather than failing the search
            sa_list = None

    req = MailSearchRequest(
        page_size=page_size,
        search_after=sa_list,
        folder_ids=[folder_id] if folder_id else None,
        account_ids=[account_id] if account_id else None,
        search_mode="lexical",
        query=None,
    )

    try:
        response = await search_service.search_mail(
            tenant_id=str(context.tenant_id),
            request=req,
        )
        return response
    except Exception as exc:
        logger.exception("Unexpected error listing messages")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected internal error occurred.",
        ) from exc


@router.get(
    "/messages/{message_id}",
    response_model=SearchHit,
    status_code=status.HTTP_200_OK,
    summary="Get Message Details",
)
async def get_message_detail(
    message_id: uuid.UUID,
    context: AuthenticationContext = Depends(require_tenant_membership()),
    message_service: MessageDataService = Depends(get_message_data_service),
) -> SearchHit:
    """Retrieve canonical message metadata strictly bounded to the authenticated tenant."""
    message = await message_service.get_message(
        tenant_id=str(context.tenant_id),
        message_id=message_id,
    )
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    # Map raw ES source to SearchHit format (which handles safe default fields mapping)
    participants = []
    for p in message.get("participants", []):
        from app.api.search.schemas import SearchParticipant

        participants.append(
            SearchParticipant(
                name=p.get("name"), email=p.get("email"), role=p.get("role", "unknown")
            )
        )

    from app.api.search.schemas import SearchSender

    sender = None
    if message.get("sender"):
        sender = SearchSender(
            name=message["sender"].get("name"), email=message["sender"].get("email")
        )

    return SearchHit(
        id=message["id"],
        mail_account_id=message["mail_account_id"],
        subject=message.get("subject"),
        sender=sender,
        sender_email=message.get("sender_email") or (sender.email if sender else None),
        body_preview=message.get("body_preview"),
        participants=participants,
        received_date_time=message.get("received_date_time"),
        folder_ids=message.get("folder_ids", []),
        is_read=message.get("is_read", False),
        has_attachments=message.get("has_attachments", False),
    )
