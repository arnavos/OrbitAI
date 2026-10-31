"""WebSocket endpoints with origin validation and session authentication."""

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging import logger
from app.core.security import authenticate_websocket
from app.db.session import async_session_factory
from app.services.system_service import SystemService

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws")
async def websocket_telemetry_endpoint(websocket: WebSocket) -> None:
    """Authenticated WebSocket stream for real-time telemetry and desktop events."""
    # 1. Enforce origin checking and token authentication before accepting
    if not await authenticate_websocket(websocket):
        return

    # 2. Negotiate subprotocol if requested via Sec-WebSocket-Protocol
    proto = websocket.headers.get("sec-websocket-protocol")
    subprotocol = "orbit-auth" if proto and "orbit-auth" in proto else None
    await websocket.accept(subprotocol=subprotocol)
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
            elif action == "get_telemetry":
                async with async_session_factory() as session:
                    detailed = await SystemService.get_detailed_health(session)
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "TELEMETRY_UPDATE",
                                "data": detailed.model_dump(),
                            }
                        )
                    )
            elif action == "get_system_info":
                async with async_session_factory() as session:
                    info = await SystemService.get_system_info(session)
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "SYSTEM_INFO",
                                "data": info.model_dump(),
                            }
                        )
                    )
            elif action == "get_audit_events":
                async with async_session_factory() as session:
                    events = await SystemService.list_audit_events(session, limit=20)
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "AUDIT_EVENTS",
                                "data": [e.model_dump(mode="json") for e in events],
                            }
                        )
                    )
            else:
                await websocket.send_text(json.dumps({"type": "ack", "received": action}))
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
