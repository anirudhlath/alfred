"""Admin API — read-only observability + curated controls for the web app.

Every endpoint requires an authenticated passkey session (``require_authenticated``,
HTTP 401 otherwise) and nothing else: admin reads and controls are usable from the
public hostname once signed in. The trusted-network gate is reserved for endpoints
that can mint or widen credentials — passkey registration, credential writes, device
tokens, voice enrolment — which live in ``web_server.py`` / ``auth_routes.py``.

Reads are defensive: missing keys/streams/files yield empty results, never 500s.
Controls map to operations the system already performs — direct Redis writes
for shared state, ACTIONS_STREAM publishes for process-owned behavior.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import statistics
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from bus.schemas.events import ActionRequest
from core.channels.stream_catalog import STREAM_CATALOG, decode_entry, stream_summaries
from core.memory.paths import episodic_cold_path, preferences_dir, profile_dir, scratchpad_path
from core.reflex.attention import (
    attention_add,
    attention_domains,
    attention_list,
    attention_remove,
    attention_seen_list,
)
from core.reflex.inference import REFLEX_BACKENDS
from shared.config import AlfredConfig
from shared.redis_streams import revrange
from shared.streams import (
    ACTIONS_STREAM,
    CONTEXT_PREFIX,
    COST_DAILY_KEY,
    DEFERRED_NOTIFICATIONS_KEY,
    DEVICE_TOKENS_KEY,
    DND_STATE_KEY,
    LIBRARIAN_STATUS_KEY,
    REFLEX_OBSERVATIONS_STREAM,
    SCRATCHPAD_QUEUE,
    SESSIONS_KEY_PREFIX,
    TRIGGERS_KEY,
    decode_stream_value,
)

if TYPE_CHECKING:
    import httpx

    from shared.types import AioRedis


_FAILED = object()
_episodic_memory: Any = None
# Held separately from _episodic_memory so shutdown can close it without reaching
# into EpisodicMemory's private attributes.
_episodic_embedder: Any = None


class DndRequest(BaseModel):
    active: bool
    until: datetime | None = None
    reason: str | None = None


class TriggerEnabledRequest(BaseModel):
    enabled: bool


# Matches the domains the bus emits (``home``, ``media``); rejects anything that
# could address another domain's keyspace — notably a ``:seen`` suffix, which
# would write straight into the sticky set. Applied with `fullmatch`: `$` would
# also accept a trailing newline.
_DOMAIN_RE = re.compile(r"[a-z0-9_]{1,64}")
_MAX_ENTITY_ID_LEN = 256
_MAX_ENTITIES_PER_LIST = 200
# One vocabulary for an unreachable attention store, shared by the GET and the PUT.
_ATTENTION_STORE_DOWN = "Attention store unavailable"


class AttentionUpdate(BaseModel):
    """Entities to add to (`allow`) or remove from (`ask`) a domain's attention set."""

    allow: list[str] = Field(default_factory=list, max_length=_MAX_ENTITIES_PER_LIST)
    ask: list[str] = Field(default_factory=list, max_length=_MAX_ENTITIES_PER_LIST)

    @field_validator("allow", "ask")
    @classmethod
    def _clean_entities(cls, entities: list[str]) -> list[str]:
        """Strip, reject blanks, and cap length — 422 rather than a junk set member.

        Colons are deliberately allowed: these are set *members*, not key names,
        so an entity id has no way to escape the domain it is written under.
        """
        cleaned: list[str] = []
        for raw in entities:
            entity_id = raw.strip()
            if not entity_id:
                raise ValueError("entity id must not be blank")
            if len(entity_id) > _MAX_ENTITY_ID_LEN:
                raise ValueError(f"entity id exceeds {_MAX_ENTITY_ID_LEN} characters")
            cleaned.append(entity_id)
        return cleaned


async def _publish_internal_action(redis: AioRedis, tool_name: str) -> None:
    action = ActionRequest(
        source="admin-api", target_service="conscious-engine", tool_name=tool_name
    )
    await redis.xadd(ACTIONS_STREAM, {"event": action.model_dump_json()})


