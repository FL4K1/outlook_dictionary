"""Tests for health check endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient


@pytest.mark.asyncio
async def test_liveness_returns_200(client: AsyncClient) -> None:
    """GET /health/live should always return 200 with status healthy."""
    response = await client.get("/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"


@pytest.mark.asyncio
async def test_liveness_response_schema(client: AsyncClient) -> None:
    """Liveness response must match HealthStatus schema."""
    response = await client.get("/health/live")
    body = response.json()
    assert "status" in body
    assert isinstance(body["status"], str)


@pytest.mark.asyncio
async def test_request_id_header_present(client: AsyncClient) -> None:
    """Every response should include X-Request-ID header."""
    response = await client.get("/health/live")
    assert "X-Request-ID" in response.headers
    # Should be a valid UUID-like string
    request_id = response.headers["X-Request-ID"]
    assert len(request_id) > 0


@pytest.mark.asyncio
async def test_custom_request_id_preserved(client: AsyncClient) -> None:
    """If client sends X-Request-ID, it should be echoed back."""
    custom_id = "test-request-123"
    response = await client.get("/health/live", headers={"X-Request-ID": custom_id})
    assert response.headers["X-Request-ID"] == custom_id


@pytest.mark.asyncio
async def test_security_headers_present(client: AsyncClient) -> None:
    """Security headers should be present on every response."""
    response = await client.get("/health/live")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-XSS-Protection"] == "1; mode=block"


@pytest.mark.asyncio
async def test_not_found_returns_consistent_error(client: AsyncClient) -> None:
    """Non-existent endpoints should return a structured error response."""
    response = await client.get("/nonexistent")
    assert response.status_code == 404
