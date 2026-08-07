"""Auth module: authentication, authorization, and session management.

PR-1.2 keeps platform authentication core provider-agnostic. External IdP
callbacks and provider-specific login adapters belong to PR-1.3.
"""

from app.auth.context import ANONYMOUS_CONTEXT, AuthenticationContext
from app.auth.events import (
    SecurityEvent,
    SecurityEventEmitter,
    SecurityEventType,
    SecurityOutcome,
    security_event_emitter,
)
from app.auth.exceptions import (
    AuthenticationError,
    InsufficientPermissionsError,
    ProviderAuthenticationError,
    RefreshTokenReusedError,
    SessionExpiredError,
    SessionRevokedError,
    TenantAccessDeniedError,
    TokenExpiredError,
    TokenInvalidError,
)
from app.auth.service import AuthenticationResult, AuthenticationService
from app.auth.sessions import SessionService
from app.auth.tokens import (
    AccessTokenSubject,
    HmacSigningProvider,
    RefreshTokenPair,
    SigningProvider,
    TokenService,
)

__all__ = [
    "ANONYMOUS_CONTEXT",
    "AccessTokenSubject",
    "AuthenticationContext",
    "AuthenticationError",
    "AuthenticationResult",
    "AuthenticationService",
    "HmacSigningProvider",
    "InsufficientPermissionsError",
    "ProviderAuthenticationError",
    "RefreshTokenPair",
    "RefreshTokenReusedError",
    "SecurityEvent",
    "SecurityEventEmitter",
    "SecurityEventType",
    "SecurityOutcome",
    "SessionExpiredError",
    "SessionRevokedError",
    "SessionService",
    "SigningProvider",
    "TenantAccessDeniedError",
    "TokenExpiredError",
    "TokenInvalidError",
    "TokenService",
    "security_event_emitter",
]
