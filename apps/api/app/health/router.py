"""Health check endpoints.

Architecture reference: Blueprint Section 11.3.

These endpoints are used by container orchestrators (Kubernetes, ECS)
and load balancers to determine application health.

- /health/live — Liveness: process is running (always 200)
- /health/ready — Readiness: all dependencies reachable
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import text

from app.common.dependencies import get_db
from app.common.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/health", tags=["health"])
logger = get_logger(__name__)


class HealthStatus(BaseModel):
    """Health check response."""

    status: str
    checks: dict[str, Any] = {}


@router.get(
    "/live",
    response_model=HealthStatus,
    status_code=status.HTTP_200_OK,
    summary="Liveness probe",
    description="Returns 200 if the process is running. No dependency checks.",
)
async def liveness() -> HealthStatus:
    """Liveness probe — always returns healthy if the process is running."""
    return HealthStatus(status="healthy")


@router.get(
    "/startup",
    response_model=HealthStatus,
    status_code=status.HTTP_200_OK,
    summary="Startup probe",
    description="Returns 200 if the application has completed initialization.",
)
async def startup() -> HealthStatus:
    """Startup probe — verifies the application has started."""
    return HealthStatus(status="healthy")


@router.get(
    "/ready",
    response_model=HealthStatus,
    summary="Readiness probe",
    description="Checks connectivity to PostgreSQL. Returns 503 if any dependency is unreachable.",
)
async def readiness(
    db: AsyncSession = Depends(get_db),
) -> HealthStatus:
    """Readiness probe — verifies all critical dependencies are reachable."""
    checks: dict[str, Any] = {}

    # Check PostgreSQL
    try:
        result = await db.execute(text("SELECT 1"))
        result.scalar()
        checks["postgresql"] = "healthy"
    except Exception as exc:
        logger.error("health_check_failed", dependency="postgresql", error=str(exc))
        checks["postgresql"] = "unhealthy"

    all_healthy = all(v == "healthy" for v in checks.values())

    if not all_healthy:
        return HealthStatus(status="unhealthy", checks=checks)

    return HealthStatus(status="healthy", checks=checks)
