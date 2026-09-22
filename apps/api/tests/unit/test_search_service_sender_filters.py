"""Unit tests for SearchService from_sender_emails and participant_emails filters."""

from unittest.mock import AsyncMock

import pytest

from app.api.search.schemas import MailSearchRequest
from app.search.service import SearchService


@pytest.mark.asyncio
async def test_search_service_generates_sender_email_terms_filter():
    mock_es_adapter = AsyncMock()
    mock_es_adapter.search.return_value = {"hits": {"hits": []}}

    service = SearchService(es_adapter=mock_es_adapter)
    req = MailSearchRequest(
        from_sender_emails=["rahul@example.com"],
        participant_emails=["alice@example.com", "bob@example.com"],
        query="project update",
    )

    await service.search_mail(tenant_id="tenant-123", request=req)

    mock_es_adapter.search.assert_called_once()
    call_args = mock_es_adapter.search.call_args[1]
    query_body = call_args["query"]

    filters = query_body["query"]["bool"]["filter"]

    # Verify tenant isolation and is_deleted
    assert {"term": {"tenant_id": "tenant-123"}} in filters
    assert {"term": {"is_deleted": False}} in filters

    # Verify exact sender and participant filters
    assert {"terms": {"sender_email": ["rahul@example.com"]}} in filters
    assert {"terms": {"participants.email": ["alice@example.com", "bob@example.com"]}} in filters
