"""Telemetry WebSocket — live fan-out of Redis streams to the web app.

Per-connection model: a receive loop handles subscribe/unsubscribe messages
while a pump task runs blocking XREADs over the subscribed streams and pushes
each new entry as it lands. No polling: XREAD blocks server-side; an empty
subscription set parks the pump on an asyncio.Event.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from loguru import logger

from core.channels.stream_catalog import KEY_TO_NAME, STREAM_CATALOG, decode_entry
from core.identity.ws_auth import authenticate_ws_cookie

if TYPE_CHECKING:
    from shared.types import AioRedis

_XREAD_BLOCK_MS = 2000


async def _last_id(r: AioRedis, key: str) -> str:
    """Resolve a stream's current last-generated id so the pump starts strictly after it.

    Subscribing at the literal "$" would re-evaluate on every XREAD call, so entries
    landing between two blocking reads on a stream that has not yet delivered on this
    connection would be silently skipped. Pinning a concrete id closes that window
    without replaying history (XREAD returns only entries after the given id).
    """
    entries: list[tuple[bytes | str, dict[Any, Any]]] = await r.xrevrange(key, count=1)
    if entries:
        eid = entries[0][0]
        return eid.decode() if isinstance(eid, bytes) else eid
    return "0-0"


def register_telemetry_ws(app: FastAPI) -> None:
    @app.websocket("/ws/telemetry")
    async def telemetry_ws(websocket: WebSocket) -> None:
        r: AioRedis = websocket.app.state.redis

        # Accept before authenticating so a rejection sends a real close frame
        # carrying code 4001 (close-before-accept surfaces as an HTTP 403 with no
        # code, and the browser then reconnects forever). Matches the /ws gate.
        await websocket.accept()

        if not await authenticate_ws_cookie(websocket, r):
            await websocket.close(code=4001, reason="Authentication required")
            return

        subs: dict[str, str] = {}  # redis key -> last seen entry id
        has_subs = asyncio.Event()

        async def _pump_loop() -> None:
            while True:
                if not subs:
                    has_subs.clear()
                    await has_subs.wait()
                    continue  # re-check subs after waking (may still be empty)
                try:
                    entries: list[
                        tuple[bytes | str, list[tuple[bytes | str, dict[Any, Any]]]]
                    ] = await r.xread(
                        dict(subs),  # type: ignore[arg-type]
                        count=100,  # backpressure unbounded into transport buffer — acceptable
                        # for the single-user admin surface; revisit if telemetry fans out
                        block=_XREAD_BLOCK_MS,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("telemetry xread failed: {}", exc)
                    # redis_error frame + 1s backoff — xread failures stay here, never exit pump
                    await websocket.send_json({"type": "status", "detail": "redis_error"})
                    await asyncio.sleep(1)
                    continue
                for stream_key, items in entries or []:
                    key = stream_key.decode() if isinstance(stream_key, bytes) else stream_key
                    if key not in subs:
                        continue  # unsubscribed mid-read
                    for entry_id, data in items:
                        if key not in subs:
                            break  # client unsubscribed mid-delivery (send_json yields)
                        eid = entry_id.decode() if isinstance(entry_id, bytes) else entry_id
                        subs[key] = eid
                        # Concurrent send_json from pump + receive loop is deliberate and
                        # lock-free: each send_json call is one atomic WS frame, matching
                        # the /ws + notification delivery worker precedent (_active_websockets).
                        await websocket.send_json(
                            {
                                "type": "entry",
                                "stream": KEY_TO_NAME.get(key, key),
                                "id": eid,
                                "event": decode_entry(data),
                            }
                        )

        async def pump() -> None:
            try:
                await _pump_loop()
            except (WebSocketDisconnect, RuntimeError, ConnectionError):
                return  # client gone — receive loop handles teardown

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "invalid JSON"})
                    continue
                names = [n for n in msg.get("streams", []) if n in STREAM_CATALOG]
                if msg.get("type") == "subscribe":
                    for name in names:
                        key = STREAM_CATALOG[name]
                        if key not in subs:
                            subs[key] = await _last_id(r, key)
                    has_subs.set()
                elif msg.get("type") == "unsubscribe":
                    for name in names:
                        subs.pop(STREAM_CATALOG[name], None)
                await websocket.send_json(
                    {
                        "type": "subscribed",
                        "streams": sorted(KEY_TO_NAME[k] for k in subs),
                    }
                )
        except WebSocketDisconnect:
            pass
        finally:
            pump_task.cancel()
            # Await the task so the in-flight xread unwinds before the handler returns.
            with contextlib.suppress(asyncio.CancelledError):
                await pump_task
