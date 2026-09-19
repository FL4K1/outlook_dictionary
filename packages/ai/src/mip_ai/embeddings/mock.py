"""Mock embedding provider for deterministic testing."""

import hashlib
import time
from typing import ClassVar

from mip_ai.embeddings.base import EmbeddingProvider, EmbeddingResult


class DeterministicMockEmbeddingProvider(EmbeddingProvider):
    """Generates deterministic embeddings based on input text hashes.

    Used strictly for local development and integration testing so the test suite
    does not depend on expensive external API calls.
    """

    # Matches OpenAI text-embedding-3-small default for compatibility testing
    _dimensions: ClassVar[int] = 1536
    _model_id: ClassVar[str] = "mock-embedding-v1"

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """Generate deterministic pseudo-random vectors."""
        start = time.perf_counter_ns()

        vectors = []
        total_chars = 0

        for text in texts:
            # Deterministic seed from content
            text_hash = hashlib.sha256(text.encode("utf-8")).digest()
            total_chars += len(text)

            # Generate a pseudo-random normalized vector by repeating hash bytes
            # This isn't semantically meaningful but is deterministic for ES tests
            vector = []
            for i in range(self._dimensions):
                val = float(text_hash[i % 32]) / 255.0  # Normalize 0.0 - 1.0
                vector.append(val)

            # Quick L2 normalization approximation
            magnitude = sum(x * x for x in vector) ** 0.5
            if magnitude > 0:
                vector = [x / magnitude for x in vector]

            vectors.append(vector)

        latency_ms = (time.perf_counter_ns() - start) / 1_000_000.0

        return EmbeddingResult(
            vectors=vectors,
            model_id=self.model_id,
            dimensions=self.dimensions,
            total_tokens=total_chars // 4,  # Rough approximation for mock
            latency_ms=latency_ms,
            provider="mock",
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions
