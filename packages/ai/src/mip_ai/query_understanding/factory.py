"""Query Understanding Provider Factory."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from mip_ai.query_understanding.errors import QueryUnderstandingConfigurationError
from mip_ai.query_understanding.gateway import GatewayQueryUnderstandingProvider
from mip_ai.query_understanding.llm_config import LLMConfig, LLMProvider
from mip_ai.query_understanding.mock import DeterministicMockQueryUnderstandingProvider

if TYPE_CHECKING:
    from mip_ai.query_understanding.base import QueryUnderstandingProvider

logger = logging.getLogger(__name__)


def get_query_understanding_provider(
    config: LLMConfig | str | None = None,
    provider_name: str | None = None,  # Legacy fallback support
) -> QueryUnderstandingProvider:
    """Resolve a query understanding provider from LLMConfig or string identifier.

    If an explicit LLMConfig is passed, it is validated and instantiated.
    If a string identifier or None is passed, legacy string resolution / env fallback is used.
    """
    if isinstance(config, str):
        provider_name = config
        config = None

    if config is not None:
        if config.provider == LLMProvider.MOCK:
            return DeterministicMockQueryUnderstandingProvider()

        # Gateway usage for custom providers
        if config.provider != LLMProvider.OLLAMA and not config.api_key:
            raise QueryUnderstandingConfigurationError(
                f"Cloud provider '{config.provider.value}' requires an API key."
            )

        return GatewayQueryUnderstandingProvider(config)

    # Legacy environment variable fallback / string identifier resolution
    if provider_name is None:
        provider_name = os.getenv("QUERY_UNDERSTANDING_PROVIDER", os.getenv("LLM_PROVIDER", "mock"))

    provider_name = provider_name.lower().strip()

    if provider_name in ("mock", "test", "testing"):
        return DeterministicMockQueryUnderstandingProvider()

    if provider_name == "openai":
        api_key = os.getenv("OPENAI_API_KEY", os.getenv("LLM_API_KEY", ""))
        if not api_key:
            raise QueryUnderstandingConfigurationError(
                "QUERY_UNDERSTANDING_PROVIDER=openai but OPENAI_API_KEY is missing."
            )

        # Preserve the old direct behavior for legacy backward compat
        from mip_ai.query_understanding.openai import OpenAIQueryUnderstandingProvider

        return OpenAIQueryUnderstandingProvider(
            api_key=api_key,
            model=os.getenv("QUERY_UNDERSTANDING_MODEL", os.getenv("LLM_MODEL", "gpt-4o-mini")),
            base_url=os.getenv(
                "QUERY_UNDERSTANDING_BASE_URL",
                os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
            ),
            timeout_seconds=float(
                os.getenv("QUERY_UNDERSTANDING_TIMEOUT", os.getenv("LLM_TIMEOUT_SECONDS", "10.0"))
            ),
        )

    try:
        provider_enum = LLMProvider(provider_name)
    except ValueError as e:
        raise QueryUnderstandingConfigurationError(
            f"Unknown query understanding provider '{provider_name}'."
        ) from e

    from pydantic import SecretStr

    env_api_key = os.getenv(f"{provider_name.upper()}_API_KEY", os.getenv("LLM_API_KEY"))
    llm_config = LLMConfig(
        provider=provider_enum,
        model=os.getenv("LLM_MODEL", os.getenv("QUERY_UNDERSTANDING_MODEL", "default")),
        api_key=SecretStr(env_api_key) if env_api_key else None,
        base_url=os.getenv("LLM_BASE_URL", os.getenv("QUERY_UNDERSTANDING_BASE_URL")),
    )

    if provider_enum != LLMProvider.OLLAMA and not llm_config.api_key:
        raise QueryUnderstandingConfigurationError(
            f"Cloud provider '{provider_name}' requires an API key."
        )

    return GatewayQueryUnderstandingProvider(llm_config)
