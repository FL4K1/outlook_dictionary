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


def get_embedding_provider(
    provider_name: str | None = None,
) -> EmbeddingProvider | None:
    """Resolve an embedding provider by name or ``EMBEDDING_PROVIDER`` env var.

    Supported values:

    - ``mock`` / ``test``: DeterministicMockEmbeddingProvider
    - ``openai``: OpenAIEmbeddingProvider (requires ``OPENAI_API_KEY``)
    - ``none`` / ``disabled``: Returns None (lexical search only)
    """
    if provider_name is None:
        provider_name = os.getenv("EMBEDDING_PROVIDER", "mock")

    provider_name = provider_name.lower().strip()

    if provider_name in ("mock", "test", "testing"):
        return DeterministicMockEmbeddingProvider()

    if provider_name in ("none", "disabled", ""):
        return None

    if provider_name == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("EMBEDDING_PROVIDER=openai but OPENAI_API_KEY missing.")

        from mip_ai.embeddings.openai import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(
            api_key=api_key,
            model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
            dimensions=int(os.getenv("EMBEDDING_DIMENSION", "1536")),
            base_url=os.getenv("EMBEDDING_BASE_URL", "https://api.openai.com/v1"),
            timeout_seconds=float(os.getenv("EMBEDDING_TIMEOUT", "30")),
            max_batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "20")),
        )

    raise ValueError(f"Unknown EMBEDDING_PROVIDER '{provider_name}'")
