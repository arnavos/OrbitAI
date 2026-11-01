"""Tests for session-token authentication and origin boundary validation."""

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.security import verify_session_token


@pytest.mark.asyncio
async def test_endpoint_rejects_missing_token(async_client: AsyncClient):
    """Verify missing session token produces a normalized 401 error envelope."""
    response = await async_client.get("/api/v1/system/info")
    assert response.status_code == 401
    payload = response.json()

    assert payload["success"] is False
    assert payload["data"] is None
    assert payload["error"]["code"] == "AUTHENTICATION_FAILED"
    assert "Invalid or missing" in payload["error"]["message"]
    assert "correlation_id" in payload


@pytest.mark.asyncio
async def test_endpoint_rejects_invalid_token(async_client: AsyncClient):
    """Verify an invalid or forged session token produces 401."""
    headers = {"x-orbit-session-token": "forged-token-abc-123"}
    response = await async_client.get("/api/v1/system/info", headers=headers)
    assert response.status_code == 401
    payload = response.json()

    assert payload["success"] is False
    assert payload["error"]["code"] == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_endpoint_accepts_valid_token(
    async_client: AsyncClient, auth_headers: dict[str, str]
):
    """Verify a valid session token grants access."""
    response = await async_client.get("/api/v1/system/info", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()

    assert payload["success"] is True
    assert payload["data"]["app_name"] == "ORBIT"
    assert payload["error"] is None


@pytest.mark.asyncio
async def test_origin_validation_blocks_unauthorized_browser_pages(
    async_client: AsyncClient, auth_headers: dict[str, str]
):
    """Verify requests originating from unauthorized external or untrusted domains are rejected with 403."""
    evil_headers = {
        **auth_headers,
        "origin": "http://malicious-site.com",
    }
    response = await async_client.get("/api/v1/system/info", headers=evil_headers)
    assert response.status_code == 403
    payload = response.json()

    assert payload["success"] is False
    assert payload["error"]["code"] == "PERMISSION_DENIED"
    assert "not authorized" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_origin_validation_permits_tauri_and_local_origins(
    async_client: AsyncClient, auth_headers: dict[str, str]
):
    """Verify legitimate Tauri desktop and local webview origins are allowed."""
    valid_origins = [
        "tauri://localhost",
        "http://tauri.localhost",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    for origin in valid_origins:
        headers = {**auth_headers, "origin": origin}
        response = await async_client.get("/api/v1/system/info", headers=headers)
        assert response.status_code == 200, f"Origin '{origin}' should be authorized."


@pytest.mark.asyncio
async def test_app_env_testing_without_credentials_fails_closed(
    async_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """Regression test: verify APP_ENV=testing without credentials fails closed."""
    # 1. Direct function check: missing expected_token must fail closed
    monkeypatch.setattr(settings, "session_token", None)
    monkeypatch.setattr(settings, "app_env", "testing")

    assert verify_session_token("any-token") is False
    assert verify_session_token(None) is False
    assert verify_session_token("") is False

    # 2. HTTP endpoint check: missing expected token in settings must reject requests with 401
    response = await async_client.get(
        "/api/v1/system/info", headers={"x-orbit-session-token": "any-token"}
    )
    assert response.status_code == 401
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "AUTHENTICATION_FAILED"

    # 3. HTTP endpoint check without header must also reject with 401
    response_no_header = await async_client.get("/api/v1/system/info")
    assert response_no_header.status_code == 401
