"""Shared WebSocket cookie authentication.

BaseHTTPMiddleware does not run for WebSocket upgrades, so WS endpoints
parse the auth cookie manually. This is the single implementation used by
/ws and /ws/telemetry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.identity.auth_middleware import COOKIE_NAME
from shared.streams import AUTH_SESSION_PREFIX

if TYPE_CHECKING:
    from fastapi import WebSocket

    from shared.types import AioRedis


async def authenticate_ws_cookie(websocket: WebSocket, redis: AioRedis) -> bool:
    """True if the WS carries a valid, authenticated alfred_auth session cookie.

    The cookie value is used verbatim (no URL-decoding) — session ids are
    server-generated UUIDs, so this is safe.
    """
    cookie_header: str = websocket.headers.get("cookie", "")
    session_id: str | None = None
    for raw_part in cookie_header.split(";"):
        part = raw_part.strip()
        if part.startswith(f"{COOKIE_NAME}="):
            session_id = part[len(f"{COOKIE_NAME}=") :]
            break
    if not session_id:
        return False
    data: dict[bytes | str, bytes | str] = await redis.hgetall(f"{AUTH_SESSION_PREFIX}{session_id}")
    return bool(data) and data.get(b"authenticated") == b"1"


async def require_ws_auth(websocket: WebSocket, redis: AioRedis) -> bool:
    """Accept the socket, then authenticate; close with 4001 on failure.

    Accept MUST happen before authenticating: a close-before-accept surfaces as an
    HTTP 403 upgrade rejection with no close code, so the browser never sees 4001 and
    reconnects forever. Owning that ordering here keeps every WS endpoint consistent.
    Returns True iff the connection is authenticated (and left open).
    """
    await websocket.accept()
    if await authenticate_ws_cookie(websocket, redis):
        return True
    await websocket.close(code=4001, reason="Authentication required")
    return False
