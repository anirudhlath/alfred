"""Admin API — read-only observability + curated controls for the web app.

All endpoints require BOTH an authenticated session cookie and a trusted
network (localhost/Tailscale), mirroring the credentials endpoints.

Reads are defensive: missing keys/streams/files yield empty results, never 500s.
Controls map to operations the system already performs — direct Redis writes
for shared state, ACTIONS_STREAM publishes for process-owned behavior.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger

from core.channels.stream_catalog import STREAM_CATALOG, decode_entry, stream_summaries
from shared.config import AlfredConfig
from shared.streams import (
    CONTEXT_PREFIX,
    COST_DAILY_KEY,
    DEFERRED_NOTIFICATIONS_KEY,
    DEVICE_TOKENS_KEY,
    DND_STATE_KEY,
    SCRATCHPAD_QUEUE,
    SESSIONS_KEY_PREFIX,
    TRIGGERS_KEY,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from shared.types import AioRedis


_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"
_FAILED = object()
_episodic_memory: Any = None


def _get_episodic_lazy(redis: AioRedis) -> Any | None:
    """Build EpisodicMemory once; heavy embedder loads on first vector search."""
    global _episodic_memory
    if _episodic_memory is _FAILED:
        return None
    if _episodic_memory is None:
        try:
            from core.memory.embedding_provider import SentenceTransformerProvider
            from core.memory.episodic.memory import EpisodicMemory
            from core.memory.redis_vector_store import RedisVectorStore
            from core.memory.sqlite_vec_store import SqliteVecStore

            config = AlfredConfig.from_env()
            _episodic_memory = EpisodicMemory(
                hot=RedisVectorStore(redis=redis, dim=config.embedding_dim),
                cold=SqliteVecStore(
                    db_path=str(_MEMORY_DIR / "episodic_cold.db"), dim=config.embedding_dim
                ),
                embedder=SentenceTransformerProvider(config.embedding_model),
            )
        except Exception as exc:
            logger.error("EpisodicMemory unavailable for admin search: {}", exc)
            _episodic_memory = _FAILED
            return None
    return _episodic_memory


def _decode_hash(fields: dict[bytes | str, Any]) -> dict[str, Any]:
    """Decode a Redis hash, dropping binary embedding fields."""
    out: dict[str, Any] = {}
    for k, v in fields.items():
        key = k.decode() if isinstance(k, bytes) else k
        if key.startswith("embedding"):
            continue
        out[key] = v.decode(errors="replace") if isinstance(v, bytes) else v
    return out


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

    @router.get("/triggers")
    async def triggers(request: Request) -> dict[str, Any]:
        raw: dict[bytes | str, bytes | str] = await _redis(request).hgetall(TRIGGERS_KEY)  # type: ignore[misc]
        items: list[dict[str, Any]] = []
        for _tid, value in raw.items():
            val = value.decode() if isinstance(value, bytes) else value
            try:
                items.append(dict(json.loads(val)))
            except (json.JSONDecodeError, ValueError):
                continue
        items.sort(key=lambda t: str(t.get("created_at", "")), reverse=True)
        return {"triggers": items}

    @router.get("/notifications/deferred")
    async def deferred_notifications(request: Request) -> dict[str, Any]:
        raw_list: list[bytes | str] = await _redis(request).lrange(  # type: ignore[misc]
            DEFERRED_NOTIFICATIONS_KEY, 0, -1
        )
        out: list[dict[str, Any]] = []
        for item in raw_list:
            val = item.decode() if isinstance(item, bytes) else item
            try:
                out.append(dict(json.loads(val)))
            except (json.JSONDecodeError, ValueError):
                continue
        return {"notifications": out}

    @router.get("/sessions")
    async def sessions(request: Request) -> dict[str, Any]:
        r = _redis(request)
        out: list[dict[str, Any]] = []
        async for key in r.scan_iter(match=f"{SESSIONS_KEY_PREFIX}*"):
            key_str = key.decode() if isinstance(key, bytes) else key
            data = _decode_hash(await r.hgetall(key_str))  # type: ignore[misc]
            history = data.get("history") or "[]"
            try:
                turns = len(json.loads(history))
            except (json.JSONDecodeError, ValueError):
                turns = 0
            out.append(
                {
                    "session_id": key_str.removeprefix(SESSIONS_KEY_PREFIX),
                    "channel": data.get("channel", "unknown"),
                    "created_at": data.get("created_at"),
                    "turns": turns,
                    "ttl_seconds": int(await r.ttl(key_str)),
                }
            )
        return {"sessions": out}

    @router.get("/devices")
    async def devices(request: Request) -> dict[str, Any]:
        raw_devices: dict[bytes | str, bytes | str] = await _redis(request).hgetall(  # type: ignore[misc]
            DEVICE_TOKENS_KEY
        )
        out: list[dict[str, Any]] = []
        for token, value in raw_devices.items():
            tok = token.decode() if isinstance(token, bytes) else token
            val = value.decode() if isinstance(value, bytes) else value
            try:
                out.append({"device_token": tok, **json.loads(val)})
            except (json.JSONDecodeError, ValueError):
                out.append({"device_token": tok})
        return {"devices": out}

    @router.get("/memory/episodic")
    async def memory_episodic(
        request: Request, q: str | None = None, limit: int = 30
    ) -> dict[str, Any]:
        """Browse or search episodic memory.

        Without ?q: scans the hot Redis context index (type=episodic only) and
        queries the cold SQLite store, each limited to `limit` entries
        (per-store limit, not merged-sorted).
        With ?q: performs vector search across both stores via EpisodicMemory.recall()
        (also limited per store); recall is non-mutating (update_stats=False) so
        admin browsing does not perturb decay-relevant retrieval stats.
        """
        r = _redis(request)
        limit = max(1, min(limit, 100))

        if q:
            memory = _get_episodic_lazy(r)
            if memory is None:
                raise HTTPException(status_code=503, detail="Vector search unavailable")
            results = await memory.recall(query=q, limit=limit, update_stats=False)
            return {
                "entries": [
                    {
                        "store": res.source_store,
                        "score": res.score,
                        **res.entry.model_dump(mode="json"),
                    }
                    for res in results
                ]
            }

        hot: list[dict[str, Any]] = []
        async for key in r.scan_iter(match=f"{CONTEXT_PREFIX}*", count=500):
            fields = await r.hgetall(key)  # type: ignore[misc]
            entry = _decode_hash(fields)
            # CONTEXT_PREFIX keyspace is shared: ContextIndexManager writes episodic,
            # semantic, and routine entries.  Skip non-episodic entries here.
            if entry.get("type") != "episodic":
                continue
            entry["store"] = "hot"
            hot.append(entry)
        hot.sort(key=lambda e: float(e.get("timestamp", 0) or 0), reverse=True)

        cold: list[dict[str, Any]] = []
        db_path = _MEMORY_DIR / "episodic_cold.db"
        if db_path.exists():
            try:
                async with aiosqlite.connect(db_path) as conn:
                    conn.row_factory = aiosqlite.Row
                    async with conn.execute(
                        "SELECT id, timestamp, source, summary, entities, valence,"
                        " significance, semantic_key FROM episodic_entries"
                        " ORDER BY timestamp DESC LIMIT ?",
                        (limit,),
                    ) as cur:
                        cold = [dict(row) | {"store": "cold"} for row in await cur.fetchall()]
            except Exception as exc:
                logger.warning("Cold store read failed: {}", exc)

        return {"entries": hot[:limit] + cold}

    @router.get("/memory/semantic")
    async def memory_semantic() -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        for dirname in ("preferences", "profile"):
            directory = _MEMORY_DIR / dirname
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.md")):
                if path.name.startswith("."):
                    continue
                files.append(
                    {
                        "name": path.name,
                        "dir": dirname,
                        "content": path.read_text(),
                        "modified": datetime.fromtimestamp(
                            path.stat().st_mtime, tz=UTC
                        ).isoformat(),
                    }
                )
        return {"files": files}

    @router.get("/memory/routines")
    async def memory_routines() -> dict[str, Any]:
        from core.memory.routines.store import RoutineStore

        store = RoutineStore(routines_dir=str(_MEMORY_DIR / "routines"))
        # list_all() does sync glob + YAML reads per file — offload to thread pool
        # so the event loop (which also serves chat/voice WS) is not blocked.
        routines = await asyncio.to_thread(store.list_all)
        return {"routines": [spec.model_dump(mode="json") for spec in routines]}

    @router.get("/memory/scratchpad")
    async def memory_scratchpad(request: Request) -> dict[str, Any]:
        path = _MEMORY_DIR / "scratchpad.md"
        content = path.read_text() if path.exists() else ""
        pending = int(await _redis(request).llen(SCRATCHPAD_QUEUE))  # type: ignore[misc]
        return {"content": content, "pending_queue": pending}

    return router


__all__ = ["create_admin_router", "require_authenticated"]