async def _publish_trigger_action(
    redis: AioRedis, tool_name: str, parameters: dict[str, Any]
) -> None:
    """Queue a trigger mutation for the triggers process (owns TriggerStore).

    The triggers process consumes ACTIONS_STREAM (group 'triggers-internal'),
    filters target_service='trigger-engine', and applies the change via the
    real TriggerEngine / TriggerStore so Redis AND the YAML snapshot stay
    consistent — no direct hash writes from the channels process.
    """
    action = ActionRequest(
        source="admin-api",
        target_service="trigger-engine",
        tool_name=tool_name,
        parameters=parameters,
    )
    await redis.xadd(ACTIONS_STREAM, {"event": action.model_dump_json()})


def _get_episodic_lazy(redis: AioRedis) -> Any | None:
    """Build EpisodicMemory once; heavy embedder loads on first vector search.

    The provider is cached for the process lifetime, so it must not be closed per
    request — a later search would find a closed httpx client. It is closed instead by
    :func:`aclose_episodic` from the web server's shutdown hook, alongside the other
    client-like resources torn down there.
    """
    global _episodic_memory, _episodic_embedder
    if _episodic_memory is _FAILED:
        return None
    if _episodic_memory is None:
        try:
            from core.memory.embedding_backend import build_embedding_provider
            from core.memory.episodic.memory import EpisodicMemory
            from core.memory.redis_vector_store import RedisVectorStore
            from core.memory.sqlite_vec_store import SqliteVecStore

            config = AlfredConfig.from_env()
            _episodic_embedder = build_embedding_provider(config)
            _episodic_memory = EpisodicMemory(
                hot=RedisVectorStore(redis=redis, dim=config.embedding_dim),
                cold=SqliteVecStore(db_path=str(episodic_cold_path()), dim=config.embedding_dim),
                embedder=_episodic_embedder,
            )
        except Exception as exc:
            logger.error("EpisodicMemory unavailable for admin search: {}", exc)
            _episodic_memory = _FAILED
            return None
    return _episodic_memory


async def aclose_episodic() -> None:
    """Release the cached embedding provider. Called from the web server's shutdown.

    Idempotent, and never raises: shutdown runs after ``yield`` in the lifespan, where
    an exception would skip the teardown that follows it.
    """
    global _episodic_memory, _episodic_embedder
    embedder, _episodic_embedder, _episodic_memory = _episodic_embedder, None, None
    if embedder is None:
        return
    with contextlib.suppress(Exception):
        await embedder.aclose()


def _base_overview(*, idle_minutes: int) -> dict[str, Any]:
    """Full Overview shape with placeholders — the single source of truth for the
    field set. The frontend `Overview` type requires every key, so both the degraded
    path (returned as-is) and the happy path (which overwrites what it can compute)
    build from this, and neither can drift into a partial payload.

    ``session`` comes from config, not Redis, so it is real on both paths — the client
    needs the idle timeout most when the house is degraded, and must not guess it."""
    return {
        "redis": {"connected": False},
        "cost": None,
        "dnd": {"active": False},
        "counts": {"sessions": 0, "devices": 0, "deferred": 0, "triggers": 0},
        "streams": {},
        "inference": {"ollama": False, "lmstudio": False},
        "reflex": {"model": None, "last_ms": None, "p50_ms": None},
        "librarian": {"last_run_at": None, "reviewed": None, "next_run_at": None},
        "session": {"idle_minutes": idle_minutes},
    }


def _safe_json(raw: Any, *, default: Any) -> Any:
    """Parse a stored JSON value, returning ``default`` on missing/corrupt data.

    Keeps the 'admin reads never 500' contract: one corrupt Redis value must not
    take down the whole overview dashboard.
    """
    if not raw:
        return default
    try:
        return json.loads(decode_stream_value(raw))
    except (json.JSONDecodeError, ValueError, TypeError):
        return default


