"""Tests for system settings and audit event integration."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_and_retrieve_setting(async_client: AsyncClient, auth_headers: dict[str, str]):
    """Verify persisting and retrieving a system setting."""
    payload = {
        "key": "ui.theme",
        "value": {"mode": "dark", "accent": "indigo"},
        "is_encrypted": False,
    }
    post_res = await async_client.post("/api/v1/system/settings", json=payload, headers=auth_headers)
    assert post_res.status_code == 200
    post_data = post_res.json()["data"]
    assert post_data["key"] == "ui.theme"
    assert post_data["value"]["mode"] == "dark"

    # Retrieve
    get_res = await async_client.get("/api/v1/system/settings/ui.theme", headers=auth_headers)
    assert get_res.status_code == 200
    get_data = get_res.json()["data"]
    assert get_data["key"] == "ui.theme"
    assert get_data["value"]["accent"] == "indigo"


@pytest.mark.asyncio
async def test_setting_not_found(async_client: AsyncClient, auth_headers: dict[str, str]):
    """Verify requesting a non-existent setting returns 404."""
    res = await async_client.get("/api/v1/system/settings/non_existent_key", headers=auth_headers)
    assert res.status_code == 404
    payload = res.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_setting_update_logs_audit_event(async_client: AsyncClient, auth_headers: dict[str, str]):
    """Verify that updating a setting automatically appends an audit event."""
    payload = {"key": "network.proxy", "value": "direct", "is_encrypted": False}
    await async_client.post("/api/v1/system/settings", json=payload, headers=auth_headers)

    audit_res = await async_client.get("/api/v1/system/audit", headers=auth_headers)
    assert audit_res.status_code == 200
    events = audit_res.json()["data"]

    # Locate the SETTING_UPDATED event
    found = any(e["event_type"] == "SETTING_UPDATED" and e["payload"].get("key") == "network.proxy" for e in events)
    assert found is True, "Audit log must contain SETTING_UPDATED event"
