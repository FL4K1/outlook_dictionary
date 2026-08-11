"""Auth module: authentication, authorization, and session management.

PR-1.2 keeps platform authentication core provider-agnostic. External IdP
callbacks and provider-specific login adapters belong to PR-1.3.
"""

from app.auth.context import ANONYMOUS_CONTEXT, AuthenticationContext
from app.auth.dependencies import (
    get_auth_context,
    require_permission,
    require_role,
    require_tenant_membership,
)
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
from app.auth.middleware import AuthenticationMiddleware
from app.auth.policy import AuthorizationDecision, PolicyEngine
from app.auth.public_routes import PUBLIC_ROUTES, is_public_route
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
    "PUBLIC_ROUTES",
    "AccessTokenSubject",
    "AuthenticationContext",
    "AuthenticationError",
    "AuthenticationMiddleware",
    "AuthenticationResult",
    "AuthenticationService",
    "AuthorizationDecision",
    "HmacSigningProvider",
    "InsufficientPermissionsError",
    "PolicyEngine",
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
    "get_auth_context",
    "is_public_route",
    "require_permission",
    "require_role",
    "require_tenant_membership",
    "security_event_emitter",
]
