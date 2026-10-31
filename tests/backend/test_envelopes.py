"""Tests verifying strict adherence to standardized API response envelopes."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_envelope_structure_on_success(async_client: AsyncClient):
    """Verify success responses contain { success: True, data: ..., error: None, correlation_id, timestamp }."""
    res = await async_client.get("/api/v1/health")
    payload = res.json()

    assert payload["success"] is True
    assert payload["data"] is not None
    assert payload["error"] is None
    assert isinstance(payload["correlation_id"], str) and len(payload["correlation_id"]) > 0
    assert "timestamp" in payload


@pytest.mark.asyncio
async def test_envelope_structure_on_auth_failure(async_client: AsyncClient):
    """Verify 401 error envelope contains { success: False, data: None, error: ErrorDetail, ... }."""
    res = await async_client.get("/api/v1/system/info")
    payload = res.json()

    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["error"]["code"] == "AUTHENTICATION_FAILED"
    assert isinstance(payload["error"]["message"], str)
    assert isinstance(payload["error"]["details"], dict)
    assert "correlation_id" in payload


@pytest.mark.asyncio
async def test_envelope_structure_on_not_found(async_client: AsyncClient, auth_headers: dict[str, str]):
    """Verify 404 error envelope format."""
    res = await async_client.get("/api/v1/system/settings/unknown_xyz", headers=auth_headers)
    payload = res.json()

    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_envelope_structure_on_validation_error(async_client: AsyncClient, auth_headers: dict[str, str]):
    """Verify 422 validation error envelope format."""
    # Send empty body to a POST route requiring SystemSettingCreate
    res = await async_client.post("/api/v1/system/settings", json={}, headers=auth_headers)
    payload = res.json()

    assert res.status_code == 422
    assert payload["success"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert "errors" in payload["error"]["details"]
