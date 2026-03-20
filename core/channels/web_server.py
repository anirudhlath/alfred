"""Web channel server — FastAPI with WebSocket for voice + chat."""

from __future__ import annotations

import base64
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from bus.schemas.events import AlfredResponse, UserRequest
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM

# Optional: WhisperSTT for voice transcription, PiperTTS for speech output
_stt_instance: Any = None
_tts_instance: Any = None


def _get_stt() -> Any:
    """Lazy-load WhisperSTT (requires voice extra)."""
    global _stt_instance
    if _stt_instance is None:
        try:
            from core.voice.stt import WhisperSTT

            _stt_instance = WhisperSTT()
        except ImportError:
            logger.warning("faster-whisper not installed — voice transcription disabled")
            _stt_instance = False  # sentinel: tried and failed
        except Exception as exc:
            logger.error("Failed to initialise WhisperSTT: {}", exc)
            _stt_instance = False
    return _stt_instance if _stt_instance is not False else None


def _get_tts() -> Any:
    """Lazy-load PiperTTS (requires voice extra)."""
    global _tts_instance
    if _tts_instance is None:
        try:
            from core.voice.tts import PiperTTS

            _tts_instance = PiperTTS()
        except ImportError:
            logger.warning("piper-tts not installed — voice output disabled")
            _tts_instance = False
        except Exception as exc:
            logger.error("Failed to initialise PiperTTS: {}", exc)
            _tts_instance = False
    return _tts_instance if _tts_instance is not False else None


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


@asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """Manage shared Redis connection pool lifecycle."""
    pool: aioredis.Redis[Any] = aioredis.from_url(app.state.redis_url, decode_responses=False)
    app.state.redis = pool
    yield
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
        r: aioredis.Redis[Any] = app.state.redis

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

    @app.post("/api/onboarding")
    async def save_onboarding(payload: OnboardingPayload) -> dict[str, str]:
        """Save onboarding preferences to semantic memory files.

        This is a bootstrap path that writes directly to preference/profile files
        before the Librarian's first consolidation cycle.
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        prefs_dir = Path(__file__).resolve().parent.parent / "memory" / "preferences"
        profile_dir = Path(__file__).resolve().parent.parent / "memory" / "profile"
        prefs_dir.mkdir(parents=True, exist_ok=True)
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Write personal preferences (wake time, dietary, work address)
        lines: list[str] = []
        if payload.wake_time:
            lines.append(f"- Usual wake time: {payload.wake_time}")
        if payload.work_address:
            lines.append(f"- Work address: {payload.work_address}")
        if payload.dietary_restrictions:
            lines.append(f"- Dietary restrictions: {payload.dietary_restrictions}")
        if lines:
            _atomic_write(
                prefs_dir / "personal.md",
                _preference_file("general", today, "manual", "Personal", lines),
            )

        # Write proactivity level
        if payload.proactivity_level:
            _atomic_write(
                profile_dir / "proactivity.md",
                _preference_file(
                    "general",
                    today,
                    "manual",
                    "Proactivity Level",
                    [f"- Level: {payload.proactivity_level}"],
                ),
            )

        # Write guest mode config
        if payload.guest_controls:
            guest_lines = [f"- {ctrl}" for ctrl in payload.guest_controls]
            _atomic_write(
                prefs_dir / "guest_mode.md",
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
    redis: aioredis.Redis[Any],
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
                    event_str = raw.decode() if isinstance(raw, bytes) else raw
                    resp = AlfredResponse.model_validate_json(event_str)
                    if resp.session_id == session_id:
                        return resp.text

    return "I apologize, sir — I seem to be taking longer than expected."
