"""Authentication and authorization exceptions.

Extends the base application exception hierarchy from app.common.exceptions
with auth-specific error types. Each maps to a precise HTTP status code
and error code for consistent API responses.

These exceptions are raised by auth services and caught by the global
exception handlers — no try/except needed in route handlers.
"""

from __future__ import annotations

from fastapi import status

from app.common.exceptions import AppError


class AuthenticationError(AppError):
    """Base class for all authentication failures.

    Results in HTTP 401. Used when a request cannot be authenticated
    (missing token, expired token, invalid signature, etc.).
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "AUTHENTICATION_FAILED"
    message = "Authentication failed."


class TokenExpiredError(AuthenticationError):
    """The access or refresh token has expired."""

    error_code = "TOKEN_EXPIRED"
    message = "The token has expired."


class TokenInvalidError(AuthenticationError):
    """The token is malformed, has an invalid signature, or is otherwise unusable."""

    error_code = "TOKEN_INVALID"
    message = "The token is invalid."


class SessionExpiredError(AuthenticationError):
    """The session has exceeded its idle or absolute timeout."""

    error_code = "SESSION_EXPIRED"
    message = "The session has expired."


class SessionRevokedError(AuthenticationError):
    """The session has been explicitly revoked."""

    error_code = "SESSION_REVOKED"
    message = "The session has been revoked."


class RefreshTokenReusedError(AuthenticationError):
    """A previously consumed refresh token was presented again.

    This is a critical security event — it indicates the refresh token
    may have been stolen. The SessionService revokes ALL sessions for
    the affected user when this is detected (ADR-008).
    """

    error_code = "REFRESH_TOKEN_REUSED"
    message = "Refresh token reuse detected. All sessions have been revoked."


class InsufficientPermissionsError(AppError):
    """The authenticated user lacks the required permission(s).

    Results in HTTP 403. The user IS authenticated but is NOT
    authorized for the requested action.
    """

    status_code = status.HTTP_403_FORBIDDEN
    error_code = "INSUFFICIENT_PERMISSIONS"
    message = "You do not have the required permissions for this action."


class TenantAccessDeniedError(AppError):
    """The user does not have membership in the requested tenant.

    Results in HTTP 403. This enforces strict tenant isolation —
    a user cannot access resources in a tenant they don't belong to.
    """

    status_code = status.HTTP_403_FORBIDDEN
    error_code = "TENANT_ACCESS_DENIED"
    message = "You do not have access to this tenant."


class ProviderAuthenticationError(AuthenticationError):
    """An external identity provider returned an error during OAuth.

    Wraps provider-specific errors (e.g., Entra ID error codes)
    into a consistent application exception.
    """

    error_code = "PROVIDER_AUTHENTICATION_FAILED"
    message = "Authentication with the identity provider failed."