def _decode_hash(fields: dict[bytes | str, Any]) -> dict[str, Any]:
    """Decode a Redis hash, dropping binary embedding fields."""
    out: dict[str, Any] = {}
    for k, v in fields.items():
        key = decode_stream_value(k)
        if key.startswith("embedding"):
            continue
        out[key] = v.decode(errors="replace") if isinstance(v, bytes) else v
    return out


_REFLEX_LATENCY_SAMPLES = 20


async def _reflex_latencies(r: AioRedis, *, count: int = _REFLEX_LATENCY_SAMPLES) -> list[float]:
    """Decision latency (ms) of the newest Reflex observations, newest first.

    Each observation stamps its own ``timestamp`` and carries the originating
    event under ``trigger_event.timestamp``; the difference is how long the
    Reflex Engine took. Entries that don't parse are skipped; any Redis error
    yields an empty list so the overview never 500s.
    """
    try:
        entries = await revrange(r, REFLEX_OBSERVATIONS_STREAM, count=count)
    except Exception as exc:
        logger.warning("Reflex observation read failed: {}", exc)
        return []
    out: list[float] = []
    for _entry_id, fields in entries:
        try:
            event = decode_entry(fields)
            observed = datetime.fromisoformat(event["timestamp"])
            triggered = datetime.fromisoformat(event["trigger_event"]["timestamp"])
            # Inside the try: subtracting a naive from an aware datetime is a
            # TypeError, and one mixed-tz entry must not take down the overview.
            latency_ms = (observed - triggered).total_seconds() * 1000
        except (KeyError, TypeError, ValueError):
            continue
        out.append(round(latency_ms, 1))
    return out


