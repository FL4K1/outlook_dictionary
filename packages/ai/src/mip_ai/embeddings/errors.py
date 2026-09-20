"""Embedding provider error hierarchy.

Provides typed error classification for retry vs dead-letter decisions
in the embedding job pipeline.
"""

from __future__ import annotations


class EmbeddingError(Exception):
    """Base error for all embedding provider failures."""


class EmbeddingTransientError(EmbeddingError):
    """Transient/retryable failure (429, 5xx, timeout, network).

    The caller should retry with exponential backoff.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class EmbeddingPermanentError(EmbeddingError):
    """Permanent/non-retryable failure (401, 400, dimension mismatch).

    The caller should dead-letter rather than retry.
    """
