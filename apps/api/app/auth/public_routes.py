"""Public route definitions and helpers.

Public routes are exempt from authentication and authorization.
The AuthenticationMiddleware skips JWT validation for these paths.
"""

from __future__ import annotations

from typing import ClassVar

PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        ("GET", "/health/startup"),
        ("POST", "/auth/token"),
        ("POST", "/auth/refresh"),
        ("POST", "/auth/logout"),
        ("POST", "/auth/logout-all"),
        ("GET", "/docs"),
        ("GET", "/redoc"),
        ("GET", "/openapi.json"),
    }
)


class PublicRoutes:
    """Constants and helpers for public route detection."""

    _routes: ClassVar[frozenset[tuple[str, str]]] = PUBLIC_ROUTES

    @classmethod
    def is_public(cls, method: str, path: str) -> bool:
        """Return True if the route is exempt from authentication."""
        return (method.upper(), path) in cls._routes


def is_public_route(method: str, path: str) -> bool:
    """Return True if the route is exempt from authentication."""
    return (method.upper(), path) in PUBLIC_ROUTES
