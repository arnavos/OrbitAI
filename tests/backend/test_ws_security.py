"""Tests for WebSocket authentication and origin policy enforcement."""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tests.backend.conftest import TEST_SESSION_TOKEN

from app.main import app


def test_websocket_rejects_missing_token():
    """Verify WebSocket connection without a token is rejected with 1008 policy violation."""
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info, client.websocket_connect("/api/v1/ws"):
        pass
    assert exc_info.value.code == 1008


def test_websocket_rejects_invalid_token():
    """Verify WebSocket connection with forged token is rejected."""
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws?token=wrong-token"):
            pass
    assert exc_info.value.code == 1008


def test_websocket_rejects_unauthorized_origin():
    """Verify WebSocket connection with unauthorized Origin header is rejected."""
    client = TestClient(app)
    headers = {"origin": "http://evil-attacker.com"}
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/ws?token={TEST_SESSION_TOKEN}", headers=headers):
            pass
    assert exc_info.value.code == 1008


def test_websocket_accepts_valid_token_and_origin():
    """Verify WebSocket connection succeeds with valid credentials and responds to ping."""
    client = TestClient(app)
    headers = {"origin": "tauri://localhost"}
    with client.websocket_connect(f"/api/v1/ws?token={TEST_SESSION_TOKEN}", headers=headers) as ws:
        # First message is connection confirmation
        init_data = json.loads(ws.receive_text())
        assert init_data["type"] == "CONNECTION_ESTABLISHED"
        assert "uptime" in init_data

        # Send ping
        ws.send_text(json.dumps({"action": "ping"}))
        pong_data = json.loads(ws.receive_text())
        assert pong_data["type"] == "pong"


def test_websocket_accepts_subprotocol_auth_without_query_params():
    """Verify WebSocket connection succeeds via Sec-WebSocket-Protocol without tokens in URL."""
    client = TestClient(app)
    headers = {"origin": "tauri://localhost"}
    subprotocols = ["orbit-auth", TEST_SESSION_TOKEN]
    with client.websocket_connect("/api/v1/ws", subprotocols=subprotocols, headers=headers) as ws:
        init_data = json.loads(ws.receive_text())
        assert init_data["type"] == "CONNECTION_ESTABLISHED"

        # Query live telemetry
        ws.send_text(json.dumps({"action": "get_telemetry"}))
        telem = json.loads(ws.receive_text())
        assert telem["type"] == "TELEMETRY_UPDATE"
        assert "memory_rss_mb" in telem["data"]
        assert telem["data"]["database_connected"] is True

        # Query system info
        ws.send_text(json.dumps({"action": "get_system_info"}))
        info = json.loads(ws.receive_text())
        assert info["type"] == "SYSTEM_INFO"
        assert "architecture" in info["data"]


def test_websocket_rejects_invalid_subprotocol_token():
    """Verify WebSocket connection via Sec-WebSocket-Protocol with invalid token is rejected."""
    client = TestClient(app)
    headers = {"origin": "tauri://localhost"}
    subprotocols = ["orbit-auth", "forged-token"]
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/ws", subprotocols=subprotocols, headers=headers):
            pass
    assert exc_info.value.code == 1008
