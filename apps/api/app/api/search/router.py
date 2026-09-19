"""Search API router — PR-2.7."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.search.schemas import MailSearchRequest, MailSearchResponse
from app.auth.dependencies import require_tenant_membership
from app.common.config import Settings, get_settings
from app.search.elasticsearch_search import (
    ElasticsearchSearchAdapter,
    SearchInvalidQueryError,
    SearchServiceUnavailableError,
)
from app.search.service import SearchService

if TYPE_CHECKING:
    from app.auth.context import AuthenticationContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/search", tags=["search"])


def get_search_service(settings: Settings = Depends(get_settings)) -> SearchService:
    """Dependency injecting the SearchService.

    The adapter manages its own ephemeral httpx.AsyncClient per search
    call when no shared client is provided, ensuring clean disposal.
    A long-lived connection pool should be held in app.state for
    production optimisation in a future PR.
    """
    adapter = ElasticsearchSearchAdapter(base_url=settings.elasticsearch_url)

    from mip_ai.embeddings import get_embedding_provider

    provider = get_embedding_provider()

    return SearchService(es_adapter=adapter, embedding_provider=provider)


@router.post(
    "/mail",
    response_model=MailSearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Search Mail Messages",
    description="Deterministic full-text and filtered search for mail messages inside the tenant bounds.",  # noqa: E501
)
async def search_mail(
    body: MailSearchRequest,
    context: AuthenticationContext = Depends(require_tenant_membership()),
    search_service: SearchService = Depends(get_search_service),
) -> MailSearchResponse:
    """Execute a mail search bounded securely to the authenticated tenant."""
    if body.from_date and body.to_date and body.from_date > body.to_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_date cannot be later than to_date",
        )

    try:
        response = await search_service.search_mail(
            tenant_id=str(context.tenant_id),
            request=body,
        )
        return response
    except SearchInvalidQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except SearchServiceUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected error during search_mail")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected internal error occurred.",
        ) from exc
