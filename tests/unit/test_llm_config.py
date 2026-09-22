"""Unit tests for single LLM configuration schema."""

import pytest
from pydantic import SecretStr, ValidationError

from mip_ai.query_understanding.llm_config import LLMConfig, LLMProvider


def test_valid_llm_config_defaults():
    config = LLMConfig()
    assert config.provider == LLMProvider.MOCK
    assert config.model == "deterministic"
    assert config.api_key is None
    assert config.base_url is None
    assert config.timeout_seconds == 30.0
    assert config.require_structured_output is True


def test_cloud_provider_config():
    config = LLMConfig(
        provider=LLMProvider.GEMINI,
        model="gemini-1.5-flash",
        api_key=SecretStr("super-secret-key"),
    )
    assert config.provider == LLMProvider.GEMINI
    assert config.model == "gemini-1.5-flash"
    assert config.api_key.get_secret_value() == "super-secret-key"


def test_ollama_provider_config_no_key():
    config = LLMConfig(
        provider=LLMProvider.OLLAMA,
        model="llama3",
        base_url="http://localhost:11434",
    )
    assert config.provider == LLMProvider.OLLAMA
    assert config.api_key is None
    assert config.base_url == "http://localhost:11434"


def test_invalid_provider_raises_validation_error():
    with pytest.raises(ValidationError, match="Input should be"):
        LLMConfig(provider="anthropic")  # type: ignore


def test_invalid_timeout_raises_validation_error():
    with pytest.raises(ValidationError):
        LLMConfig(timeout_seconds="not-a-number")  # type: ignore


def test_config_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        LLMConfig(provider=LLMProvider.MOCK, extra_field="forbidden")  # type: ignore
