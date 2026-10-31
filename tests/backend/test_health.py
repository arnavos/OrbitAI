"""Tests for public and detailed health endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_public_health_probe(async_client: AsyncClient):
    """Verify that GET /api/v1/health is accessible without authentication."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()

    assert payload["success"] is True
    assert payload["data"]["status"] == "healthy"
    assert "version" in payload["data"]
    assert "uptime_seconds" in payload["data"]
    assert payload["correlation_id"] is not None
    assert "x-correlation-id" in response.headers


@pytest.mark.asyncio
async def test_detailed_health_requires_auth(async_client: AsyncClient):
    """Verify that GET /api/v1/health/detailed rejects unauthenticated callers with 401."""
    response = await async_client.get("/api/v1/health/detailed")
    assert response.status_code == 401
    payload = response.json()

    assert payload["success"] is False
    assert payload["error"]["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_detailed_health_with_valid_auth(async_client: AsyncClient, auth_headers: dict[str, str]):
    """Verify that authenticated GET /api/v1/health/detailed returns resource metrics and WAL diagnostics."""
    response = await async_client.get("/api/v1/health/detailed", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()

    assert payload["success"] is True
    data = payload["data"]
    assert data["status"] in ("healthy", "degraded")
    assert data["memory_rss_mb"] > 0
    assert data["pid"] > 0
    assert "cpu_percent" in data
    assert data["database_connected"] is True
    assert data["database_latency_ms"] >= 0
    assert data["journal_mode"].lower() == "wal"
