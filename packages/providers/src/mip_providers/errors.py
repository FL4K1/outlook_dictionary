"""Provider exception taxonomy for mail and identity providers.

All provider-specific exceptions inherit from ProviderError.
Tokens MUST NEVER be included in exception messages, reprs, or attributes.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base exception for all provider operations."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        request_id: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after = retry_after

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code is not None:
            parts.append(f"(status_code={self.status_code})")
        if self.request_id:
            parts.append(f"(request_id='{self.request_id}')")
        return " ".join(parts)


class AuthExpiredError(ProviderError):
    """Raised when provider access token is expired or invalid (HTTP 401)."""


class ProviderPermissionError(ProviderError):
    """Raised when access is forbidden due to missing scope or permission (HTTP 403)."""


class ProviderNotFoundError(ProviderError):
    """Raised when a requested provider resource is not found (HTTP 404)."""


class DeltaCursorExpiredError(ProviderError):
    """Raised when a delta sync token or nextLink cursor is expired/invalid (HTTP 410)."""


class ProviderRateLimitedError(ProviderError):
    """Raised when provider rate limits requests (HTTP 429)."""
