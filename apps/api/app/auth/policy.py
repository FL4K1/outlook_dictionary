"""Policy engine for authorization decisions.

PolicyEngine is a default-deny authorization component.
It operates on fully-resolved AuthenticationContext objects and returns
immutable AuthorizationDecision values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.auth.exceptions import InsufficientPermissionsError, TenantAccessDeniedError
from app.auth.public_routes import PUBLIC_ROUTES

if TYPE_CHECKING:
    import uuid

    from app.auth.context import AuthenticationContext


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """Immutable authorization decision returned by PolicyEngine."""

    allowed: bool
    reason: str | None = None
    missing_permissions: frozenset[str] = frozenset()


class PolicyEngine:
    """Enforce authorization policy on authenticated requests.

    Default-deny: if no required_permissions and no required_role_ids
    are specified, access is denied unless the route is public.

    PolicyEngine operates purely on in-memory AuthenticationContext objects.
    Repository dependencies are accepted for EDD compliance and future expansion.
    """

    def __init__(
        self,
        membership_repo: object | None = None,
        role_repo: object | None = None,
        permission_repo: object | None = None,
    ) -> None:
        self._public_routes = PUBLIC_ROUTES
        self._membership_repo = membership_repo
        self._role_repo = role_repo
        self._permission_repo = permission_repo

    def is_public_route(self, path: str, method: str) -> bool:
        """Return True if the route is exempt from authentication."""
        return (method.upper(), path) in self._public_routes

    def authorize(
        self,
        context: AuthenticationContext,
        resource: str,
        action: str,
        resource_owner_id: uuid.UUID | None = None,
    ) -> AuthorizationDecision:
        """Make an authorization decision.

        Default-deny: if no permissions match, access is denied.

        Args:
            context: The authenticated request context.
            resource: The resource type (e.g., "mail_account", "user").
            action: The action (e.g., "read", "write", "delete").
            resource_owner_id: Optional owner of the resource for ownership checks.

        Returns:
            AuthorizationDecision with allowed flag and reason.
        """
        required_permission = f"{resource}.{action}"

        if context.is_service_account:
            return AuthorizationDecision(
                allowed=required_permission in context.permissions,
                reason=None,
            )

        if not context.permissions:
            return AuthorizationDecision(
                allowed=False,
                reason="No permissions granted",
                missing_permissions=frozenset({required_permission}),
            )

        if required_permission in context.permissions:
            return AuthorizationDecision(allowed=True, reason=None)

        if resource_owner_id is not None and context.user_id == resource_owner_id:
            return AuthorizationDecision(allowed=True, reason="Resource owner")

        return AuthorizationDecision(
            allowed=False,
            reason="Insufficient permissions",
            missing_permissions=frozenset({required_permission}),
        )

    def enforce(
        self,
        context: AuthenticationContext,
        required_permissions: frozenset[str] | None = None,
        required_role_ids: frozenset[uuid.UUID] | None = None,
        resource_tenant_id: uuid.UUID | None = None,
    ) -> None:
        """Legacy enforce method for backward compatibility.

        Raises:
            TenantAccessDeniedError: Tenant isolation violation.
            InsufficientPermissionsError: Missing permissions or roles.
        """
        if resource_tenant_id is not None and context.tenant_id != resource_tenant_id:
            raise TenantAccessDeniedError()

        if required_permissions is not None and not context.has_all_permissions(
            *required_permissions
        ):
            raise InsufficientPermissionsError()

        if required_role_ids is not None and not (required_role_ids <= context.role_ids):
            raise InsufficientPermissionsError()
