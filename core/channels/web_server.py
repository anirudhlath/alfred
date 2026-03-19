"""Web channel server — FastAPI with WebSocket for voice + chat."""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from loguru import logger

from bus.schemas.events import AlfredResponse, UserRequest
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM


@asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """Manage shared Redis connection pool lifecycle."""
    pool: aioredis.Redis[Any] = aioredis.from_url(app.state.redis_url, decode_responses=False)
    app.state.redis = pool
    yield
    await pool.aclose()  # type: ignore[misc]


def create_app(redis_url: str = "redis://localhost:6379") -> FastAPI:
    """Create the FastAPI application for the web channel."""
    app = FastAPI(title="Alfred Web Channel", lifespan=_lifespan)
    app.state.redis_url = redis_url

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "web-channel"}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        session_id = str(uuid4())
        r: aioredis.Redis[Any] = app.state.redis

        try:
            while True:
                data = await websocket.receive_json()
                content_type = data.get("type", "text")
                content = data.get("content", "")

                request = UserRequest(
                    source="web-pwa",
                    channel="web_pwa",
                    session_id=session_id,
                    identity_claim=data.get("identity", "guest"),
                    content_type=content_type,
                    content=content,
                )

                response_text = await _publish_and_wait(r, request, session_id, timeout=30.0)

                await websocket.send_json(
                    {
                        "type": "response",
                        "text": response_text,
                        "session_id": session_id,
                    }
                )

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected (session={})", session_id)

    # Mount static files for PWA (if directory exists)
    web_dir = os.path.join(os.path.dirname(__file__), "..", "..", "web")
    if os.path.isdir(web_dir):
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="static")

    return app


async def _publish_and_wait(
    redis: aioredis.Redis[Any],
    request: UserRequest,
    session_id: str,
    timeout: float = 30.0,
) -> str:
    """Publish request and poll the responses stream for a matching response.

    Captures the latest stream ID before publishing to avoid scanning history.
    """
    # Get current stream tail so we only read new entries
    try:
        info: dict[str, Any] = await redis.xinfo_stream(  # type: ignore[misc]
            USER_RESPONSES_STREAM
        )
        last_id: str | bytes = info.get("last-generated-id", "0-0")
        if isinstance(last_id, bytes):
            last_id = last_id.decode()
    except Exception:
        last_id = "0-0"

    # Publish the request
    await redis.xadd(  # type: ignore[misc]
        USER_REQUESTS_STREAM,
        {"event": request.model_dump_json()},
    )

    start = time.monotonic()

    while (time.monotonic() - start) < timeout:
        entries = await redis.xread(  # type: ignore[misc]
            {USER_RESPONSES_STREAM: last_id}, count=10, block=1000
        )
        for _stream, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                last_id = entry_id
                raw = entry_data.get(b"event") or entry_data.get("event")
                if raw:
                    event_str = raw.decode() if isinstance(raw, bytes) else raw
                    resp = AlfredResponse.model_validate_json(event_str)
                    if resp.session_id == session_id:
                        return resp.text

    return "I apologize, sir — I seem to be taking longer than expected."
