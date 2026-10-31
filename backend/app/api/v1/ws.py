"""WebSocket endpoints with origin validation and session authentication."""

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging import logger
from app.core.security import authenticate_websocket
from app.services.system_service import SystemService

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws")
async def websocket_telemetry_endpoint(websocket: WebSocket) -> None:
    """Authenticated WebSocket stream for real-time telemetry and desktop events."""
    # 1. Enforce origin checking and token authentication before accepting
    if not await authenticate_websocket(websocket):
        return

    # 2. Accept connection
    await websocket.accept()
    logger.info("WebSocket client connected successfully.")

    try:
        # Send initial connection handshake confirmation
        await websocket.send_text(
            json.dumps(
                {
                    "type": "CONNECTION_ESTABLISHED",
                    "uptime": SystemService.get_uptime_seconds(),
                }
            )
        )

        while True:
            # Listen for client heartbeat/messages
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                action = msg.get("action")
            except Exception:
                action = "ping"

            if action == "ping":
                await websocket.send_text(
                    json.dumps({"type": "pong", "time": SystemService.get_uptime_seconds()})
                )
            else:
                await websocket.send_text(json.dumps({"type": "ack", "received": action}))
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
