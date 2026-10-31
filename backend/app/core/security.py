"""Security middleware, origin validation, and session-token authentication."""

import hmac

from fastapi import Header, Request, WebSocket, status

from app.core.config import settings
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.logging import logger

SESSION_TOKEN_HEADER = "x-orbit-session-token"


def is_origin_allowed(origin: str | None) -> bool:
    """Validate that Origin is an authorized Tauri shell or local dev origin."""
    if not origin:
        # Direct loopback calls (CLI or health probes) omit Origin
        return True

    # Normalize origin
    origin_lower = origin.lower().rstrip("/")

    # Check exact allowed origins
    for allowed in settings.allowed_origins:
        if origin_lower == allowed.lower().rstrip("/"):
            return True

    # Check local port bindings during dev
    return bool(origin_lower.startswith(("http://127.0.0.1:", "http://localhost:")))


def verify_session_token(token: str | None) -> bool:
    """Verify session token using constant-time comparison to prevent timing attacks."""
    expected_token = settings.session_token

    # If no token is configured in environment, require explicit configuration in non-testing
    if not expected_token:
        return settings.app_env == "testing"

    if not token:
        return False

    return hmac.compare_digest(token.encode("utf-8"), expected_token.encode("utf-8"))


async def require_session_auth(
    request: Request,
    x_orbit_session_token: str | None = Header(default=None, alias=SESSION_TOKEN_HEADER),
) -> None:
    """FastAPI dependency for endpoints requiring verified session token and origin."""
    # 1. Validate Origin header if present
    origin = request.headers.get("origin")
    if origin and not is_origin_allowed(origin):
        logger.warning(f"Access forbidden: rejected request with unauthorized origin '{origin}'")
        raise PermissionDeniedError(message=f"Access denied: Origin '{origin}' is not authorized.")

    # 2. Validate Session Token
    if not verify_session_token(x_orbit_session_token):
        logger.warning("Authentication failed: invalid or missing session token")
        raise AuthenticationError(message="Invalid or missing X-Orbit-Session-Token header.")


async def authenticate_websocket(websocket: WebSocket) -> bool:
    """Authenticate incoming WebSocket connection. Returns True if valid, False if rejected."""
    # 1. Check Origin
    origin = websocket.headers.get("origin")
    if origin and not is_origin_allowed(origin):
        logger.warning("WebSocket rejected: unauthorized origin '%s'", origin)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized origin")
        return False

    # 2. Extract Session Token (Sec-WebSocket-Protocol, HTTP header, or fallback query parameter)
    ws_token = None
    proto_header = websocket.headers.get("sec-websocket-protocol")
    if proto_header:
        protocols = [p.strip() for p in proto_header.split(",")]
        if "orbit-auth" in protocols:
            idx = protocols.index("orbit-auth")
            if idx + 1 < len(protocols):
                ws_token = protocols[idx + 1]

    if not ws_token:
        ws_token = websocket.headers.get(SESSION_TOKEN_HEADER) or websocket.query_params.get(
            "token"
        )

    if not verify_session_token(ws_token):
        logger.warning("WebSocket rejected: missing or invalid session token")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid session token")
        return False

    return True
