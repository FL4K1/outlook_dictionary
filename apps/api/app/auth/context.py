"""Immutable request-scoped authentication context.

AuthenticationContext is the single object downstream services receive after
middleware verifies the access token, validates the backing session, resolves
tenant context, and loads server-side permissions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class AuthenticationContext:
    """Immutable authentication context for the current request.

    Business services must consume this object instead of parsing JWTs,
    inspecting headers, or loading authorization state independently.
    """

    request_id: str
    correlation_id: str
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    membership_id: uuid.UUID | None = None
    role_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)
    permissions: frozenset[str] = field(default_factory=frozenset)
    authentication_method: str = "session"
    provider: str | None = None
    authenticated_at: datetime | None = None
    request_ip: str | None = None
    user_agent: str | None = None
    is_service_account: bool = False

    def has_permission(self, codename: str) -> bool:
        """Check if the authenticated principal has a specific permission."""
        return codename in self.permissions

    def has_any_permission(self, *codenames: str) -> bool:
        """Check if the authenticated principal has any of the given permissions."""
        return bool(self.permissions & set(codenames))

    def has_all_permissions(self, *codenames: str) -> bool:
        """Check if the authenticated principal has all of the given permissions."""
        return set(codenames) <= self.permissions


ANONYMOUS_CONTEXT: AuthenticationContext | None = None