def _int_or_none(raw: Any) -> int | None:
    """Parse an int, or None when the value is missing or not one.

    ``str.isdigit()`` is not a safe pre-test: it accepts non-decimal Unicode
    digits such as U+00B2 that ``int()`` then rejects. Ask ``int()`` directly.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _librarian_status(r: AioRedis) -> dict[str, Any]:
    """The ``alfred:librarian:status`` hash, shaped for the overview.

    Every field is optional: the hash doesn't exist until the Librarian's first
    run, and any Redis error is swallowed the same way the reflex read swallows
    its own, so a nulled-out block is the worst case rather than a 500.
    """
    try:
        fields = _decode_hash(await r.hgetall(LIBRARIAN_STATUS_KEY))
    except Exception as exc:
        logger.warning("Librarian status read failed: {}", exc)
        fields = {}
    return {
        "last_run_at": fields.get("last_run_at"),
        "reviewed": _int_or_none(fields.get("reviewed")),
        "next_run_at": fields.get("next_run_at"),
    }


async def _attention_domain(r: AioRedis, domain: str) -> dict[str, Any]:
    return {
        "domain": domain,
        "members": await attention_list(r, domain),
        "seen": await attention_seen_list(r, domain),
    }


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


def create_admin_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/admin",
        dependencies=[Depends(require_authenticated)],
    )

    @router.get("/overview")
    async def overview(request: Request) -> dict[str, Any]:
        r = _redis(request)
        # Read up front so the degraded path carries it too. A malformed value can't
        # 500 us here: core/channels/__main__.py loads the same config before
        # create_app, so the process would never have started.
        cfg = AlfredConfig.from_env()
        out = _base_overview(idle_minutes=cfg.session_timeout_minutes)
        try:
            await r.ping()
        except Exception:
            return out  # degraded: full shape, all placeholders

        out["redis"]["connected"] = True
        raw_cost = await r.get(COST_DAILY_KEY)
        out["cost"] = _safe_json(raw_cost, default=None)
        raw_dnd = await r.get(DND_STATE_KEY)
        out["dnd"] = _safe_json(raw_dnd, default={"active": False})

        session_count = 0
        async for _ in r.scan_iter(match=f"{SESSIONS_KEY_PREFIX}*"):
            session_count += 1
        out["counts"] = {
            "sessions": session_count,
            "devices": int(await r.hlen(DEVICE_TOKENS_KEY)),
            "deferred": int(await r.llen(DEFERRED_NOTIFICATIONS_KEY)),
            "triggers": int(await r.hlen(TRIGGERS_KEY)),
        }
        out["streams"] = await stream_summaries(r)
        out["inference"] = {
            "ollama": await _check_http(request, cfg.ollama_host.rstrip("/") + "/api/tags"),
            "lmstudio": await _check_http(request, cfg.lmstudio_host.rstrip("/") + "/v1/models"),
        }
        latencies = await _reflex_latencies(r)
        # Normalised like the dispatcher does (core/reflex/inference.py), so
        # REFLEX_BACKEND=OpenAI names the model the engine will actually use — and
        # judged against the dispatcher's own accepted set, imported rather than
        # retyped: a backend it refuses runs no model, so this reports null instead
        # of quietly naming OLLAMA_MODEL for, say, REFLEX_BACKEND=vllm.
        backend = cfg.reflex_backend.strip().lower()
        if backend not in REFLEX_BACKENDS:
            reflex_model = None
        else:
            reflex_model = (
                cfg.openai_compat_model if backend == "openai" else cfg.ollama_model
            ) or None
        out["reflex"] = {
            "model": reflex_model,
            "last_ms": latencies[0] if latencies else None,
            "p50_ms": round(statistics.median(latencies), 1) if latencies else None,
        }
        out["librarian"] = await _librarian_status(r)
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
        raw = await revrange(_redis(request), key, count=count, max_id=max_id)
        entries = [
            {"id": decode_stream_value(eid), "event": decode_entry(data)} for eid, data in raw
        ]
        # When stream length is an exact multiple of count, the client gets one final
        # empty page (entries: [], next_before: null) — intentional standard cursor behavior.
        next_before = entries[-1]["id"] if len(entries) == count else None
        return {"entries": entries, "next_before": next_before}

    @router.get("/triggers")
    async def triggers(request: Request) -> dict[str, Any]:
        raw: dict[bytes | str, bytes | str] = await _redis(request).hgetall(TRIGGERS_KEY)
        items: list[dict[str, Any]] = []
        for _tid, value in raw.items():
            val = decode_stream_value(value)
            try:
                items.append(dict(json.loads(val)))
            except (json.JSONDecodeError, ValueError):
                continue
        items.sort(key=lambda t: str(t.get("created_at", "")), reverse=True)
        return {"triggers": items}

    @router.get("/notifications/deferred")
    async def deferred_notifications(request: Request) -> dict[str, Any]:
        raw_list: list[bytes | str] = await _redis(request).lrange(
            DEFERRED_NOTIFICATIONS_KEY, 0, -1
        )
        out: list[dict[str, Any]] = []
        for item in raw_list:
            val = decode_stream_value(item)
            try:
                out.append(dict(json.loads(val)))
            except (json.JSONDecodeError, ValueError):
                continue
        return {"notifications": out}

    @router.get("/sessions")
    async def sessions(request: Request) -> dict[str, Any]:
        r = _redis(request)
        out: list[dict[str, Any]] = []
        # N+1 (hgetall+ttl per session) is fine — session count is small and
        # this is admin-triggered.
        async for key in r.scan_iter(match=f"{SESSIONS_KEY_PREFIX}*"):
            key_str = decode_stream_value(key)
            data = _decode_hash(await r.hgetall(key_str))
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
        raw_devices: dict[bytes | str, bytes | str] = await _redis(request).hgetall(
            DEVICE_TOKENS_KEY
        )
        out: list[dict[str, Any]] = []
        for token, value in raw_devices.items():
            # Truncated: a full APNs token is credential-equivalent and this route is
            # reachable from the public hostname with only a session. 12 chars is what
            # the UI renders and is enough to tell devices apart.
            tok = decode_stream_value(token)[:12]
            val = decode_stream_value(value)
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
            try:
                results = await memory.recall(query=q, limit=limit, update_stats=False)
            except Exception as exc:
                # Construction no longer proves usability. SentenceTransformerProvider
                # loaded its model in __init__, so a broken embedder failed above, in
                # _get_episodic_lazy. The HTTP backend's __init__ only builds an httpx
                # client, so a down or restarting embedding server first surfaces here —
                # recall() embeds the query before it touches either store. Uncaught,
                # that is a 500 from a module that promises reads never 500.
                logger.error("Episodic vector search failed: {}", exc)
                raise HTTPException(status_code=503, detail="Vector search unavailable") from exc
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
            fields = await r.hgetall(key)
            entry = _decode_hash(fields)
            # CONTEXT_PREFIX keyspace is shared: ContextIndexManager writes episodic,
            # semantic, and routine entries.  Skip non-episodic entries here.
            if entry.get("type") != "episodic":
                continue
            entry["store"] = "hot"
            hot.append(entry)
        hot.sort(key=lambda e: float(e.get("timestamp", 0) or 0), reverse=True)

        cold: list[dict[str, Any]] = []
        db_path = episodic_cold_path()
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
        def _read_semantic() -> list[dict[str, Any]]:
            files: list[dict[str, Any]] = []
            for directory in (preferences_dir(), profile_dir()):
                dirname = directory.name
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
            return files

        # Offload sync glob + reads off the event loop (which also serves chat/voice WS).
        return {"files": await asyncio.to_thread(_read_semantic)}

    @router.get("/memory/routines")
    async def memory_routines() -> dict[str, Any]:
        from core.memory.routines.store import RoutineStore

        store = RoutineStore()
        # list_all() does sync glob + YAML reads per file — offload to thread pool
        # so the event loop (which also serves chat/voice WS) is not blocked.
        routines = await asyncio.to_thread(store.list_all)
        return {"routines": [spec.model_dump(mode="json") for spec in routines]}

    @router.get("/memory/scratchpad")
    async def memory_scratchpad(request: Request) -> dict[str, Any]:
        path = scratchpad_path()
        content = await asyncio.to_thread(lambda: path.read_text() if path.exists() else "")
        pending = int(await _redis(request).llen(SCRATCHPAD_QUEUE))
        return {"content": content, "pending_queue": pending}

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------

    @router.post("/dnd")
    async def set_dnd(request: Request, body: DndRequest) -> dict[str, Any]:
        r = _redis(request)
        if not body.active:
            await r.delete(DND_STATE_KEY)
            logger.info("Admin cleared DND")
            return {"active": False}
        state: dict[str, Any] = {
            "active": True,
            "until": body.until.isoformat() if body.until else None,
            "reason": body.reason,
            "source": "manual",
        }
        await r.set(DND_STATE_KEY, json.dumps(state))
        logger.info(
            "Admin set DND until {} (reason: {})",
            state["until"] or "indefinite",
            body.reason or "none",
        )
        return state

    @router.post("/notifications/drain")
    async def drain_notifications(request: Request) -> dict[str, str]:
        await _publish_internal_action(_redis(request), "drain_deferred_notifications")
        logger.info("Admin queued deferred-notification drain")
        return {"status": "queued"}

    @router.post("/librarian/run")
    async def run_librarian(request: Request) -> dict[str, str]:
        await _publish_internal_action(_redis(request), "run_librarian")
        logger.info("Admin queued manual Librarian run")
        return {"status": "queued"}

    async def _validate_trigger_exists(r: AioRedis, trigger_id: str) -> None:
        """Read-only existence + integrity check (keeps 404 + corrupt-500 contract).

        The mutation itself is owned by the triggers process; this only guards the
        synchronous response so the web app sees 404/500 for bad ids immediately.
        """
        raw = await r.hget(TRIGGERS_KEY, trigger_id)
        if raw is None:
            raise HTTPException(status_code=404, detail=f"Unknown trigger '{trigger_id}'")
        try:
            json.loads(decode_stream_value(raw))
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                status_code=500, detail=f"Trigger '{trigger_id}' has corrupt stored data"
            ) from exc

    @router.post("/triggers/{trigger_id}/enabled")
    async def set_trigger_enabled(
        request: Request, trigger_id: str, body: TriggerEnabledRequest
    ) -> dict[str, Any]:
        r = _redis(request)
        await _validate_trigger_exists(r, trigger_id)
        await _publish_trigger_action(
            r, "set_trigger_enabled", {"trigger_id": trigger_id, "enabled": body.enabled}
        )
        logger.info("Admin queued trigger '{}' enabled={}", trigger_id, body.enabled)
        # The triggers process applies the change via TriggerStore (Redis + YAML)
        # within its 60s cache window.
        return {
            "status": "queued",
            "trigger_id": trigger_id,
            "enabled": body.enabled,
            "effective_within_seconds": 60,
        }

    @router.post("/triggers/{trigger_id}/fire")
    async def fire_trigger(request: Request, trigger_id: str) -> dict[str, Any]:
        r = _redis(request)
        await _validate_trigger_exists(r, trigger_id)
        await _publish_trigger_action(r, "fire_trigger", {"trigger_id": trigger_id})
        logger.info("Admin queued manual fire for trigger '{}'", trigger_id)
        return {"status": "queued", "trigger_id": trigger_id}

    @router.delete("/sessions/{session_id}")
    async def delete_session(request: Request, session_id: str) -> dict[str, bool]:
        deleted = await _redis(request).delete(f"{SESSIONS_KEY_PREFIX}{session_id}")
        logger.info("Admin deleted session {}", session_id)
        return {"deleted": bool(deleted)}

    @router.get("/attention")
    async def attention(request: Request) -> dict[str, Any]:
        """Every domain's attention set and its sticky ``:seen`` companion."""
        r = _redis(request)
        try:
            domains = await attention_domains(r)
        except Exception as exc:
            # Not `{"domains": []}`: that is the shape "nothing is configured" has, and
            # the PWA's setup gate branches on exactly that. An outage says so instead,
            # in the same vocabulary as the sibling PUT.
            logger.warning("Attention read failed: {}", exc)
            raise HTTPException(status_code=503, detail=_ATTENTION_STORE_DOWN) from exc
        # Per-domain, so one unreadable set costs its own row rather than the page.
        out: list[dict[str, Any]] = []
        for domain in domains:
            try:
                out.append(await _attention_domain(r, domain))
            except Exception as exc:
                logger.warning("Attention read for {} failed: {}", domain, exc)
        return {"domains": out}

    @router.put("/attention/{domain}")
    async def update_attention(
        request: Request, domain: str, body: AttentionUpdate
    ) -> dict[str, Any]:
        """Add (`allow`) or sticky-remove (`ask`) entities for one domain.

        `ask` is applied after `allow`, so an entity in both lists ends up
        removed and sticky.
        """
        if not _DOMAIN_RE.fullmatch(domain):
            raise HTTPException(status_code=400, detail="Invalid domain")
        r = _redis(request)
        applied = 0  # writes are not transactional — say how far we got
        try:
            for entity_id in body.allow:
                await attention_add(r, domain, entity_id)
                applied += 1
            for entity_id in body.ask:
                await attention_remove(r, domain, entity_id)
                applied += 1
            # Read back inside the guard: a write is not confirmed until it reads.
            updated = await _attention_domain(r, domain)
        except Exception as exc:
            logger.warning(
                "Attention write for {} failed after {} of {} changes: {}",
                domain,
                applied,
                len(body.allow) + len(body.ask),
                exc,
            )
            raise HTTPException(status_code=503, detail=_ATTENTION_STORE_DOWN) from exc
        logger.info(
            "Attention set '{}' updated via admin: +{} -{}",
            domain,
            len(body.allow),
            len(body.ask),
        )
        return updated

    return router


__all__ = ["create_admin_router", "require_authenticated"]
