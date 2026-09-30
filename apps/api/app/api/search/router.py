"""Search API router — PR-2.7."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.search.schemas import (
    MailSearchRequest,
    MailSearchResponse,
    NaturalLanguageSearchResponse,
    NLMailSearchRequest,
)
from app.auth.dependencies import require_tenant_membership
from app.common.config import Settings, get_settings
from app.search.elasticsearch_search import (
    ElasticsearchSearchAdapter,
    SearchInvalidQueryError,
    SearchServiceUnavailableError,
)
from app.search.nl_service import (
    EntityAmbiguityError,
    EntityResolutionError,
    InvalidTimezoneError,
    NaturalLanguageSearchService,
    UnsupportedQueryCapabilityError,
)
from app.search.service import SearchService
from mip_ai.query_understanding import (
    QueryUnderstandingConfigurationError,
    QueryUnderstandingMalformedOutputError,
    QueryUnderstandingPermanentError,
    QueryUnderstandingRateLimitError,
    QueryUnderstandingTransientError,
)

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


def get_natural_language_search_service(
    search_service: SearchService = Depends(get_search_service),
    settings: Settings = Depends(get_settings),
) -> NaturalLanguageSearchService:
    """Dependency injecting NaturalLanguageSearchService."""
    from mip_ai.query_understanding import get_query_understanding_provider
    from mip_ai.synthesis import GatewaySearchSynthesisProvider

    provider = get_query_understanding_provider(config=settings.llm)
    synthesis_provider = GatewaySearchSynthesisProvider(config=settings.llm)
    return NaturalLanguageSearchService(
        provider=provider, search_service=search_service, synthesis_provider=synthesis_provider
    )


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


@router.post(
    "/mail/natural-language",
    response_model=NaturalLanguageSearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Natural Language Mail Search",
    description="Translate natural language queries into structured search requests executed against tenant messages.",  # noqa: E501
)
async def search_mail_natural_language(
    body: NLMailSearchRequest,
    context: AuthenticationContext = Depends(require_tenant_membership()),
    nl_service: NaturalLanguageSearchService = Depends(get_natural_language_search_service),
) -> NaturalLanguageSearchResponse:
    """Execute natural language search bounded securely to authenticated tenant."""
    try:
        return await nl_service.search_natural_language(
            tenant_id=context.tenant_id,
            natural_query=body.natural_query,
            user_timezone=body.user_timezone,
            page_size=body.page_size,
            search_after=body.search_after,
            synthesize=body.synthesize,
        )
    except (
        UnsupportedQueryCapabilityError,
        EntityAmbiguityError,
        EntityResolutionError,
        InvalidTimezoneError,
        SearchInvalidQueryError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except (
        QueryUnderstandingMalformedOutputError,
        QueryUnderstandingRateLimitError,
        QueryUnderstandingTransientError,
        QueryUnderstandingPermanentError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Query understanding service failed: {exc}",
        ) from exc
    except QueryUnderstandingConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query understanding service misconfigured: {exc}",
        ) from exc
    except SearchServiceUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Unexpected error during natural language mail search")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected internal error occurred.",
        ) from exc
