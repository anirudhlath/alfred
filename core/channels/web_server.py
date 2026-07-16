"""Web channel server — FastAPI with WebSocket for voice + chat."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import httpx
import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.responses import Response

from bus.schemas.events import AlfredResponse, UserRequest
from core.channels.admin_api import create_admin_router
from core.channels.telemetry_ws import register_telemetry_ws
from core.identity.auth_middleware import AuthCookieMiddleware
from core.identity.auth_routes import create_auth_router
from core.identity.credentials import CredentialStore
from core.identity.ws_auth import require_ws_auth
from core.warmup import start_warmup
from shared.streams import (
    USER_REQUESTS_STREAM,
    USER_RESPONSES_STREAM,
    decode_stream_value,
)

_lazy_cache: dict[str, Any] = {}
_FAILED: object = object()  # sentinel for imports that already failed

# Patchable in tests — must be set before the lifespan registers the SPA route.
_SPA_DIST: Path = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


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


# Model construction takes 10-40s and must run off the event loop; the lock
# keeps a warmup task and a first request from loading the same model twice.
_voice_load_lock = asyncio.Lock()


async def _aget_voice(key: str, getter: Callable[[], Any]) -> Any:
    cached = _lazy_cache.get(key)
    if cached is not None:
        return None if cached is _FAILED else cached
    async with _voice_load_lock:
        return await asyncio.to_thread(getter)


async def _aget_stt() -> Any:
    """WhisperSTT instance (or None), constructed off the event loop."""
    return await _aget_voice("stt", _get_stt)


async def _aget_tts() -> Any:
    """PiperTTS instance (or None), constructed off the event loop."""
    return await _aget_voice("tts", _get_tts)


async def _transcribe_async(stt: Any, audio_bytes: bytes, audio_fmt: str) -> str:
    """Run blocking Whisper transcription in a worker thread."""
    result = await asyncio.to_thread(stt.transcribe, audio_bytes, audio_format=audio_fmt)
    return cast("str", result)


async def _synthesize_async(tts: Any, text: str) -> bytes:
    """Run blocking Piper synthesis in a worker thread."""
    result = await asyncio.to_thread(tts.synthesize, text)
    return cast("bytes", result)


_active_websockets: dict[WebSocket, str] = {}
_CHANNEL_SOURCE_MAP: dict[str, str] = {
    "ios": "ios-app",
    "web_pwa": "web-pwa",
    "voice": "web-pwa",
}


def get_active_websockets() -> list[WebSocket]:
    """Return list of currently connected WebSocket sessions (all channels)."""
    return list(_active_websockets)


def get_web_websockets() -> list[WebSocket]:
    """Return only web/PWA WebSocket sessions (excludes iOS).

    iOS clients receive notifications via APNs, so WebSocket and Voice
    adapters should only push to web clients to avoid duplicates.
    """
    return [ws for ws, ch in _active_websockets.items() if ch != "ios"]


_ALLOWED_AUDIO_FORMATS = {"wav", "webm", "aac", "m4a", "ogg", "mp3"}


def _decode_audio(data_url: str) -> tuple[bytes, str]:
    """Decode a base64 data URL to raw bytes and extract audio format.

    Returns:
        Tuple of (audio_bytes, format_extension). Format is extracted from MIME type
        (e.g., 'webm', 'aac', 'wav'). Defaults to 'wav' if no MIME type found.
    """
    fmt = "wav"  # default
    if "," in data_url:
        header, encoded = data_url.split(",", 1)
        # Extract format from "data:audio/webm;base64" or "data:audio/aac;base64"
        if "audio/" in header:
            mime_part = header.split("audio/", 1)[1]
            extracted = mime_part.split(";", 1)[0].split("+", 1)[0].strip()
            if extracted in _ALLOWED_AUDIO_FORMATS:
                fmt = extracted
        return base64.b64decode(encoded), fmt
    return base64.b64decode(data_url), fmt


class OnboardingPayload(BaseModel):
    """Onboarding wizard submission from the PWA."""

    wake_time: str | None = None
    work_address: str | None = None
    dietary_restrictions: str | None = None
    proactivity_level: str | None = None  # opinionated | moderate | conservative
    guest_controls: list[str] | None = None


_DEVICE_TOKEN_PATTERN = r"^[a-fA-F0-9]+$"


class DeviceRegistration(BaseModel):
    """APNs device token registration."""

    device_token: str = Field(min_length=32, max_length=200, pattern=_DEVICE_TOKEN_PATTERN)
    platform: str = Field(pattern=r"^(ios|ipados|macos)$")
    identity: str


class DeviceUnregistration(BaseModel):
    """APNs device token removal."""

    device_token: str = Field(min_length=32, max_length=200, pattern=_DEVICE_TOKEN_PATTERN)


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


_TAILSCALE_RANGE = ipaddress.ip_network("100.64.0.0/10")


async def require_trusted_network(request: Request) -> None:
    """FastAPI dependency — restrict endpoint to localhost or Tailscale CGNAT range."""
    client_host = request.client.host if request.client else ""
    if client_host in ("127.0.0.1", "::1", "testclient"):
        return
    try:
        addr = ipaddress.ip_address(client_host)
        if addr in _TAILSCALE_RANGE:
            return
    except ValueError:
        pass
    raise HTTPException(status_code=403, detail="Access restricted to trusted networks")


async def _init_apns_adapter(pool: aioredis.Redis[Any]) -> None:  # type: ignore[type-arg]
    """Register APNs adapter if credentials are configured via environment."""
    team_id = os.getenv("APNS_TEAM_ID", "")
    key_id = os.getenv("APNS_KEY_ID", "")
    bundle_id = os.getenv("APNS_BUNDLE_ID", "")

    if not (team_id and key_id and bundle_id):
        logger.info("APNs credentials not configured, skipping adapter")
        return

    default_key_path = Path(__file__).resolve().parents[2] / "secrets" / f"AuthKey_{key_id}.p8"
    p8_path = Path(os.getenv("APNS_KEY_PATH") or default_key_path)

    if not p8_path.exists():
        logger.warning("APNs key file missing at {}, skipping adapter", p8_path)
        return

    private_key = p8_path.read_text()

    import core.notifications.adapters.apns  # noqa: F401 — trigger @register
    from core.notifications.adapters.apns import APNsChannelAdapter
    from core.notifications.channels import ChannelRegistry

    adapter = APNsChannelAdapter(
        redis=pool,
        team_id=team_id,
        key_id=key_id,
        private_key=private_key,
        bundle_id=bundle_id,
    )
    ChannelRegistry.set_instance("apns", adapter)
    logger.info("APNs adapter registered (team={}, bundle={})", team_id, bundle_id)


@asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """Manage shared Redis connection pool lifecycle + notification delivery."""
    from core.notifications.delivery import notification_delivery_worker

    pool: aioredis.Redis[Any] = aioredis.from_url(app.state.redis_url, decode_responses=False)  # type: ignore[type-arg]
    app.state.redis = pool
    app.state.http = httpx.AsyncClient(timeout=2.0)

    # Initialize WebAuthn credential store
    credential_store = CredentialStore()
    await credential_store.initialize()
    app.state.credential_store = credential_store

    # Mount auth router (needs both redis and credential_store)
    auth_router = create_auth_router(
        store=credential_store, redis=pool, trusted_network_dep=require_trusted_network
    )
    app.include_router(auth_router)

    # Must register the SPA catch-all AFTER the auth router so that
    # /{full_path:path} never shadows /api/auth/* routes (Starlette matches
    # routes in registration order).
    from core.channels.spa import mount_spa

    mount_spa(app, _SPA_DIST)

    try:
        await _init_apns_adapter(pool)
    except Exception as exc:
        logger.warning("APNs adapter init failed ({}): {}", type(exc).__name__, exc)

    shutdown = asyncio.Event()
    delivery_task = asyncio.create_task(
        notification_delivery_worker(pool, group="channels-delivery", shutdown=shutdown)
    )

    # Load voice models in the background so the first audio message doesn't
    # pay the 10-40s lazy-load cost; the server starts serving immediately.
    async def _warm_stt() -> None:
        if await _aget_stt() is None:
            raise RuntimeError("voice extra not installed or model failed to load")

    async def _warm_tts() -> None:
        if await _aget_tts() is None:
            raise RuntimeError("voice extra not installed or model failed to load")

    warmup_task = start_warmup("channels", {"whisper stt": _warm_stt, "piper tts": _warm_tts})

    yield

    shutdown.set()
    delivery_task.cancel()
    warmup_task.cancel()

    from core.notifications.channels import ChannelRegistry

    apns = ChannelRegistry.get_instance("apns")
    if apns is not None and hasattr(apns, "close"):
        await apns.close()

    await credential_store.close()
    await app.state.http.aclose()
    await pool.close()


def _ensure_integrations_registered() -> None:
    """Import integration adapter modules to trigger @register() decorators."""
    import core.integrations.apple_calendar
    import core.integrations.apple_health
    import core.integrations.robinhood
    import core.integrations.weather  # noqa: F401


def create_app(redis_url: str = "redis://localhost:6379") -> FastAPI:
    """Create the FastAPI application for the web channel."""
    _ensure_integrations_registered()
    app = FastAPI(title="Alfred Web Channel", lifespan=_lifespan)
    app.state.redis_url = redis_url

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "web-channel"}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        r: aioredis.Redis[Any] = app.state.redis  # type: ignore[type-arg]

        # Accept + cookie auth + 4001-on-fail, in the one place that owns the ordering.
        if not await require_ws_auth(websocket, r):
            return

        _active_websockets[websocket] = "web_pwa"

        # Assign session immediately so clients (iOS) that wait for the
        # session message before sending don't deadlock.
        session_id = str(uuid4())
        session_locked = False
        await websocket.send_json({"type": "session", "session_id": session_id})

        try:
            while True:
                data = await websocket.receive_json()

                # Allow client to restore a previous session on its first message only
                if not session_locked and (client_sid := data.get("session_id")):
                    session_id = client_sid
                session_locked = True

                raw_type = data.get("type", "text")
                content = data.get("content", "")

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
                    stt = await _aget_stt()
                    if stt is not None:
                        try:
                            audio_bytes, audio_fmt = _decode_audio(content)
                            content = await _transcribe_async(stt, audio_bytes, audio_fmt)
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

                client_channel = data.get("channel", "web_pwa")
                if client_channel not in ("web_pwa", "voice", "ios"):
                    client_channel = "web_pwa"
                _active_websockets[websocket] = client_channel
                request = UserRequest(
                    source=_CHANNEL_SOURCE_MAP.get(client_channel, "web-pwa"),
                    channel=client_channel,
                    session_id=session_id,
                    # Only authenticated sockets reach here (require_ws_auth gates above).
                    identity_claim="sir",
                    content_type=content_type,
                    content=content,
                )

                alfred_resp = await _publish_and_wait(r, request, session_id, timeout=60.0)

                response_payload: dict[str, Any] = {
                    "type": "response",
                    "text": alfred_resp.text,
                    "session_id": session_id,
                    "actions_taken": alfred_resp.actions_taken,
                    "mood": alfred_resp.mood,
                }

                # Synthesise audio for the response
                tts = await _aget_tts()
                if tts is not None:
                    try:
                        wav_bytes = await _synthesize_async(tts, alfred_resp.text)
                        response_payload["audio"] = base64.b64encode(wav_bytes).decode()
                    except Exception as exc:
                        logger.error("TTS synthesis failed: {}", exc)

                await websocket.send_json(response_payload)

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected (session={})", session_id)
        finally:
            _active_websockets.pop(websocket, None)

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
        dependencies=[Depends(require_trusted_network)],
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
        dependencies=[Depends(require_trusted_network)],
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
    async def save_onboarding(payload: OnboardingPayload, request: Request) -> dict[str, str]:
        """Save onboarding preferences to semantic memory files.

        Writes default values for any null fields. Skips writing if the
        preference file already exists (prevents clobbering Librarian data).
        """
        if not getattr(request.state, "authenticated", False):
            raise HTTPException(status_code=401, detail="Authentication required")
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

    @app.post(
        "/api/devices/register",
        dependencies=[Depends(require_trusted_network)],
    )
    async def register_device(payload: DeviceRegistration) -> dict[str, str]:
        """Register an APNs device token for push notifications."""
        from shared.streams import DEVICE_TOKENS_KEY

        r: aioredis.Redis[Any] = app.state.redis  # type: ignore[type-arg]
        value = json.dumps(
            {
                "platform": payload.platform,
                "identity": payload.identity,
                "registered_at": datetime.now(UTC).isoformat(),
            }
        )
        await r.hset(DEVICE_TOKENS_KEY, payload.device_token, value)  # type: ignore[misc]
        logger.info("Registered device token (platform={})", payload.platform)
        return {"status": "ok"}

    @app.delete(
        "/api/devices/register",
        dependencies=[Depends(require_trusted_network)],
    )
    async def unregister_device(payload: DeviceUnregistration) -> dict[str, str]:
        """Remove an APNs device token."""
        from shared.streams import DEVICE_TOKENS_KEY

        r: aioredis.Redis[Any] = app.state.redis  # type: ignore[type-arg]
        await r.hdel(DEVICE_TOKENS_KEY, payload.device_token)  # type: ignore[misc]
        logger.info("Unregistered device token")
        return {"status": "ok"}

    app.include_router(create_admin_router(require_trusted_network))
    register_telemetry_ws(app)

    class NoCacheStaticMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
            response: Response = await call_next(request)
            if request.url.path.endswith((".css", ".js", ".html")):
                response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return response

    app.add_middleware(NoCacheStaticMiddleware)
    app.add_middleware(AuthCookieMiddleware)

    return app


async def _publish_and_wait(
    redis: aioredis.Redis[Any],  # type: ignore[type-arg]
    request: UserRequest,
    session_id: str,
    timeout: float = 30.0,
) -> AlfredResponse:
    """Publish request and poll the responses stream for a matching response.

    Captures the latest stream ID before publishing to avoid scanning history.
    Returns the full AlfredResponse so callers can forward actions_taken and mood.
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
                        return resp

    logger.warning(
        "No response for session {} within {}s timeout — returning fallback",
        session_id,
        timeout,
    )
    return AlfredResponse(
        source="web-channel",
        channel="web_pwa",
        session_id=session_id,
        text="I apologize, sir — I seem to be taking longer than expected.",
    )
