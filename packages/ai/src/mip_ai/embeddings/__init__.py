"""Embeddings sub-package."""

from mip_ai.embeddings.base import EmbeddingProvider, EmbeddingResult
from mip_ai.embeddings.mock import DeterministicMockEmbeddingProvider

__all__ = [
    "DeterministicMockEmbeddingProvider",
    "EmbeddingProvider",
    "EmbeddingResult",
]
