"""Embedding Provider Factory.

Factory for resolving and instantiating embedding provider implementations based on configuration.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from mip_ai.embeddings.mock import DeterministicMockEmbeddingProvider

if TYPE_CHECKING:
    from mip_ai.embeddings.base import EmbeddingProvider

logger = logging.getLogger(__name__)


def get_embedding_provider(provider_name: str | None = None) -> EmbeddingProvider | None:
    """Resolve an embedding provider by name or environment variable EMBEDDING_PROVIDER.

    Supported values:
    - 'mock': DeterministicMockEmbeddingProvider for local testing & development
    - 'none' / None: Disabled (lexical search fallback)
    """
    if provider_name is None:
        provider_name = os.getenv("EMBEDDING_PROVIDER", "mock")

    provider_name = provider_name.lower().strip()

    if provider_name in ("mock", "test", "testing"):
        return DeterministicMockEmbeddingProvider()
    elif provider_name in ("none", "disabled", ""):
        return None
    elif provider_name == "openai":
        # Placeholder for OpenAI text-embedding-3-small provider when API credentials are provided
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning(
                "EMBEDDING_PROVIDER=openai specified but OPENAI_API_KEY missing. Fallback to None."
            )
            return None
        raise NotImplementedError(
            "OpenAI embedding provider client requires explicit external API key configuration."
        )
    else:
        logger.warning(
            "Unknown EMBEDDING_PROVIDER '%s', defaulting to DeterministicMockEmbeddingProvider",
            provider_name,
        )
        return DeterministicMockEmbeddingProvider()
