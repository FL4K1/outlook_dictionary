"""OpenAI Embedding Provider.

Production embedding provider using the OpenAI embeddings API via raw httpx.
No ``openai`` SDK dependency — just HTTP POST to ``/v1/embeddings``.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

import httpx

from mip_ai.embeddings.base import EmbeddingProvider, EmbeddingResult
from mip_ai.embeddings.errors import (
    EmbeddingPermanentError,
    EmbeddingTransientError,
)

logger = logging.getLogger(__name__)

# Status codes that should be retried
_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Production embedding provider backed by the OpenAI API.

    Uses ``httpx.AsyncClient`` for async HTTP calls.  Supports batching
    up to *max_batch_size* texts per request and validates that returned
    vectors match the expected *dimensions*.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
        max_batch_size: int = 20,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_batch_size = max_batch_size

    # ---- EmbeddingProvider protocol ----

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    # ---- Core ----

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """Generate embeddings via the OpenAI embeddings API.

        Raises:
            EmbeddingTransientError: on 429/5xx, timeout, or network errors.
            EmbeddingPermanentError: on 4xx (non-429), dimension mismatch,
                or malformed API responses.
        """
        if not texts:
            return EmbeddingResult(
                vectors=[],
                model_id=self._model,
                dimensions=self._dimensions,
                total_tokens=0,
                latency_ms=0.0,
                provider="openai",
            )

        all_vectors: list[list[float]] = []
        total_tokens = 0
        start = time.perf_counter_ns()

        # Process in batches
        for i in range(0, len(texts), self._max_batch_size):
            batch = texts[i : i + self._max_batch_size]
            batch_result = await self._call_api(batch)
            all_vectors.extend(batch_result["vectors"])
            total_tokens += batch_result["tokens"]

        latency_ms = (time.perf_counter_ns() - start) / 1_000_000.0

        return EmbeddingResult(
            vectors=all_vectors,
            model_id=self._model,
            dimensions=self._dimensions,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            provider="openai",
        )

    async def _call_api(self, texts: list[str]) -> dict[str, Any]:
        """Make a single API call for a batch of texts."""
        url = f"{self._base_url}/embeddings"
        payload = {
            "input": texts,
            "model": self._model,
            "dimensions": self._dimensions,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout)) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                raise EmbeddingTransientError(
                    f"OpenAI request timed out after {self._timeout}s"
                ) from exc
            except httpx.NetworkError as exc:
                raise EmbeddingTransientError(f"OpenAI network error: {exc}") from exc

        if response.status_code in _TRANSIENT_STATUS_CODES:
            retry_after: float | None = None
            raw = response.headers.get("Retry-After")
            if raw:
                with contextlib.suppress(ValueError):
                    retry_after = float(raw)
            raise EmbeddingTransientError(
                f"OpenAI transient HTTP {response.status_code}",
                retry_after=retry_after,
            )

        if response.status_code != 200:
            raise EmbeddingPermanentError(
                f"OpenAI HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            body = response.json()
        except Exception as exc:
            raise EmbeddingPermanentError("OpenAI returned invalid JSON") from exc

        data = body.get("data")
        if not isinstance(data, list):
            raise EmbeddingPermanentError("OpenAI response missing 'data' array")

        vectors: list[list[float]] = []
        for item in sorted(data, key=lambda d: d.get("index", 0)):
            vec = item.get("embedding")
            if not isinstance(vec, list) or len(vec) != self._dimensions:
                raise EmbeddingPermanentError(
                    f"Dimension mismatch: expected {self._dimensions}, "
                    f"got {len(vec) if isinstance(vec, list) else 'null'}"
                )
            vectors.append(vec)

        usage = body.get("usage", {})
        tokens = usage.get("total_tokens", 0)

        logger.debug(
            "OpenAI embed batch=%d tokens=%d model=%s",
            len(texts),
            tokens,
            self._model,
        )

        return {"vectors": vectors, "tokens": tokens}
