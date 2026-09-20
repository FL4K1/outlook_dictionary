"""Mail Intelligence Platform — Embedding abstractions."""

from mip_ai.embeddings.base import EmbeddingProvider, EmbeddingResult
from mip_ai.embeddings.errors import (
    EmbeddingError,
    EmbeddingPermanentError,
    EmbeddingTransientError,
)
from mip_ai.embeddings.factory import get_embedding_provider
from mip_ai.embeddings.text import build_semantic_text

__all__ = [
    "EmbeddingError",
    "EmbeddingPermanentError",
    "EmbeddingProvider",
    "EmbeddingResult",
    "EmbeddingTransientError",
    "build_semantic_text",
    "get_embedding_provider",
]
