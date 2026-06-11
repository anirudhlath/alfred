"""Admin API — read-only observability + curated controls for the web app.

All endpoints require BOTH an authenticated session cookie and a trusted
network (localhost/Tailscale), mirroring the credentials endpoints.

Reads are defensive: missing keys/streams/files yield empty results, never 500s.
Controls map to operations the system already performs — direct Redis writes
for shared state, ACTIONS_STREAM publishes for process-owned behavior.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from core.channels.stream_catalog import STREAM_CATALOG, decode_entry, stream_summaries
from shared.config import AlfredConfig
from shared.streams import (
    COST_DAILY_KEY,
    DEFERRED_NOTIFICATIONS_KEY,
    DEVICE_TOKENS_KEY,
    DND_STATE_KEY,
    SESSIONS_KEY_PREFIX,
    TRIGGERS_KEY,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from shared.types import AioRedis


async def require_authenticated(request: Request) -> None:
    """401 unless AuthCookieMiddleware marked this request authenticated."""
    if not getattr(request.state, "authenticated", False):
        raise HTTPException(status_code=401, detail="Authentication required")


def _redis(request: Request) -> AioRedis:
    r: AioRedis = request.app.state.redis
    return r


async def _check_http(request: Request, url: str) -> bool:
    """Probe a local inference server via the lifespan-owned httpx client.

    No lazy creation: when the client is absent (tests don't run the lifespan),
    report False deterministically — never open real connections from a test.
    """
    client: httpx.AsyncClient | None = getattr(request.app.state, "http", None)
    if client is None:
        return False
    try:
        resp = await client.get(url)
        return bool(resp.status_code < 500)
    except Exception:
        return False


def create_admin_router(trusted_network_dep: Callable[..., Any]) -> APIRouter:
    router = APIRouter(
        prefix="/api/admin",
        dependencies=[Depends(trusted_network_dep), Depends(require_authenticated)],
    )

    @router.get("/overview")
    async def overview(request: Request) -> dict[str, Any]:
        r = _redis(request)
        out: dict[str, Any] = {"redis": {"connected": True}}
        try:
            await r.ping()  # type: ignore[misc]
        except Exception:
            out["redis"]["connected"] = False
            return out

        raw_cost = await r.get(COST_DAILY_KEY)
        out["cost"] = json.loads(raw_cost) if raw_cost else None
        raw_dnd = await r.get(DND_STATE_KEY)
        out["dnd"] = json.loads(raw_dnd) if raw_dnd else {"active": False}

        session_count = 0
        async for _ in r.scan_iter(match=f"{SESSIONS_KEY_PREFIX}*"):
            session_count += 1
        out["counts"] = {
            "sessions": session_count,
            "devices": int(await r.hlen(DEVICE_TOKENS_KEY)),  # type: ignore[misc]
            "deferred": int(await r.llen(DEFERRED_NOTIFICATIONS_KEY)),  # type: ignore[misc]
            "triggers": int(await r.hlen(TRIGGERS_KEY)),  # type: ignore[misc]
        }
        out["streams"] = await stream_summaries(r)
        cfg = AlfredConfig.from_env()
        out["inference"] = {
            "ollama": await _check_http(request, cfg.ollama_host.rstrip("/") + "/api/tags"),
            "lmstudio": await _check_http(request, cfg.lmstudio_host.rstrip("/") + "/v1/models"),
        }
        return out

    @router.get("/streams")
    async def streams(request: Request) -> dict[str, Any]:
        return await stream_summaries(_redis(request))

    @router.get("/streams/{name}")
    async def stream_history(
        request: Request, name: str, count: int = 50, before: str | None = None
    ) -> dict[str, Any]:
        if before is not None and not re.fullmatch(r"\d+-\d+", before):
            raise HTTPException(
                status_code=400, detail="Invalid 'before' cursor; expected '<ms>-<seq>'"
            )
        key = STREAM_CATALOG.get(name)
        if key is None:
            raise HTTPException(status_code=404, detail=f"Unknown stream '{name}'")
        count = max(1, min(count, 200))
        max_id = f"({before}" if before else "+"
        raw: list[tuple[bytes | str, dict[bytes | str, bytes | str]]] = await _redis(
            request
        ).xrevrange(key, max=max_id, min="-", count=count)
        entries = [
            {"id": eid.decode() if isinstance(eid, bytes) else eid, "event": decode_entry(data)}
            for eid, data in raw
        ]
        # When stream length is an exact multiple of count, the client gets one final
        # empty page (entries: [], next_before: null) — intentional standard cursor behavior.
        next_before = entries[-1]["id"] if len(entries) == count else None
        return {"entries": entries, "next_before": next_before}

    return router


__all__ = ["create_admin_router", "require_authenticated"]
