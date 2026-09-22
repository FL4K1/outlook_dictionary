"""Query understanding provider error hierarchy."""

from __future__ import annotations


class QueryUnderstandingError(Exception):
    """Base error for all query understanding failures."""


class QueryUnderstandingTransientError(QueryUnderstandingError):
    """Transient failure (5xx, timeout, network error)."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class QueryUnderstandingRateLimitError(QueryUnderstandingTransientError):
    """Rate limit failure (429)."""


class QueryUnderstandingMalformedOutputError(QueryUnderstandingError):
    """Provider output was unparseable or failed Pydantic schema validation."""


class QueryUnderstandingConfigurationError(QueryUnderstandingError):
    """Provider configuration error (missing key, invalid setting)."""


class QueryUnderstandingPermanentError(QueryUnderstandingError):
    """Permanent/unrecoverable provider error (401, 400, etc.)."""
