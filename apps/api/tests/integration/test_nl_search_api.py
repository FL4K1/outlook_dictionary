import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.context import AuthenticationContext
from app.auth.dependencies import get_auth_context
from app.main import create_app
from app.search.service import SearchService
from mip_ai.query_understanding.base import QueryUnderstandingResult


@pytest.mark.asyncio
async def test_nl_search_api_endpoint_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUERY_UNDERSTANDING_PROVIDER", "mock")

    tenant_id = uuid.uuid4()

    def mock_auth() -> AuthenticationContext:
        return AuthenticationContext(
            request_id="test-req",
            correlation_id="test-corr",
            user_id=uuid.uuid4(),
            tenant_id=tenant_id,
            organization_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            membership_id=uuid.uuid4(),
        )

    app = create_app()
    app.dependency_overrides[get_auth_context] = mock_auth

    mock_search_service = AsyncMock(spec=SearchService)
    mock_search_service.search_mail.return_value = {
        "items": [
            {
                "id": str(uuid.uuid4()),
                "mail_account_id": str(uuid.uuid4()),
                "subject": "Kubernetes Cluster Upgrade",
                "sender": "Rahul",
                "participants": [],
                "received_date_time": "2026-09-20T10:00:00Z",
                "folder_ids": [],
                "is_read": False,
                "has_attachments": True,
            }
        ],
        "next_page_cursor": None,
    }

    from app.api.search.router import get_search_service

    app.dependency_overrides[get_search_service] = lambda: mock_search_service

    with patch("app.auth.middleware.is_public_route", return_value=True):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/search/mail/natural-language",
                json={
                    "natural_query": "unread emails from Rahul about Kubernetes",
                    "user_timezone": "UTC",
                    "page_size": 10,
                },
            )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["subject"] == "Kubernetes Cluster Upgrade"


@pytest.mark.asyncio
async def test_nl_search_api_endpoint_malformed_llm_output_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingProvider:
        async def understand_query(self, query: str) -> QueryUnderstandingResult:
            from mip_ai.query_understanding.errors import QueryUnderstandingMalformedOutputError

            raise QueryUnderstandingMalformedOutputError("LLM returned invalid JSON")

    from app.api.search.router import get_natural_language_search_service
    from app.search.nl_service import NaturalLanguageSearchService

    failing_service = NaturalLanguageSearchService(
        provider=FailingProvider(),  # type: ignore[arg-type]
        search_service=AsyncMock(),
    )
    app = create_app()
    app.dependency_overrides[get_natural_language_search_service] = lambda: failing_service

    tenant_id = uuid.uuid4()

    def mock_auth() -> AuthenticationContext:
        return AuthenticationContext(
            request_id="test-req",
            correlation_id="test-corr",
            user_id=uuid.uuid4(),
            tenant_id=tenant_id,
            organization_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            membership_id=uuid.uuid4(),
        )

    app.dependency_overrides[get_auth_context] = mock_auth

    with patch("app.auth.middleware.is_public_route", return_value=True):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/search/mail/natural-language",
                json={
                    "natural_query": "invalid query leading to malformed JSON",
                },
            )

    assert resp.status_code == 502, resp.text
    assert "Query understanding service failed" in resp.json()["detail"]
