"""Unit tests for Query Understanding provider, factory, and OpenAI implementation."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from mip_ai.query_understanding import (
    DeterministicMockQueryUnderstandingProvider,
    QueryUnderstandingConfigurationError,
    QueryUnderstandingMalformedOutputError,
    QueryUnderstandingPermanentError,
    QueryUnderstandingRateLimitError,
    QueryUnderstandingTransientError,
    get_query_understanding_provider,
)
from mip_ai.query_understanding.openai import OpenAIQueryUnderstandingProvider
from mip_models.search import MailQueryPlan


@pytest.mark.asyncio
async def test_mock_query_understanding_provider():
    provider = DeterministicMockQueryUnderstandingProvider()
    res = await provider.understand_query("unread emails from Rahul about Kubernetes last week")
    assert res.query_plan.is_read is False
    assert res.query_plan.sender is not None
    assert res.query_plan.sender.name == "Rahul"
    assert res.query_plan.query == "kubernetes"
    assert res.query_plan.date_range is not None
    assert res.query_plan.date_range.kind == "last_week"


def test_factory_resolves_mock(monkeypatch):
    monkeypatch.setenv("QUERY_UNDERSTANDING_PROVIDER", "mock")
    provider = get_query_understanding_provider()
    assert isinstance(provider, DeterministicMockQueryUnderstandingProvider)


def test_factory_openai_missing_key_raises_config_error(monkeypatch):
    monkeypatch.setenv("QUERY_UNDERSTANDING_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(QueryUnderstandingConfigurationError):
        get_query_understanding_provider()


def test_factory_unknown_provider_raises_config_error():
    with pytest.raises(QueryUnderstandingConfigurationError):
        get_query_understanding_provider("anthropic_unknown")


@pytest.mark.asyncio
async def test_openai_provider_success():
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    sample_response_body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "query": "Kubernetes",
                            "sender": {"name": "Rahul", "email": None},
                            "participants": [],
                            "recipients": [],
                            "folder": None,
                            "account": None,
                            "is_read": False,
                            "has_attachments": True,
                            "date_range": {"kind": "last_week"},
                            "retrieval_intent": "keyword",
                        }
                    )
                }
            }
        ]
    }
    mock_response = httpx.Response(200, json=sample_response_body)
    mock_client.post.return_value = mock_response

    provider = OpenAIQueryUnderstandingProvider(api_key="test-key", client=mock_client)
    result = await provider.understand_query("unread emails from Rahul about Kubernetes last week")

    assert isinstance(result.query_plan, MailQueryPlan)
    assert result.query_plan.query == "Kubernetes"
    assert result.query_plan.sender.name == "Rahul"
    assert result.query_plan.is_read is False
    assert result.query_plan.has_attachments is True


@pytest.mark.asyncio
async def test_openai_provider_malformed_json_raises_malformed_output():
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    sample_response_body = {"choices": [{"message": {"content": "INVALID JSON TEXT"}}]}
    mock_response = httpx.Response(200, json=sample_response_body)
    mock_client.post.return_value = mock_response

    provider = OpenAIQueryUnderstandingProvider(api_key="test-key", client=mock_client)
    with pytest.raises(QueryUnderstandingMalformedOutputError):
        await provider.understand_query("test query")


@pytest.mark.asyncio
async def test_openai_provider_schema_violation_raises_malformed_output():
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    sample_response_body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "query": "test",
                            "extra_forbidden_field": True,
                        }
                    )
                }
            }
        ]
    }
    mock_response = httpx.Response(200, json=sample_response_body)
    mock_client.post.return_value = mock_response

    provider = OpenAIQueryUnderstandingProvider(api_key="test-key", client=mock_client)
    with pytest.raises(QueryUnderstandingMalformedOutputError):
        await provider.understand_query("test query")


@pytest.mark.asyncio
async def test_openai_provider_rate_limit_429():
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = httpx.Response(429, headers={"Retry-After": "5"}, text="Rate limit exceeded")
    mock_client.post.return_value = mock_response

    provider = OpenAIQueryUnderstandingProvider(api_key="test-key", client=mock_client)
    with pytest.raises(QueryUnderstandingRateLimitError) as exc_info:
        await provider.understand_query("test query")
    assert exc_info.value.retry_after == 5.0


@pytest.mark.asyncio
async def test_openai_provider_transient_500():
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = httpx.Response(500, text="Internal Server Error")
    mock_client.post.return_value = mock_response

    provider = OpenAIQueryUnderstandingProvider(api_key="test-key", client=mock_client)
    with pytest.raises(QueryUnderstandingTransientError):
        await provider.understand_query("test query")


@pytest.mark.asyncio
async def test_openai_provider_permanent_401():
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = httpx.Response(401, text="Unauthorized")
    mock_client.post.return_value = mock_response

    provider = OpenAIQueryUnderstandingProvider(api_key="test-key", client=mock_client)
    with pytest.raises(QueryUnderstandingPermanentError):
        await provider.understand_query("test query")
