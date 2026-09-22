"""Unit tests for the multi-provider LLM gateway."""

import json
from unittest.mock import AsyncMock, patch

import litellm
import pytest
from pydantic import SecretStr

from mip_ai.query_understanding.errors import (
    QueryUnderstandingConfigurationError,
    QueryUnderstandingMalformedOutputError,
    QueryUnderstandingRateLimitError,
    QueryUnderstandingTransientError,
)
from mip_ai.query_understanding.gateway import GatewayQueryUnderstandingProvider
from mip_ai.query_understanding.llm_config import LLMConfig, LLMProvider
from mip_models.search import MailQueryPlan


@pytest.fixture
def mock_litellm_acompletion():
    with patch(
        "mip_ai.query_understanding.gateway.litellm.acompletion", new_callable=AsyncMock
    ) as m:
        yield m


@pytest.fixture
def mock_params_check():
    with patch(
        "mip_ai.query_understanding.gateway.litellm.get_supported_openai_params",
        return_value=["response_format", "tools", "temperature"],
    ) as check:
        yield check


@pytest.fixture
def sample_valid_llm_response():
    return {
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


def test_gateway_model_routing():
    """Verify provider prefixing works cleanly and prevents double prefixes."""
    configs = [
        # OpenAI -> direct
        (LLMConfig(provider=LLMProvider.OPENAI, model="gpt-4o"), "gpt-4o"),
        (LLMConfig(provider=LLMProvider.OPENAI, model="openai/gpt-4o"), "openai/gpt-4o"),
        # Others -> prefixed
        (
            LLMConfig(provider=LLMProvider.GEMINI, model="gemini-1.5-flash"),
            "gemini/gemini-1.5-flash",
        ),
        (
            LLMConfig(provider=LLMProvider.GEMINI, model="gemini/gemini-1.5-flash"),
            "gemini/gemini-1.5-flash",
        ),
        (LLMConfig(provider=LLMProvider.OPENROUTER, model="meta/llama3"), "openrouter/meta/llama3"),
        (
            LLMConfig(provider=LLMProvider.OPENROUTER, model="openrouter/meta/llama3"),
            "openrouter/meta/llama3",
        ),
    ]

    for config, expected_model in configs:
        provider = GatewayQueryUnderstandingProvider(config)
        assert provider._model == expected_model


@pytest.mark.asyncio
async def test_successful_structured_output_query(
    mock_litellm_acompletion, mock_params_check, sample_valid_llm_response
):
    """Test a successful structured output query passes cleanly."""
    mock_litellm_acompletion.return_value = sample_valid_llm_response
    config = LLMConfig(
        provider=LLMProvider.GEMINI, model="gemini-1.5-flash", api_key=SecretStr("test")
    )
    provider = GatewayQueryUnderstandingProvider(config)

    result = await provider.understand_query("test query")

    # Verify LiteLLM payload
    mock_litellm_acompletion.assert_called_once()
    kwargs = mock_litellm_acompletion.call_args.kwargs
    assert kwargs["model"] == "gemini/gemini-1.5-flash"
    assert kwargs["api_key"] == "test"
    assert kwargs["temperature"] == 0.0
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["strict"] is True

    # Verify structural validation via local Pydantic
    plan = result.query_plan
    assert isinstance(plan, MailQueryPlan)
    assert plan.query == "Kubernetes"
    assert plan.sender.name == "Rahul"


@pytest.mark.asyncio
async def test_capability_fail_closed(mock_params_check):
    """If provider does not support JSON schema, fail closed explicitly."""
    mock_params_check.return_value = ["temperature", "max_tokens"]  # No response_format
    config = LLMConfig(provider=LLMProvider.GROQ, model="llama", api_key=SecretStr("test"))
    provider = GatewayQueryUnderstandingProvider(config)

    with pytest.raises(QueryUnderstandingConfigurationError, match="does not reliably support"):
        await provider.understand_query("test")


@pytest.mark.asyncio
async def test_pydantic_deserialization_failure_raises_malformed(
    mock_litellm_acompletion, mock_params_check
):
    """If returning valid JSON but missing required MailQueryPlan fields, fail closed."""
    mock_litellm_acompletion.return_value = {
        "choices": [{"message": {"content": '{"unexpected_field": "no"}', "role": "assistant"}}]
    }
    config = LLMConfig(provider=LLMProvider.GEMINI, model="gemini-1.5", api_key=SecretStr("test"))
    provider = GatewayQueryUnderstandingProvider(config)

    with pytest.raises(QueryUnderstandingMalformedOutputError):
        await provider.understand_query("test")


@pytest.mark.asyncio
async def test_gateway_rate_limit_translation(mock_litellm_acompletion, mock_params_check):
    mock_litellm_acompletion.side_effect = litellm.exceptions.RateLimitError(
        message="Too many requests", llm_provider="gemini", model="gemini-1.5"
    )
    provider = GatewayQueryUnderstandingProvider(LLMConfig(api_key=SecretStr("test")))
    with pytest.raises(QueryUnderstandingRateLimitError):
        await provider.understand_query("test")


@pytest.mark.asyncio
async def test_gateway_timeout_translation(mock_litellm_acompletion, mock_params_check):
    mock_litellm_acompletion.side_effect = litellm.exceptions.Timeout(
        message="Request timed out", llm_provider="gemini", model="gemini-1.5"
    )
    provider = GatewayQueryUnderstandingProvider(LLMConfig(api_key=SecretStr("test")))
    with pytest.raises(QueryUnderstandingTransientError):
        await provider.understand_query("test")


@pytest.mark.asyncio
async def test_gateway_auth_translation(mock_litellm_acompletion, mock_params_check):
    mock_litellm_acompletion.side_effect = litellm.exceptions.AuthenticationError(
        message="Invalid key", llm_provider="gemini", model="gemini-1.5"
    )
    provider = GatewayQueryUnderstandingProvider(LLMConfig(api_key=SecretStr("test")))
    with pytest.raises(QueryUnderstandingConfigurationError):
        await provider.understand_query("test")


@pytest.mark.asyncio
async def test_gateway_unsupported_params_translation(mock_litellm_acompletion, mock_params_check):
    mock_litellm_acompletion.side_effect = litellm.exceptions.UnsupportedParamsError(
        message="response_format not supported", status_code=400
    )
    provider = GatewayQueryUnderstandingProvider(LLMConfig(api_key=SecretStr("test")))
    with pytest.raises(QueryUnderstandingConfigurationError):
        await provider.understand_query("test")
