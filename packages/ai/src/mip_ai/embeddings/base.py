"""Embedding Provider Protocol interfaces.

Defines the contract for all embedding provider implementations.
Architecture reference: Blueprint Section 3.11.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EmbeddingResult:
    """Result from an embedding generation request."""

    vectors: list[list[float]]
    model_id: str
    dimensions: int
    total_tokens: int
    latency_ms: float
    provider: str
    metadata: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Interface for embedding generation providers.

    Every embedding stored in the platform MUST be tagged with the
    model_id from the provider that generated it, enabling migration
    when embedding models change.
    """

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """Generate embeddings for a batch of texts."""
        ...

    @property
    def model_id(self) -> str:
        """Unique identifier for this model version (used for versioning)."""
        ...

    @property
    def dimensions(self) -> int:
        """Vector dimensionality."""
        ...
