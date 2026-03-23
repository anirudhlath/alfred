"""Web channel server — FastAPI with WebSocket for voice + chat."""

from __future__ import annotations

import asyncio
import base64
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from bus.schemas.events import AlfredResponse, UserRequest
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM, decode_stream_value

# Optional: WhisperSTT for voice transcription, PiperTTS for speech output
_lazy_cache: dict[str, Any] = {}
_FAILED: object = object()  # sentinel for imports that already failed


def _lazy_load(key: str, module: str, cls_name: str, missing_msg: str) -> Any:
    """Lazy-load a class from an optional module. Returns instance or None on failure."""
    cached = _lazy_cache.get(key)
    if cached is _FAILED:
        return None
    if cached is not None:
        return cached
    try:
        import importlib

        mod = importlib.import_module(module)
        instance = getattr(mod, cls_name)()
        _lazy_cache[key] = instance
        return instance
    except ImportError:
        logger.warning("{} — {} disabled", missing_msg, key)
        _lazy_cache[key] = _FAILED
    except Exception as exc:
        logger.error("Failed to initialise {}: {}", cls_name, exc)
        _lazy_cache[key] = _FAILED
    return None


def _get_stt() -> Any:
    """Lazy-load WhisperSTT (requires voice extra)."""
    return _lazy_load("stt", "core.voice.stt", "WhisperSTT", "faster-whisper not installed")


def _get_tts() -> Any:
    """Lazy-load PiperTTS (requires voice extra)."""
    return _lazy_load("tts", "core.voice.tts", "PiperTTS", "piper-tts not installed")


# Active WebSocket connections — used by notification channel adapters
_active_websockets: list[WebSocket] = []


def get_active_websockets() -> list[WebSocket]:
    """Return list of currently connected WebSocket sessions."""
    return list(_active_websockets)


def _decode_audio(data_url: str) -> bytes:
    """Decode a base64 data URL (data:audio/webm;base64,...) to raw bytes."""
    if "," in data_url:
        data_url = data_url.split(",", 1)[1]
    return base64.b64decode(data_url)


class OnboardingPayload(BaseModel):
    """Onboarding wizard submission from the PWA."""

    wake_time: str | None = None
    work_address: str | None = None
    dietary_restrictions: str | None = None
    proactivity_level: str | None = None  # opinionated | moderate | conservative
    guest_controls: list[str] | None = None


def _preference_file(
    domain: str, updated: str, confidence: str, title: str, lines: list[str]
) -> str:
    """Format a preference Markdown file with YAML frontmatter."""
    body = "\n".join(lines)
    front = f"---\ndomain: {domain}\nupdated: {updated}\nconfidence: {confidence}\n---"
    return f"{front}\n\n# {title}\n\n{body}\n"


def _atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically (tmp + rename)."""
    from shared.fs import atomic_write

    atomic_write(path, content)


def _get_prefs_dirs() -> tuple[Path, Path]:
    """Return (preferences_dir, profile_dir) for semantic memory."""
    base = Path(__file__).resolve().parent.parent / "memory"
    return base / "preferences", base / "profile"


async def require_localhost(request: Request) -> None:
    """FastAPI dependency — restrict endpoint to localhost only."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(status_code=403, detail="Localhost access only")


@asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """Manage shared Redis connection pool lifecycle + notification delivery."""
    import asyncio

    from core.notifications.delivery import notification_delivery_worker

    pool: aioredis.Redis[Any] = aioredis.from_url(app.state.redis_url, decode_responses=False)  # type: ignore[type-arg]
    app.state.redis = pool

    shutdown = asyncio.Event()
    delivery_task = asyncio.create_task(
        notification_delivery_worker(pool, group="channels-delivery", shutdown=shutdown)
    )

    yield

    shutdown.set()
    delivery_task.cancel()
    await pool.close()


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
        _active_websockets.append(websocket)
        r: aioredis.Redis[Any] = app.state.redis  # type: ignore[type-arg]

        # Accept optional session_id from client for reconnect persistence
        session_id: str | None = None

        try:
            while True:
                data = await websocket.receive_json()

                # Allow client to restore session across reconnects
                if session_id is None:
                    session_id = data.get("session_id") or str(uuid4())
                    # Send assigned session_id so client can persist it
                    await websocket.send_json({"type": "session", "session_id": session_id})

                raw_type = data.get("type", "text")
                content = data.get("content", "")

                # C5: Validate content_type before constructing UserRequest
                if raw_type not in ("text", "audio"):
                    await websocket.send_json(
                        {
                            "type": "error",
                            "text": f"Unsupported content type: {raw_type}",
                            "session_id": session_id,
                        }
                    )
                    continue
                content_type: str = raw_type

                # Transcribe audio to text before sending to Conscious Engine
                if content_type == "audio" and content:
                    stt = _get_stt()
                    if stt is not None:
                        try:
                            audio_bytes = _decode_audio(content)
                            content = stt.transcribe(audio_bytes)
                            content_type = "text"
                            logger.info("Transcribed voice → '{}' chars", len(content))
                            await websocket.send_json(
                                {
                                    "type": "transcription",
                                    "text": content,
                                    "session_id": session_id,
                                }
                            )
                        except Exception as e:
                            logger.error("Voice transcription failed: {}", e)
                            await websocket.send_json(
                                {
                                    "type": "response",
                                    "text": "I'm afraid I couldn't make out what was said.",
                                    "session_id": session_id,
                                }
                            )
                            continue
                    else:
                        await websocket.send_json(
                            {
                                "type": "response",
                                "text": (
                                    "Voice processing is not available at the moment, "
                                    "I'm afraid. Please type your message instead."
                                ),
                                "session_id": session_id,
                            }
                        )
                        continue

                request = UserRequest(
                    source="web-pwa",
                    channel="web_pwa",
                    session_id=session_id,
                    identity_claim=data.get("identity", "guest"),
                    content_type=content_type,
                    content=content,
                )

                response_text = await _publish_and_wait(r, request, session_id, timeout=30.0)

                response_payload: dict[str, Any] = {
                    "type": "response",
                    "text": response_text,
                    "session_id": session_id,
                }

                # Synthesise audio for the response
                tts = _get_tts()
                if tts is not None:
                    try:
                        wav_bytes = tts.synthesize(response_text)
                        response_payload["audio"] = base64.b64encode(wav_bytes).decode()
                    except Exception as exc:
                        logger.error("TTS synthesis failed: {}", exc)

                await websocket.send_json(response_payload)

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected (session={})", session_id)
        finally:
            _active_websockets.remove(websocket)

    @app.get("/api/integrations")
    async def list_integrations() -> list[dict[str, Any]]:
        """List all integrations with schema and configured status."""
        from core.integrations.registry import IntegrationRegistry
        from shared.secrets import aget_all_secrets

        async def _build_info(name: str) -> dict[str, Any]:
            integration_cls = IntegrationRegistry.get_class(name)
            schema = integration_cls.credentials_schema
            stored = await aget_all_secrets(name, list(schema.fields))
            configured = {f: f in stored for f in schema.fields}
            return {
                "name": name,
                "category": integration_cls.category,
                "schema": schema.model_dump(),
                "configured": configured,
            }

        return list(
            await asyncio.gather(*[_build_info(n) for n in IntegrationRegistry.available()])
        )

    @app.put(
        "/api/integrations/{name}/credentials",
        dependencies=[Depends(require_localhost)],
    )
    async def save_credentials(name: str, request: Request) -> dict[str, str]:
        """Save integration credentials to OS keyring."""
        from core.integrations.registry import IntegrationRegistry
        from shared.secrets import aset_secret

        try:
            integration_cls = IntegrationRegistry.get_class(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown integration: {name}") from None

        schema = integration_cls.credentials_schema
        body: dict[str, str] = await request.json()

        unknown = set(body.keys()) - set(schema.fields.keys())
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown fields: {unknown}")

        missing = [
            f
            for f, field in schema.fields.items()
            if field.required and f not in body and not field.transient
        ]
        if missing:
            raise HTTPException(status_code=422, detail=f"Missing required fields: {missing}")

        await asyncio.gather(
            *[aset_secret(name, f, v) for f, v in body.items() if not schema.fields[f].transient]
        )

        await asyncio.to_thread(IntegrationRegistry.reconfigure, name)
        return {"status": "ok"}

    @app.delete(
        "/api/integrations/{name}/credentials",
        dependencies=[Depends(require_localhost)],
    )
    async def delete_credentials(name: str) -> dict[str, str]:
        """Clear all credentials for an integration from OS keyring."""
        from core.integrations.registry import IntegrationRegistry
        from shared.secrets import adelete_secret

        try:
            integration_cls = IntegrationRegistry.get_class(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown integration: {name}") from None

        await asyncio.gather(
            *[adelete_secret(name, f) for f in integration_cls.credentials_schema.fields]
        )

        await asyncio.to_thread(IntegrationRegistry.reconfigure, name)
        return {"status": "ok"}

    @app.get("/api/integrations/{name}/status")
    async def integration_status(name: str) -> dict[str, Any]:
        """Run health check on an integration adapter."""
        from core.integrations.registry import IntegrationRegistry

        try:
            IntegrationRegistry.get_class(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Unknown integration: {name}") from None

        try:
            instance = IntegrationRegistry.get(name)
            healthy = await instance.health_check()
        except Exception:
            healthy = False
        return {"name": name, "healthy": healthy}

    @app.post("/api/onboarding")
    async def save_onboarding(payload: OnboardingPayload) -> dict[str, str]:
        """Save onboarding preferences to semantic memory files.

        Writes default values for any null fields. Skips writing if the
        preference file already exists (prevents clobbering Librarian data).
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        prefs_dir, profile_dir = _get_prefs_dirs()
        prefs_dir.mkdir(parents=True, exist_ok=True)
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Personal preferences (with defaults)
        personal_path = prefs_dir / "personal.md"
        if not personal_path.exists():
            wake = payload.wake_time or "07:00"
            lines: list[str] = [f"- Usual wake time: {wake}"]
            if payload.work_address:
                lines.append(f"- Work address: {payload.work_address}")
            if payload.dietary_restrictions:
                lines.append(f"- Dietary restrictions: {payload.dietary_restrictions}")
            _atomic_write(
                personal_path,
                _preference_file("general", today, "manual", "Personal", lines),
            )

        # Proactivity level (with default)
        proactivity_path = profile_dir / "proactivity.md"
        if not proactivity_path.exists():
            level = payload.proactivity_level or "moderate"
            _atomic_write(
                proactivity_path,
                _preference_file(
                    "general",
                    today,
                    "manual",
                    "Proactivity Level",
                    [f"- Level: {level}"],
                ),
            )

        # Guest mode config (with defaults)
        guest_path = prefs_dir / "guest_mode.md"
        if not guest_path.exists():
            controls = payload.guest_controls or ["Lighting control", "Media playback"]
            guest_lines = [f"- {ctrl}" for ctrl in controls]
            _atomic_write(
                guest_path,
                _preference_file("general", today, "manual", "Guest Mode", guest_lines),
            )

        n_fields = len(payload.model_dump(exclude_none=True))
        logger.info("Onboarding preferences saved ({} fields)", n_fields)
        return {"status": "ok"}

    # Mount static files for PWA (if directory exists)
    web_dir = Path(__file__).resolve().parent.parent.parent / "web"
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="static")

    return app


async def _publish_and_wait(
    redis: aioredis.Redis[Any],  # type: ignore[type-arg]
    request: UserRequest,
    session_id: str,
    timeout: float = 30.0,
) -> str:
    """Publish request and poll the responses stream for a matching response.

    Captures the latest stream ID before publishing to avoid scanning history.
    """
    # Use a time-based ID so we only read responses after this point.
    # This avoids xinfo_stream which fails on non-existent streams and
    # "0-0" which would scan all historical entries on a non-fresh Redis.
    last_id = f"{int(time.time() * 1000)}-0"

    # Publish the request
    await redis.xadd(
        USER_REQUESTS_STREAM,
        {"event": request.model_dump_json()},
    )

    start = time.monotonic()

    while (time.monotonic() - start) < timeout:
        entries = await redis.xread({USER_RESPONSES_STREAM: last_id}, count=10, block=1000)
        for _stream, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                last_id = entry_id
                raw = entry_data.get(b"event") or entry_data.get("event")
                if raw:
                    event_str = decode_stream_value(raw)
                    resp = AlfredResponse.model_validate_json(event_str)
                    if resp.session_id == session_id:
                        return resp.text

    return "I apologize, sir — I seem to be taking longer than expected."
