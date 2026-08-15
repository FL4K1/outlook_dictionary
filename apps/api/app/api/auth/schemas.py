"""Pydantic schemas for the authentication API endpoints.

PR-1.2.5 defines four public endpoints:
- POST /auth/refresh
- POST /auth/token
- POST /auth/logout
- POST /auth/logout-all

Validation semantics: Missing or structurally invalid fields produce HTTP 422
via FastAPI/Pydantic's default validation pipeline. No custom validators
alter this behavior.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RefreshTokenRequest(BaseModel):
    """Request body for POST /auth/refresh."""

    refresh_token: str = Field(
        ...,
        description="The refresh token to exchange for a new access token.",
    )


class TokenRequest(BaseModel):
    """Request body for POST /auth/token.

    In Phase 1 only the refresh_token grant type is supported.
    """

    grant_type: str = Field(
        ...,
        description="OAuth2 grant type. Only 'refresh_token' is accepted in Phase 1.",
    )
    refresh_token: str = Field(..., description="The refresh token.")
    scope: str | None = Field(
        default=None,
        description="Optional requested scopes. Ignored in Phase 1.",
    )


class TokenResponse(BaseModel):
    """Response body for successful token issuance."""

    access_token: str = Field(..., description="Newly issued access token.")
    refresh_token: str = Field(..., description="Newly issued refresh token.")
    token_type: str = Field(default="bearer", description="Token type.")
    expires_in: int = Field(..., description="Access token lifetime in seconds.")


class LogoutRequest(BaseModel):
    """Request body for POST /auth/logout."""

    refresh_token: str = Field(..., description="The refresh token to revoke.")


class LogoutAllRequest(BaseModel):
    """Request body for POST /auth/logout-all."""

    refresh_token: str = Field(
        ...,
        description=("Refresh token identifying the session whose siblings should be revoked."),
    )
