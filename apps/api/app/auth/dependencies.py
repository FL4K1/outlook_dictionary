"""FastAPI dependencies for authentication and authorization.

Provides request-scoped dependencies that downstream route handlers
can use to enforce authentication and authorization requirements.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, Request

from app.auth.events import (
    SecurityEvent,
    SecurityEventType,
    SecurityOutcome,
    security_event_emitter,
)
from app.auth.exceptions import AuthenticationError, InsufficientPermissionsError
from app.auth.policy import PolicyEngine

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.auth.context import AuthenticationContext


async def get_auth_context(request: Request) -> AuthenticationContext:
    """Retrieve the authentication context from the current request.

    Raises:
        AuthenticationError: If the middleware did not attach an
            AuthenticationContext to the request (e.g., public route).
    """
    context = getattr(request.state, "auth_context", None)
    if context is None:
        raise AuthenticationError("No authentication context on request.")
    return context  # type: ignore[no-any-return]


def require_permission(*required_permissions: str) -> Callable[..., AuthenticationContext]:
    """Dependency factory that requires one of the specified permissions.

    Usage::

        @router.get("/resource")
        async def read(
            context: AuthenticationContext = Depends(require_permission("resource.read")),
        ) -> ...
    """

    async def _dependency(
        context: AuthenticationContext = Depends(get_auth_context),
    ) -> AuthenticationContext:
        policy_engine = PolicyEngine()
        for permission in required_permissions:
            decision = policy_engine.authorize(
                context,
                resource=permission.split(".")[0],
                action=permission.split(".")[1],
            )
            if decision.allowed:
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.AUTHORIZATION_SUCCESS,
                        outcome=SecurityOutcome.SUCCESS,
                        user_id=context.user_id,
                        tenant_id=context.tenant_id,
                        session_id=context.session_id,
                        metadata={"request_id": context.request_id, "permission": permission},
                    )
                )
                return context

        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.AUTHORIZATION_FAILURE,
                outcome=SecurityOutcome.FAILURE,
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                session_id=context.session_id,
                metadata={
                    "request_id": context.request_id,
                    "required_permissions": ",".join(required_permissions),
                },
            )
        )
        raise InsufficientPermissionsError()

    return _dependency  # type: ignore[return-value]


def require_role(*required_roles: str) -> Callable[..., AuthenticationContext]:
    """Dependency factory that requires one of the specified role names.

    Usage::

        @router.delete("/resource")
        async def delete(
            context: AuthenticationContext = Depends(require_role("admin")),
        ) -> ...
    """

    async def _dependency(
        context: AuthenticationContext = Depends(get_auth_context),
    ) -> AuthenticationContext:
        for role_name in required_roles:
            if role_name in context.role_names:
                security_event_emitter.emit(
                    SecurityEvent(
                        event_type=SecurityEventType.AUTHORIZATION_SUCCESS,
                        outcome=SecurityOutcome.SUCCESS,
                        user_id=context.user_id,
                        tenant_id=context.tenant_id,
                        session_id=context.session_id,
                        metadata={"request_id": context.request_id, "role": role_name},
                    )
                )
                return context

        security_event_emitter.emit(
            SecurityEvent(
                event_type=SecurityEventType.AUTHORIZATION_FAILURE,
                outcome=SecurityOutcome.FAILURE,
                user_id=context.user_id,
                tenant_id=context.tenant_id,
                session_id=context.session_id,
                metadata={
                    "request_id": context.request_id,
                    "required_roles": ",".join(required_roles),
                },
            )
        )
        raise InsufficientPermissionsError()

    return _dependency  # type: ignore[return-value]


def require_tenant_membership() -> Callable[..., AuthenticationContext]:
    """Dependency factory that verifies active tenant membership.

    Usage::

        @router.get("/tenant/resource")
        async def read(
            context: AuthenticationContext = Depends(require_tenant_membership()),
        ) -> ...
    """

    async def _dependency(
        context: AuthenticationContext = Depends(get_auth_context),
    ) -> AuthenticationContext:
        if context.membership_id is None:
            security_event_emitter.emit(
                SecurityEvent(
                    event_type=SecurityEventType.AUTHORIZATION_FAILURE,
                    outcome=SecurityOutcome.FAILURE,
                    user_id=context.user_id,
                    tenant_id=context.tenant_id,
                    session_id=context.session_id,
                    metadata={"request_id": context.request_id, "reason": "No active membership"},
                )
            )
            raise AuthenticationError("No active tenant membership.")
        return context

    return _dependency  # type: ignore[return-value]
