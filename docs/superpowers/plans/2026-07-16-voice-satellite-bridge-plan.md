# Voice Satellite Bridge Implementation Plan (Monorepo)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alfred-side support for physical "Hey Alfred" voice satellites: a Wyoming-protocol bridge in the channels process (voice loop + announcements), room-aware context, and a real SpeakerID implementation.

**Architecture:** Satellites run stock `wyoming-satellite` and listen on TCP 10700; the bridge *connects out* to each configured satellite, receives wake-triggered audio streams, endpoints them with silero VAD, runs the existing Whisper → Conscious → Piper pipeline, and streams reply audio back. A new `SatelliteChannelAdapter` reuses the open connections for spoken URGENT notifications. Spec: `docs/superpowers/specs/2026-07-15-voice-satellite-design.md`.

**Tech Stack:** Python 3.13, `wyoming` 1.10+ (protocol), `pysilero-vad` 3.4+ (endpointing, zero-dep), `speechbrain` 1.1+ (ECAPA-TDNN voiceprints), existing faster-whisper/piper-tts, FastAPI, Redis Streams, React 19 (enrollment card).

## Global Constraints

- Python 3.13+, async-first, Pydantic v2, type hints on all signatures, `mypy --strict` must pass.
- `uv` for packages; `ruff check . --fix && ruff format .` before every commit (line-length 100).
- `loguru` only — never stdlib logging.
- Stream/key constants come from `shared/streams.py` — never hardcode (`VOICEPRINT_KEY` already exists there).
- No polling: blocking reads and protocol keepalive pings are the allowed exceptions (same class as Redis blocking XREAD).
- Import `AioRedis` type alias from `shared.types`; `redis.asyncio` awaits may need `# type: ignore[misc]`.
- Tests live in `tests/core/...` mirroring module paths; root `conftest.py` provides autouse fixtures (InMemoryKeyring). NEVER add `tests/conftest.py`.
- Run tests with `.venv/bin/python -m pytest` (worktrees may default to system Python 3.14 — create the venv with `uv venv --python 3.13`).
- Channel adapter modules must be imported to fire `@ChannelRegistry.register()`; instances with constructor args are injected via `ChannelRegistry.set_instance()`.
- Voice models never run on the event loop — always `asyncio.to_thread` (existing convention in `core/channels/web_server.py`).
- Auto-download models on first use (Piper pattern) — never require manual downloads.

## Protocol facts (verified against wyoming 1.10.0, wyoming-satellite 1.4.1, HA integration — 2026-07)

The implementer should trust these; they were verified from source:

1. Server connects TO the satellite (`AsyncTcpClient(host, port)` from `wyoming.client`). Handshake: send `Describe()`, read until `Info`, then send `RunSatellite()` to arm.
2. On local wake detection the satellite sends `Detection`, then `RunPipeline(start_stage=asr, ...)`, then streams mic audio as `AudioChunk` events (16 kHz/16-bit/mono by default; **no** `AudioStart` for mic audio). It keeps streaming until the server sends **`Transcript`** (or `Error`/`PauseSatellite`) — `Transcript` is the "end of command" signal: satellite stops streaming, plays its done-WAV, re-arms local wake.
3. Server-side VAD is expected: send `VoiceStarted`/`VoiceStopped` (module `wyoming.vad`) as feedback (satellite uses them for LEDs); they are optional but cheap.
4. Reply TTS: send `Synthesize(text=...)` (FYI event), then `AudioStart(rate, width, channels, timestamp=0)` → `AudioChunk`s (payload = raw s16le PCM, 1024 samples/chunk) → `AudioStop()`. Satellite replies `Played` when done.
5. Announcements need NO special event: any server-sent `AudioStart/AudioChunk/AudioStop` outside a pipeline run is played by the satellite (this is exactly how HA's `assist_satellite.announce` works).
6. Keepalive: reply `Pong` to satellite `Ping`s; send our own `Ping` every ~10 s and treat 30 s of read silence as a dead connection.
7. All event classes share `.event() -> Event`, `Cls.is_type(event.type)`, `Cls.from_event(event)`. `AudioChunkConverter(rate=16000, width=2, channels=1).convert(chunk)` normalizes audio. `wyoming.event` has `async_read_event(reader)` / `async_write_event(event, writer)` for raw streams (used by the fake satellite in tests).
8. `pysilero-vad` 3.4: `SileroVoiceActivityDetector()` is callable — `det(pcm_1024_bytes) -> float` probability; `SileroVoiceActivityDetector.chunk_bytes() == 1024` (512 samples @16 kHz); `det.reset()` between utterances. We implement start/end hysteresis ourselves.
9. `speechbrain` 1.1: `from speechbrain.inference.speaker import EncoderClassifier`; `EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb", savedir=..., run_opts={"device": ...})`; `encode_batch(torch.Tensor[batch, time]) -> [batch, 1, 192]`, input 16 kHz mono float. Same-speaker cosine ≈ 0.4–0.7+, different ≈ 0.0–0.25 → default match threshold 0.45 (NOT the spec's original ~0.7; Task 16 amends the spec).

## File Structure

```
bus/schemas/events.py                      # + satellite channel, device_id/area/identity_confidence
core/channels/request_bus.py               # NEW: publish_and_wait (extracted from web_server)
core/channels/voice_models.py              # NEW: shared lazy STT/TTS/SpeakerID loaders (extracted)
core/channels/satellite/__init__.py        # NEW
core/channels/satellite/config.py          # NEW: SatelliteEntry + load_satellites()
core/channels/satellite/audio.py           # NEW: pcm_to_wav / wav_to_pcm
core/channels/satellite/endpointing.py     # NEW: UtteranceCollector + default_collector_factory
core/channels/satellite/bridge.py          # NEW: SatelliteConnection + SatelliteBridge
core/channels/satellite/pipeline.py        # NEW: SatellitePipeline (utterance handler)
core/voice/audio.py                        # NEW: decode_to_pcm16k (PyAV)
core/voice/speaker_id.py                   # stub → real ECAPA implementation
core/conscious/identity.py                 # + voice_id branch
core/conscious/context_assembler.py        # + area section, satellite voice channel
core/conscious/engine.py                   # pass identity_confidence + area through
core/notifications/adapters/satellite.py   # NEW: SatelliteChannelAdapter
core/channels/web_server.py                # delegate to request_bus/voice_models; /api/voice/enroll; lifespan wiring
config/satellites.yaml.example             # NEW
web/src/pages/VoiceEnrollmentCard.tsx      # NEW + SettingsPage wiring
docs/voice-satellites.md                   # NEW feature doc
```

---

### Task 1: Satellite channel + metadata fields in bus schemas

**Files:**
- Modify: `bus/schemas/events.py:92-114`
- Test: `tests/bus/test_satellite_schema.py`

**Interfaces:**
- Produces: `UserRequest` accepts `channel="satellite"` and has `device_id: str | None`, `area: str | None`, `identity_confidence: float | None` (all default `None`). `AlfredResponse` accepts `channel="satellite"`.

- [ ] **Step 1: Write the failing test**

```python
"""Satellite channel schema — backward compatibility and new fields."""

import pytest
from pydantic import ValidationError

from bus.schemas.events import AlfredResponse, UserRequest


def test_user_request_accepts_satellite_channel() -> None:
    req = UserRequest(
        source="satellite",
        channel="satellite",
        session_id="sat-kitchen",
        identity_claim="sir",
        content_type="audio",
        content="turn off the lights",
        device_id="kitchen",
        area="Kitchen",
        identity_confidence=0.82,
    )
    assert req.device_id == "kitchen"
    assert req.area == "Kitchen"
    assert req.identity_confidence == 0.82


def test_user_request_new_fields_default_none() -> None:
    """Old-style payloads (web/iOS/signal) validate unchanged."""
    req = UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id="s1",
        identity_claim="sir",
        content_type="text",
        content="hello",
    )
    assert req.device_id is None
    assert req.area is None
    assert req.identity_confidence is None


def test_user_request_roundtrip_without_new_fields() -> None:
    """JSON published by an older process (no new keys) still validates."""
    old_json = (
        '{"event_type": "user_request", "source": "web-pwa", "channel": "web_pwa",'
        ' "session_id": "s1", "identity_claim": "sir", "content_type": "text",'
        ' "content": "hi"}'
    )
    req = UserRequest.model_validate_json(old_json)
    assert req.identity_confidence is None


def test_alfred_response_accepts_satellite_channel() -> None:
    resp = AlfredResponse(
        source="conscious-engine", channel="satellite", session_id="sat-kitchen", text="Done."
    )
    assert resp.channel == "satellite"


def test_identity_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        UserRequest(
            source="satellite",
            channel="satellite",
            session_id="s",
            identity_claim="sir",
            content_type="audio",
            content="x",
            identity_confidence=1.5,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/bus/test_satellite_schema.py -v`
Expected: FAIL — `Input should be 'web_pwa', 'signal', 'voice' or 'ios'` / unexpected keyword errors.

- [ ] **Step 3: Implement the schema changes**

In `bus/schemas/events.py`, update `UserRequest` and `AlfredResponse`:

```python
class UserRequest(BaseEvent):
    """Inbound user interaction from any channel."""

    event_type: str = "user_request"
    channel: Literal["web_pwa", "signal", "voice", "ios", "satellite"]
    session_id: str
    identity_claim: str
    authenticated: bool = False
    content_type: Literal["text", "audio"]
    content: str
    audio_ref: str | None = None
    # Satellite metadata (None for non-satellite channels)
    device_id: str | None = None
    area: str | None = None
    identity_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class AlfredResponse(BaseEvent):
    """Outbound response to a user channel."""

    event_type: str = "alfred_response"
    channel: Literal["web_pwa", "signal", "voice", "ios", "satellite"]
    ...  # rest unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/bus/test_satellite_schema.py tests/bus -v`
Expected: PASS (including all existing bus tests).

- [ ] **Step 5: Run schema-reviewer agent** on the diff (backward compatibility check), fix anything raised.

- [ ] **Step 6: Commit**

```bash
git add bus/schemas/events.py tests/bus/test_satellite_schema.py
git commit -m "feat(bus): satellite channel + device/area/identity-confidence fields"
```

---

### Task 2: Extract `publish_and_wait` into `core/channels/request_bus.py`

**Files:**
- Create: `core/channels/request_bus.py`
- Modify: `core/channels/web_server.py:667-712` (remove `_publish_and_wait`, import instead)
- Test: `tests/core/channels/test_request_bus.py`

**Interfaces:**
- Produces: `async def publish_and_wait(redis: AioRedis, request: UserRequest, session_id: str, timeout: float = 30.0) -> AlfredResponse` in `core.channels.request_bus`. Fallback response uses `channel=request.channel` (bug fix: the old code hardcoded `web_pwa`).

- [ ] **Step 1: Write the failing test**

```python
"""publish_and_wait — shared request/response bus helper."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

from bus.schemas.events import AlfredResponse, UserRequest
from core.channels.request_bus import publish_and_wait
from shared.streams import USER_REQUESTS_STREAM


def _request(session_id: str = "sat-kitchen") -> UserRequest:
    return UserRequest(
        source="satellite",
        channel="satellite",
        session_id=session_id,
        identity_claim="sir",
        content_type="audio",
        content="hello",
    )


async def test_publishes_request_and_returns_matching_response() -> None:
    resp = AlfredResponse(
        source="conscious-engine", channel="satellite", session_id="sat-kitchen", text="Hello sir."
    )
    redis = AsyncMock()
    redis.xread = AsyncMock(
        return_value=[(b"stream", [(b"1-1", {b"event": resp.model_dump_json().encode()})])]
    )

    result = await publish_and_wait(redis, _request(), "sat-kitchen", timeout=5.0)

    assert result.text == "Hello sir."
    stream, payload = redis.xadd.call_args.args
    assert stream == USER_REQUESTS_STREAM
    assert json.loads(payload["event"])["session_id"] == "sat-kitchen"


async def test_timeout_returns_fallback_with_request_channel() -> None:
    redis = AsyncMock()

    async def _no_entries(*args: Any, **kwargs: Any) -> list[Any]:
        await asyncio.sleep(0.01)
        return []

    redis.xread = AsyncMock(side_effect=_no_entries)

    result = await publish_and_wait(redis, _request(), "sat-kitchen", timeout=0.05)

    assert result.channel == "satellite"  # not hardcoded web_pwa
    assert result.session_id == "sat-kitchen"


async def test_skips_responses_for_other_sessions() -> None:
    other = AlfredResponse(
        source="conscious-engine", channel="web_pwa", session_id="other", text="nope"
    )
    mine = AlfredResponse(
        source="conscious-engine", channel="satellite", session_id="sat-kitchen", text="yes"
    )
    redis = AsyncMock()
    redis.xread = AsyncMock(
        return_value=[
            (
                b"stream",
                [
                    (b"1-1", {b"event": other.model_dump_json().encode()}),
                    (b"1-2", {b"event": mine.model_dump_json().encode()}),
                ],
            )
        ]
    )

    result = await publish_and_wait(redis, _request(), "sat-kitchen", timeout=5.0)
    assert result.text == "yes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/channels/test_request_bus.py -v`
Expected: FAIL — `ModuleNotFoundError: core.channels.request_bus`.

- [ ] **Step 3: Create `core/channels/request_bus.py`**

Move the body of `_publish_and_wait` verbatim from `web_server.py` with two changes: public name, and the fallback `AlfredResponse` uses `source="channels"`, `channel=request.channel`:

```python
"""Shared request/response bus helper for user-facing channels."""

from __future__ import annotations

import time
from typing import Any

import redis.asyncio as aioredis
from loguru import logger

from bus.schemas.events import AlfredResponse, UserRequest
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM, decode_stream_value


async def publish_and_wait(
    redis: aioredis.Redis[Any],  # type: ignore[type-arg]
    request: UserRequest,
    session_id: str,
    timeout: float = 30.0,
) -> AlfredResponse:
    """Publish request and block-read the responses stream for a matching response.

    Captures the latest stream ID before publishing to avoid scanning history.
    Returns the full AlfredResponse so callers can forward actions_taken and mood.
    """
    last_id = f"{int(time.time() * 1000)}-0"

    await redis.xadd(USER_REQUESTS_STREAM, {"event": request.model_dump_json()})

    start = time.monotonic()
    while (time.monotonic() - start) < timeout:
        entries = await redis.xread({USER_RESPONSES_STREAM: last_id}, count=10, block=1000)
        for _stream, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                last_id = entry_id
                raw = entry_data.get(b"event") or entry_data.get("event")
                if raw:
                    resp = AlfredResponse.model_validate_json(decode_stream_value(raw))
                    if resp.session_id == session_id:
                        return resp

    logger.warning(
        "No response for session {} within {}s timeout — returning fallback", session_id, timeout
    )
    return AlfredResponse(
        source="channels",
        channel=request.channel,
        session_id=session_id,
        text="I apologize, sir — I seem to be taking longer than expected.",
    )
```

In `web_server.py`: delete the `_publish_and_wait` function, add `from core.channels.request_bus import publish_and_wait`, and change the call site (line ~439) to `await publish_and_wait(r, request, session_id, timeout=60.0)`. Remove now-unused imports (`time`, `USER_REQUESTS_STREAM`, `USER_RESPONSES_STREAM`, `decode_stream_value` — keep any still used elsewhere in the file; ruff will flag).

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/core/channels/test_request_bus.py tests/core/channels/test_web_server.py tests/core/channels/test_web_websockets.py -v`
Expected: PASS. If existing web tests referenced `_publish_and_wait` by name, update them to patch `core.channels.web_server.publish_and_wait` (the imported binding).

- [ ] **Step 5: Commit**

```bash
git add core/channels/request_bus.py core/channels/web_server.py tests/core/channels/test_request_bus.py
git commit -m "refactor(channels): extract publish_and_wait into shared request_bus"
```

---

### Task 3: Extract voice model loaders into `core/channels/voice_models.py`

**Files:**
- Create: `core/channels/voice_models.py`
- Modify: `core/channels/web_server.py:43-115` (move loaders, re-export)
- Test: existing `tests/core/channels/test_voice_async.py` must keep passing (no new tests — pure move)

**Interfaces:**
- Produces: `core.channels.voice_models` exports `_lazy_cache`, `_FAILED`, `_get_stt()`, `_get_tts()`, `_aget_stt()`, `_aget_tts()`, `_transcribe_async(stt, audio_bytes, audio_fmt)`, `_synthesize_async(tts, text)`. `web_server` re-imports these under the same names so existing tests and `core/channels/__main__.py` keep working.

- [ ] **Step 1: Move the code**

Cut from `web_server.py` lines 43–115 (the `_lazy_cache`/`_FAILED` module state, `_lazy_load`, `_get_stt`, `_get_tts`, `_voice_load_lock`, `_aget_voice`, `_aget_stt`, `_aget_tts`, `_transcribe_async`, `_synthesize_async`) into a new `core/channels/voice_models.py` with the module docstring `"""Shared lazy voice-model loaders for the channels process."""` and the imports they need (`asyncio`, `Any`, `cast`, `Callable` under TYPE_CHECKING, `loguru.logger`).

In `web_server.py` replace them with:

```python
from core.channels.voice_models import (  # noqa: F401 — re-exported for tests/__main__
    _FAILED,
    _aget_stt,
    _aget_tts,
    _get_stt,
    _get_tts,
    _lazy_cache,
    _synthesize_async,
    _transcribe_async,
)
```

- [ ] **Step 2: Run the full channels test suite**

Run: `.venv/bin/python -m pytest tests/core/channels -v`
Expected: PASS. If a test monkeypatches `web_server._lazy_cache` by *assignment* (not mutation), point it at `core.channels.voice_models._lazy_cache` instead.

- [ ] **Step 3: Verify lint/types and commit**

```bash
ruff check core/channels --fix && ruff format core/channels
mypy --strict core/channels
git add core/channels/voice_models.py core/channels/web_server.py tests/core/channels
git commit -m "refactor(channels): extract shared voice model loaders"
```

---

### Task 4: IdentityGate `voice_id` branch

**Files:**
- Modify: `core/conscious/identity.py:60-90`
- Modify: `core/conscious/engine.py:572-576` (pass `identity_confidence`)
- Test: `tests/core/conscious/test_identity_satellite.py`

**Interfaces:**
- Consumes: `UserRequest.identity_confidence` (Task 1).
- Produces: `IdentityGate.resolve(channel, identity_claim, authenticated, identity_confidence: float | None = None) -> IdentityResult`. Satellite + confidence → `method="voice_id"`; satellite without confidence → local-claim 0.7 (same trust as web/iOS).

- [ ] **Step 1: Write the failing test**

```python
"""IdentityGate — satellite channel resolution."""

from core.conscious.identity import IdentityGate


def _gate() -> IdentityGate:
    return IdentityGate(registered_phone="+15550001111")


def test_satellite_with_voiceprint_match_uses_voice_id() -> None:
    result = _gate().resolve(
        channel="satellite", identity_claim="sir", authenticated=False, identity_confidence=0.85
    )
    assert result.identity == "sir"
    assert result.method == "voice_id"
    assert result.confidence == 0.85
    assert result.factors == ["voiceprint"]
    assert result.risk_clearance == "low"


def test_satellite_without_voiceprint_falls_back_to_local_claim() -> None:
    result = _gate().resolve(
        channel="satellite", identity_claim="sir", authenticated=False, identity_confidence=None
    )
    assert result.identity == "sir"
    assert result.method == "local_claim"
    assert result.confidence == 0.7


def test_satellite_unknown_claim_is_guest() -> None:
    result = _gate().resolve(
        channel="satellite", identity_claim="guest", authenticated=False, identity_confidence=None
    )
    assert result.identity == "guest"


def test_existing_channels_unaffected() -> None:
    result = _gate().resolve(channel="web_pwa", identity_claim="sir", authenticated=False)
    assert result.method == "local_claim"
    assert result.confidence == 0.7
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/conscious/test_identity_satellite.py -v`
Expected: FAIL — unexpected keyword `identity_confidence` / unknown-channel warning path.

- [ ] **Step 3: Implement**

In `core/conscious/identity.py`, change `resolve` (keep existing branches; add the parameter and the satellite branch before the `web_pwa` branch):

```python
    def resolve(
        self,
        channel: str,
        identity_claim: str,
        authenticated: bool,
        identity_confidence: float | None = None,
    ) -> IdentityResult:
        """Unified resolution from a UserRequest's fields."""
        if channel == "signal":
            return self.resolve_signal(sender_phone=identity_claim)
        if channel == "satellite":
            if identity_confidence is not None and identity_claim == IDENTITY_SIR:
                return IdentityResult(
                    identity=IDENTITY_SIR,
                    confidence=identity_confidence,
                    method="voice_id",
                    factors=["voiceprint"],
                    risk_clearance="low",
                )
            if identity_claim == IDENTITY_SIR:
                return IdentityResult(
                    identity=IDENTITY_SIR,
                    confidence=0.7,
                    method="local_claim",
                    factors=["identity_claim"],
                    risk_clearance="low",
                )
            return self.resolve_session(authenticated=False)
        if channel in ("web_pwa", "voice", "ios"):
            ...  # unchanged
```

In `core/conscious/engine.py` (line ~572), pass the field through:

```python
        identity = self._identity_gate.resolve(
            channel=request.channel,
            identity_claim=request.identity_claim,
            authenticated=request.authenticated,
            identity_confidence=request.identity_confidence,
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/core/conscious -v`
Expected: PASS (new + existing).

- [ ] **Step 5: Commit**

```bash
git add core/conscious/identity.py core/conscious/engine.py tests/core/conscious/test_identity_satellite.py
git commit -m "feat(identity): voice_id resolution for satellite channel"
```

---

### Task 5: Room-aware context in ContextAssembler

**Files:**
- Modify: `core/conscious/context_assembler.py:37,46-56` (`_VOICE_CHANNELS`, `assemble` signature + Location section)
- Modify: `core/conscious/engine.py:630-640` (pass `area=request.area`)
- Test: `tests/core/conscious/test_context_area.py`

**Interfaces:**
- Consumes: `UserRequest.area` (Task 1).
- Produces: `ContextAssembler.assemble(..., area: str | None = None)` — emits a `## Location` section; `"satellite"` added to `_VOICE_CHANNELS` so the voice-delivery prompt engages for satellite audio requests.

- [ ] **Step 1: Write the failing test**

```python
"""ContextAssembler — satellite area injection."""

from core.conscious.context_assembler import ContextAssembler
from core.identity.schemas import IdentityResult


def _sir() -> IdentityResult:
    return IdentityResult(
        identity="sir", confidence=0.9, method="voice_id", factors=["voiceprint"],
        risk_clearance="low",
    )


def test_area_injects_location_section() -> None:
    prompt = ContextAssembler().assemble(
        identity=_sir(), tools_section="", channel="satellite", content_type="audio",
        area="Kitchen",
    )
    assert "## Location" in prompt
    assert "Kitchen" in prompt


def test_no_area_no_location_section() -> None:
    prompt = ContextAssembler().assemble(
        identity=_sir(), tools_section="", channel="web_pwa", content_type="text"
    )
    assert "## Location" not in prompt


def test_satellite_is_a_voice_channel() -> None:
    """Voice-delivery prompt engages for satellite audio requests."""
    assembler = ContextAssembler()
    assert "satellite" in assembler._VOICE_CHANNELS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/conscious/test_context_area.py -v`
Expected: FAIL — unexpected keyword `area`.

- [ ] **Step 3: Implement**

In `context_assembler.py`:

```python
    # Channels that have TTS output
    _VOICE_CHANNELS: frozenset[str] = frozenset({"web_pwa", "satellite"})
```

Add `area: str | None = None` to `assemble()`'s signature, and after the Identity section (section 2), insert:

```python
        # 2a. Location — physical satellite context
        if area:
            parts.append(
                f"\n## Location\nThis request was spoken at the {area} satellite. "
                f'When a device is referenced without naming a room ("the lights"), '
                f"assume the {area} area."
            )
```

In `engine.py`'s `assemble(...)` call (line ~630), add `area=request.area,`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/core/conscious -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/conscious/context_assembler.py core/conscious/engine.py tests/core/conscious/test_context_area.py
git commit -m "feat(conscious): room-aware Location context for satellite requests"
```

---

### Task 6: Satellite config loader

**Files:**
- Create: `core/channels/satellite/__init__.py` (empty docstring module)
- Create: `core/channels/satellite/config.py`
- Create: `config/satellites.yaml.example`
- Test: `tests/core/channels/satellite/test_config.py` (+ empty `tests/core/channels/satellite/__init__.py`)

**Interfaces:**
- Produces: `SatelliteEntry` (pydantic: `name: str`, `host: str`, `port: int = 10700`, `area: str | None = None`) and `load_satellites(path: Path | None = None) -> list[SatelliteEntry]` reading env `SATELLITES_CONFIG` (default `config/satellites.yaml`); missing file → `[]` with a log line, malformed → raises `ValueError`.

- [ ] **Step 1: Write the failing test**

```python
"""Satellite YAML config loader."""

from pathlib import Path

import pytest

from core.channels.satellite.config import SatelliteEntry, load_satellites


def test_load_satellites(tmp_path: Path) -> None:
    cfg = tmp_path / "satellites.yaml"
    cfg.write_text(
        """
satellites:
  - name: kitchen
    host: 192.168.1.40
    area: Kitchen
  - name: office
    host: office-sat.local
    port: 10701
"""
    )
    entries = load_satellites(cfg)
    assert entries == [
        SatelliteEntry(name="kitchen", host="192.168.1.40", port=10700, area="Kitchen"),
        SatelliteEntry(name="office", host="office-sat.local", port=10701, area=None),
    ]


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_satellites(tmp_path / "nope.yaml") == []


def test_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "custom.yaml"
    cfg.write_text("satellites:\n  - name: a\n    host: h\n")
    monkeypatch.setenv("SATELLITES_CONFIG", str(cfg))
    assert load_satellites()[0].name == "a"


def test_malformed_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("satellites:\n  - host-only: true\n")
    with pytest.raises(ValueError):
        load_satellites(cfg)


def test_duplicate_names_raise(tmp_path: Path) -> None:
    cfg = tmp_path / "dup.yaml"
    cfg.write_text(
        "satellites:\n  - name: a\n    host: h1\n  - name: a\n    host: h2\n"
    )
    with pytest.raises(ValueError):
        load_satellites(cfg)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/channels/satellite/test_config.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `core/channels/satellite/config.py`**

```python
"""Satellite fleet configuration — YAML single source of truth."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel, ValidationError

_DEFAULT_PATH = Path("config") / "satellites.yaml"


class SatelliteEntry(BaseModel):
    """One physical satellite device."""

    name: str
    host: str
    port: int = 10700
    area: str | None = None


def load_satellites(path: Path | None = None) -> list[SatelliteEntry]:
    """Load satellite entries from YAML. Missing file → empty fleet.

    Path resolution: explicit arg > SATELLITES_CONFIG env > config/satellites.yaml.
    """
    resolved = path or Path(os.getenv("SATELLITES_CONFIG", str(_DEFAULT_PATH)))
    if not resolved.exists():
        logger.info("No satellite config at {} — satellite bridge disabled", resolved)
        return []
    raw = yaml.safe_load(resolved.read_text()) or {}
    try:
        entries = [SatelliteEntry.model_validate(item) for item in raw.get("satellites", [])]
    except ValidationError as exc:
        raise ValueError(f"Invalid satellite config {resolved}: {exc}") from exc
    names = [e.name for e in entries]
    if len(names) != len(set(names)):
        raise ValueError(f"Duplicate satellite names in {resolved}")
    return entries
```

Create `config/satellites.yaml.example`:

```yaml
# Copy to config/satellites.yaml (gitignored) and edit.
# `area` must match a Home Assistant area name for room-aware commands.
satellites:
  - name: kitchen
    host: 192.168.1.40   # or kitchen-sat.local
    port: 10700          # wyoming-satellite default
    area: Kitchen
```

Add `config/satellites.yaml` to `.gitignore` (keep the `.example` tracked).

- [ ] **Step 4: Run tests, lint, commit**

Run: `.venv/bin/python -m pytest tests/core/channels/satellite -v` → PASS

```bash
git add core/channels/satellite tests/core/channels/satellite config/satellites.yaml.example .gitignore
git commit -m "feat(satellite): fleet config loader"
```

---

### Task 7: Audio utilities (WAV framing + PyAV decode)

**Files:**
- Create: `core/channels/satellite/audio.py`
- Create: `core/voice/audio.py`
- Test: `tests/core/channels/satellite/test_audio.py`, `tests/core/voice/test_audio_decode.py`

**Interfaces:**
- Produces: `pcm_to_wav(pcm: bytes, rate: int = 16000, width: int = 2, channels: int = 1) -> bytes` and `wav_to_pcm(wav: bytes) -> tuple[bytes, int, int, int]` (pcm, rate, width, channels) in `core.channels.satellite.audio`; `decode_to_pcm16k(data: bytes) -> bytes` in `core.voice.audio` (any container/codec PyAV can read → 16 kHz s16 mono PCM).

- [ ] **Step 1: Write the failing tests**

`tests/core/channels/satellite/test_audio.py`:

```python
"""WAV framing helpers."""

from core.channels.satellite.audio import pcm_to_wav, wav_to_pcm


def test_pcm_wav_roundtrip() -> None:
    pcm = bytes(range(256)) * 8  # 2048 bytes of arbitrary s16le
    wav = pcm_to_wav(pcm, rate=16000)
    out, rate, width, channels = wav_to_pcm(wav)
    assert (out, rate, width, channels) == (pcm, 16000, 2, 1)


def test_pcm_to_wav_has_riff_header() -> None:
    assert pcm_to_wav(b"\x00\x00" * 160)[:4] == b"RIFF"
```

`tests/core/voice/test_audio_decode.py`:

```python
"""PyAV decode helper (voice extra)."""

import pytest

av = pytest.importorskip("av")

from core.channels.satellite.audio import pcm_to_wav
from core.voice.audio import decode_to_pcm16k


def test_decode_wav_to_pcm16k() -> None:
    # 0.5s of silence @ 22050 Hz mono s16 — decode must resample to 16 kHz
    src = pcm_to_wav(b"\x00\x00" * 11025, rate=22050)
    pcm = decode_to_pcm16k(src)
    n_samples = len(pcm) // 2
    assert abs(n_samples - 8000) < 160  # ~0.5s @ 16kHz, resampler edge tolerance
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/core/channels/satellite/test_audio.py tests/core/voice/test_audio_decode.py -v`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement**

`core/channels/satellite/audio.py`:

```python
"""PCM/WAV framing helpers for the satellite bridge."""

from __future__ import annotations

import io
import wave


def pcm_to_wav(pcm: bytes, rate: int = 16000, width: int = 2, channels: int = 1) -> bytes:
    """Wrap raw PCM in a WAV container (for Whisper/file interfaces)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm(wav_bytes: bytes) -> tuple[bytes, int, int, int]:
    """Extract (pcm, rate, width, channels) from a WAV container (for Wyoming playback)."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        return (
            wav.readframes(wav.getnframes()),
            wav.getframerate(),
            wav.getsampwidth(),
            wav.getnchannels(),
        )
```

`core/voice/audio.py`:

```python
"""Audio decode helpers — any browser/HTTP upload to canonical 16 kHz mono PCM."""

from __future__ import annotations

import io


def decode_to_pcm16k(data: bytes) -> bytes:
    """Decode any PyAV-readable audio (webm/opus, wav, m4a…) to 16 kHz s16 mono PCM.

    PyAV ships with faster-whisper (voice extra). Import is local so the module
    can be imported without the extra installed.
    """
    import av

    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    out = bytearray()
    with av.open(io.BytesIO(data)) as container:
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                out.extend(bytes(resampled.planes[0]))
    for resampled in resampler.resample(None):  # flush
        out.extend(bytes(resampled.planes[0]))
    return bytes(out)
```

Add mypy override in `pyproject.toml` (append to the existing override list):

```toml
[[tool.mypy.overrides]]
module = ["av.*"]
ignore_missing_imports = true
```

- [ ] **Step 4: Run tests, commit**

Run: `.venv/bin/python -m pytest tests/core/channels/satellite/test_audio.py tests/core/voice/test_audio_decode.py -v` → PASS

```bash
git add core/channels/satellite/audio.py core/voice/audio.py tests/core/channels/satellite/test_audio.py tests/core/voice/test_audio_decode.py pyproject.toml
git commit -m "feat(voice): PCM/WAV framing + PyAV decode helpers"
```

---

### Task 8: Utterance endpointing (`UtteranceCollector`)

**Files:**
- Create: `core/channels/satellite/endpointing.py`
- Modify: `pyproject.toml` (voice extra: `pysilero-vad>=3.4`; mypy override `pysilero_vad.*`)
- Test: `tests/core/channels/satellite/test_endpointing.py`

**Interfaces:**
- Produces:
  - `CollectorEvent` dataclass: `kind: Literal["speech_start", "utterance", "timeout"]`, `pcm: bytes | None` (set only for `utterance`).
  - `UtteranceCollector(vad: Callable[[bytes], float], *, threshold=0.5, end_threshold=0.35, silence_ms=800, no_speech_timeout_ms=8000, max_utterance_ms=15000)` with `feed(pcm: bytes) -> list[CollectorEvent]`. Feed accepts arbitrary chunk sizes; internally frames to 1024 bytes (512 samples @ 16 kHz = 32 ms). Keeps a 320 ms pre-speech ring buffer prepended to the utterance. After emitting `utterance` or `timeout` the collector is exhausted (further feeds return `[]`).
  - `default_collector_factory() -> UtteranceCollector` — builds a real `SileroVoiceActivityDetector`-backed collector (import inside the function).

- [ ] **Step 1: Write the failing test**

```python
"""UtteranceCollector — streaming VAD endpointing with scripted probabilities."""

from collections import deque

from core.channels.satellite.endpointing import CollectorEvent, UtteranceCollector

FRAME = b"\x01\x00" * 512  # one 32ms frame (1024 bytes)
_MS_PER_FRAME = 32


def _collector(probs: list[float], **kwargs: object) -> UtteranceCollector:
    q = deque(probs)
    return UtteranceCollector(vad=lambda _frame: q.popleft() if q else 0.0, **kwargs)  # type: ignore[arg-type]


def _feed_frames(c: UtteranceCollector, n: int) -> list[CollectorEvent]:
    events: list[CollectorEvent] = []
    for _ in range(n):
        events.extend(c.feed(FRAME))
    return events


def test_speech_then_silence_emits_utterance() -> None:
    speech_frames = 10
    silence_frames = 800 // _MS_PER_FRAME + 1  # cross the 800ms end threshold
    c = _collector([0.9] * speech_frames + [0.0] * (silence_frames + 5))
    events = _feed_frames(c, speech_frames + silence_frames + 5)
    kinds = [e.kind for e in events]
    assert kinds[0] == "speech_start"
    assert "utterance" in kinds
    utterance = next(e for e in events if e.kind == "utterance")
    assert utterance.pcm is not None
    # utterance includes the spoken frames (plus pre-roll/tail padding)
    assert len(utterance.pcm) >= speech_frames * len(FRAME)


def test_no_speech_times_out() -> None:
    frames = 8000 // _MS_PER_FRAME + 2
    c = _collector([0.0] * frames)
    events = _feed_frames(c, frames)
    assert [e.kind for e in events] == ["timeout"]


def test_max_utterance_forces_end() -> None:
    frames = 15000 // _MS_PER_FRAME + 5
    c = _collector([0.9] * frames)
    events = _feed_frames(c, frames)
    assert events[0].kind == "speech_start"
    assert events[-1].kind == "utterance"


def test_exhausted_after_utterance() -> None:
    c = _collector([0.9] * 10 + [0.0] * 40)
    _feed_frames(c, 50)
    assert c.feed(FRAME) == []


def test_partial_chunks_are_buffered() -> None:
    """Feeding half-frames must not crash and must eventually frame up."""
    c = _collector([0.0] * 10)
    assert c.feed(FRAME[:512]) == []
    assert c.feed(FRAME[512:]) == []  # completes exactly one frame → one vad call
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/channels/satellite/test_endpointing.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `core/channels/satellite/endpointing.py`**

```python
"""Streaming utterance endpointing — silero VAD probabilities + hysteresis.

The satellite streams mic audio indefinitely after wake; the server decides
when the command has ended. Start when prob >= threshold; end when prob stays
below end_threshold for silence_ms. Frames are 512 samples (1024 bytes) of
16 kHz s16 mono PCM — pysilero-vad's fixed chunk size.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

FRAME_BYTES = 1024  # 512 samples @ 16kHz s16 mono = 32 ms
_MS_PER_FRAME = 32
_PRE_ROLL_FRAMES = 10  # 320 ms kept before speech start


@dataclass(frozen=True)
class CollectorEvent:
    """State transition emitted by the collector."""

    kind: Literal["speech_start", "utterance", "timeout"]
    pcm: bytes | None = None


class UtteranceCollector:
    """Single-utterance collector. Create one per pipeline run; not reusable."""

    def __init__(
        self,
        vad: Callable[[bytes], float],
        *,
        threshold: float = 0.5,
        end_threshold: float = 0.35,
        silence_ms: int = 800,
        no_speech_timeout_ms: int = 8000,
        max_utterance_ms: int = 15000,
    ) -> None:
        self._vad = vad
        self._threshold = threshold
        self._end_threshold = end_threshold
        self._silence_frames_needed = max(1, silence_ms // _MS_PER_FRAME)
        self._no_speech_frames = max(1, no_speech_timeout_ms // _MS_PER_FRAME)
        self._max_frames = max(1, max_utterance_ms // _MS_PER_FRAME)
        self._buffer = bytearray()
        self._pre_roll: list[bytes] = []
        self._speech: bytearray = bytearray()
        self._in_speech = False
        self._silence_run = 0
        self._frames_seen = 0
        self._speech_frames = 0
        self._done = False

    def feed(self, pcm: bytes) -> list[CollectorEvent]:
        """Feed arbitrary-size PCM; returns zero or more state transitions."""
        if self._done:
            return []
        self._buffer.extend(pcm)
        events: list[CollectorEvent] = []
        while len(self._buffer) >= FRAME_BYTES and not self._done:
            frame = bytes(self._buffer[:FRAME_BYTES])
            del self._buffer[:FRAME_BYTES]
            events.extend(self._process_frame(frame))
        return events

    def _process_frame(self, frame: bytes) -> list[CollectorEvent]:
        self._frames_seen += 1
        prob = self._vad(frame)

        if not self._in_speech:
            self._pre_roll.append(frame)
            if len(self._pre_roll) > _PRE_ROLL_FRAMES:
                self._pre_roll.pop(0)
            if prob >= self._threshold:
                self._in_speech = True
                self._speech.extend(b"".join(self._pre_roll))
                return [CollectorEvent(kind="speech_start")]
            if self._frames_seen >= self._no_speech_frames:
                self._done = True
                return [CollectorEvent(kind="timeout")]
            return []

        self._speech.extend(frame)
        self._speech_frames += 1
        self._silence_run = self._silence_run + 1 if prob < self._end_threshold else 0

        if (
            self._silence_run >= self._silence_frames_needed
            or self._speech_frames >= self._max_frames
        ):
            self._done = True
            return [CollectorEvent(kind="utterance", pcm=bytes(self._speech))]
        return []


def default_collector_factory() -> UtteranceCollector:
    """Real silero-backed collector (one detector per utterance; ggml, loads in ms)."""
    from pysilero_vad import SileroVoiceActivityDetector

    detector = SileroVoiceActivityDetector()
    return UtteranceCollector(vad=detector)
```

In `pyproject.toml`: add `"pysilero-vad>=3.4"` to the `voice` extra and a mypy override:

```toml
[[tool.mypy.overrides]]
module = ["pysilero_vad.*"]
ignore_missing_imports = true
```

Then `uv pip install -e ".[dev,memory,voice,integrations]"`.

- [ ] **Step 4: Run tests, commit**

Run: `.venv/bin/python -m pytest tests/core/channels/satellite/test_endpointing.py -v` → PASS

```bash
git add core/channels/satellite/endpointing.py tests/core/channels/satellite/test_endpointing.py pyproject.toml uv.lock
git commit -m "feat(satellite): streaming VAD utterance endpointing"
```

---

### Task 9: SpeakerID — stub becomes real (ECAPA-TDNN)

**Files:**
- Modify: `core/voice/speaker_id.py` (full rewrite, keep class/dataclass names)
- Modify: `pyproject.toml` (voice extra: `speechbrain>=1.1`; mypy override `speechbrain.*`)
- Test: `tests/core/voice/test_speaker_id.py`

**Interfaces:**
- Consumes: `VOICEPRINT_KEY` from `shared.streams`; `AioRedis` from `shared.types`.
- Produces:
  - `SpeakerMatch(identity: str, confidence: float, enrolled: bool)` (unchanged dataclass).
  - `SpeakerID(redis: AioRedis, *, threshold: float | None = None, device: str = "cpu", embed_fn: Callable[[bytes], np.ndarray] | None = None)`. `threshold` default from env `SPEAKER_ID_THRESHOLD` else `0.45`.
  - `async identify(pcm: bytes) -> SpeakerMatch` — input is 16 kHz s16 mono PCM. Cosine vs all enrolled prints; score ≥ threshold → `SpeakerMatch(identity, confidence, enrolled=True)` with `confidence = min(0.95, 0.7 + (score - threshold) * 0.5)`; otherwise `SpeakerMatch("unknown", 0.0, False)`. No enrolled prints → unknown.
  - `async enroll(identity: str, audio_samples: list[bytes]) -> bool` — mean of per-sample normalized embeddings, L2-normalized, stored as float32 bytes in Redis hash `VOICEPRINT_KEY` field `identity`.
  - `async delete(identity: str) -> None`, `async enrolled_identities() -> list[str]`.
  - `embed_fn` injects a synchronous embedding function for tests; the default lazily loads SpeechBrain ECAPA off the event loop (asyncio.Lock + `asyncio.to_thread`), `savedir=data/models/spkrec-ecapa-voxceleb` (auto-download).

- [ ] **Step 1: Write the failing test**

```python
"""SpeakerID — voiceprint enroll/identify with injected embedder."""

import numpy as np
from typing import Any
from unittest.mock import AsyncMock

from core.voice.speaker_id import SpeakerID
from shared.streams import VOICEPRINT_KEY


def _unit(v: list[float]) -> np.ndarray:
    arr = np.array(v, dtype=np.float32)
    return arr / np.linalg.norm(arr)


def _fake_redis(store: dict[bytes, bytes]) -> AsyncMock:
    redis = AsyncMock()

    async def _hset(key: str, field: str, value: bytes) -> int:
        store[field.encode()] = value
        return 1

    async def _hgetall(key: str) -> dict[bytes, bytes]:
        return dict(store)

    redis.hset = AsyncMock(side_effect=_hset)
    redis.hgetall = AsyncMock(side_effect=_hgetall)
    redis.hdel = AsyncMock(side_effect=lambda key, field: store.pop(field.encode(), None))
    return redis


def _speaker_id(store: dict[bytes, bytes], embeddings: dict[bytes, list[float]]) -> SpeakerID:
    """embed_fn maps exact pcm bytes → fixed unit vectors."""

    def embed(pcm: bytes) -> np.ndarray:
        return _unit(embeddings[pcm])

    return SpeakerID(_fake_redis(store), threshold=0.45, embed_fn=embed)


async def test_enroll_stores_normalized_mean() -> None:
    store: dict[bytes, bytes] = {}
    sid = _speaker_id(store, {b"s1": [1.0, 0.0], b"s2": [0.0, 1.0]})
    assert await sid.enroll("sir", [b"s1", b"s2"]) is True
    stored = np.frombuffer(store[b"sir"], dtype=np.float32)
    assert np.allclose(np.linalg.norm(stored), 1.0, atol=1e-5)


async def test_identify_match_above_threshold() -> None:
    store: dict[bytes, bytes] = {}
    sid = _speaker_id(store, {b"enroll": [1.0, 0.0], b"query": [0.95, 0.1]})
    await sid.enroll("sir", [b"enroll"])
    match = await sid.identify(b"query")
    assert match.identity == "sir"
    assert match.enrolled is True
    assert 0.7 <= match.confidence <= 0.95


async def test_identify_below_threshold_is_unknown() -> None:
    store: dict[bytes, bytes] = {}
    sid = _speaker_id(store, {b"enroll": [1.0, 0.0], b"query": [0.0, 1.0]})
    await sid.enroll("sir", [b"enroll"])
    match = await sid.identify(b"query")
    assert match == await sid.identify(b"query")  # deterministic
    assert match.identity == "unknown"
    assert match.enrolled is False
    assert match.confidence == 0.0


async def test_identify_with_no_enrollments() -> None:
    sid = _speaker_id({}, {b"q": [1.0, 0.0]})
    match = await sid.identify(b"q")
    assert match.identity == "unknown"


async def test_identify_picks_best_of_multiple() -> None:
    store: dict[bytes, bytes] = {}
    sid = _speaker_id(
        store, {b"a": [1.0, 0.0], b"b": [0.0, 1.0], b"q": [0.9, 0.44]}
    )
    await sid.enroll("sir", [b"a"])
    await sid.enroll("guest_bob", [b"b"])
    assert (await sid.identify(b"q")).identity == "sir"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/voice/test_speaker_id.py -v`
Expected: FAIL — `SpeakerID.__init__` takes no arguments (current stub).

- [ ] **Step 3: Rewrite `core/voice/speaker_id.py`**

```python
"""SpeakerID — voiceprint speaker identification (ECAPA-TDNN embeddings).

Enrollment: mean of normalized per-sample embeddings → Redis hash
VOICEPRINT_KEY (field = identity, value = float32 bytes).
Inference: cosine similarity of the utterance embedding vs all enrolled prints.

Input contract: 16 kHz, 16-bit, mono PCM bytes.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from loguru import logger

from shared.streams import VOICEPRINT_KEY

if TYPE_CHECKING:
    from collections.abc import Callable

    from shared.types import AioRedis

_DEFAULT_THRESHOLD = 0.45  # ECAPA cosine: same speaker ≈ 0.4-0.7+, different ≈ 0.0-0.25
_MODEL_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
_MODEL_DIR = Path("data") / "models" / "spkrec-ecapa-voxceleb"


@dataclass(frozen=True)
class SpeakerMatch:
    """Result of a speaker identification attempt."""

    identity: str
    confidence: float
    enrolled: bool


_UNKNOWN = SpeakerMatch(identity="unknown", confidence=0.0, enrolled=False)


class SpeakerID:
    """Voiceprint-based speaker identification backed by Redis storage."""

    def __init__(
        self,
        redis: AioRedis,
        *,
        threshold: float | None = None,
        device: str = "cpu",
        embed_fn: Callable[[bytes], np.ndarray] | None = None,
    ) -> None:
        self._redis = redis
        self._threshold = (
            threshold
            if threshold is not None
            else float(os.getenv("SPEAKER_ID_THRESHOLD", str(_DEFAULT_THRESHOLD)))
        )
        self._device = device
        self._embed_fn = embed_fn
        self._model: Any = None
        self._load_lock = asyncio.Lock()

    async def identify(self, audio_bytes: bytes) -> SpeakerMatch:
        """Identify the speaker of a 16 kHz s16 mono PCM utterance."""
        prints = await self._load_prints()
        if not prints:
            return _UNKNOWN
        embedding = await self._embed(audio_bytes)
        best_identity, best_score = "", -1.0
        for identity, print_vec in prints.items():
            score = float(np.dot(embedding, print_vec))
            if score > best_score:
                best_identity, best_score = identity, score
        if best_score < self._threshold:
            logger.debug("SpeakerID: best={} score={:.3f} < threshold", best_identity, best_score)
            return _UNKNOWN
        confidence = min(0.95, 0.7 + (best_score - self._threshold) * 0.5)
        return SpeakerMatch(identity=best_identity, confidence=confidence, enrolled=True)

    async def enroll(self, identity: str, audio_samples: list[bytes]) -> bool:
        """Enroll a voiceprint from one or more PCM samples."""
        if not audio_samples:
            return False
        embeddings = [await self._embed(s) for s in audio_samples]
        mean = np.mean(np.stack(embeddings), axis=0)
        mean = mean / (np.linalg.norm(mean) + 1e-10)
        await self._redis.hset(  # type: ignore[misc]
            VOICEPRINT_KEY, identity, mean.astype(np.float32).tobytes()
        )
        logger.info("Enrolled voiceprint for '{}' ({} samples)", identity, len(audio_samples))
        return True

    async def delete(self, identity: str) -> None:
        """Remove an enrolled voiceprint."""
        await self._redis.hdel(VOICEPRINT_KEY, identity)  # type: ignore[misc]

    async def enrolled_identities(self) -> list[str]:
        """List enrolled identity labels."""
        return sorted((await self._load_prints()).keys())

    async def _load_prints(self) -> dict[str, np.ndarray]:
        raw: dict[bytes, bytes] = await self._redis.hgetall(VOICEPRINT_KEY)  # type: ignore[misc]
        return {k.decode(): np.frombuffer(v, dtype=np.float32) for k, v in raw.items()}

    async def _embed(self, pcm: bytes) -> np.ndarray:
        if self._embed_fn is not None:
            return await asyncio.to_thread(self._embed_fn, pcm)
        await self._ensure_model()
        return await asyncio.to_thread(self._embed_ecapa, pcm)

    async def _ensure_model(self) -> None:
        if self._model is not None:
            return
        async with self._load_lock:
            if self._model is not None:
                return

            def _load() -> Any:
                from speechbrain.inference.speaker import EncoderClassifier

                return EncoderClassifier.from_hparams(
                    source=_MODEL_SOURCE,
                    savedir=str(_MODEL_DIR),
                    run_opts={"device": self._device},
                )

            self._model = await asyncio.to_thread(_load)
            logger.info("SpeakerID: loaded ECAPA model on {}", self._device)

    def _embed_ecapa(self, pcm: bytes) -> np.ndarray:
        import torch

        wav = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        tensor = torch.from_numpy(wav).unsqueeze(0)
        emb = self._model.encode_batch(tensor)  # [1, 1, 192]
        vec = emb.squeeze().detach().cpu().numpy().astype(np.float32)
        return vec / (np.linalg.norm(vec) + 1e-10)
```

In `pyproject.toml`: add `"speechbrain>=1.1"` to the `voice` extra and `speechbrain.*` to a mypy override (can join the `av.*` list). Then `uv pip install -e ".[dev,memory,voice,integrations]"`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/core/voice -v`
Expected: PASS. Also run `mypy --strict core/voice/`.

- [ ] **Step 5: Commit**

```bash
git add core/voice/speaker_id.py tests/core/voice/test_speaker_id.py pyproject.toml uv.lock
git commit -m "feat(voice): real ECAPA-TDNN SpeakerID (enroll/identify via Redis voiceprints)"
```

---

### Task 10: Voice enrollment API (`POST /api/voice/enroll`)

**Files:**
- Modify: `core/channels/voice_models.py` (add `aget_speaker_id`)
- Modify: `core/channels/web_server.py` (new endpoint + payload model, near the onboarding endpoint)
- Test: `tests/core/channels/test_voice_enroll.py`

**Interfaces:**
- Consumes: `SpeakerID` (Task 9), `decode_to_pcm16k` (Task 7).
- Produces:
  - `voice_models.aget_speaker_id(redis: Any) -> Any | None` — lazy singleton, `None` when voice extra missing (same `_lazy_cache`/`_FAILED` pattern, key `"speaker_id"`).
  - `POST /api/voice/enroll` `{identity: str, samples: [dataURL, ...]}` → `{"status": "enrolled", "identity": ...}`; 503 when voice unavailable; gated by `require_trusted_network` + authenticated session (same double-gate as admin routes).

- [ ] **Step 1: Write the failing test**

```python
"""POST /api/voice/enroll."""

from typing import Any
from unittest.mock import AsyncMock, patch

import base64

from fastapi.testclient import TestClient

from core.channels.satellite.audio import pcm_to_wav


def _sample() -> str:
    wav = pcm_to_wav(b"\x00\x01" * 16000)  # 1s of noise-ish PCM
    return "data:audio/wav;base64," + base64.b64encode(wav).decode()


def test_enroll_happy_path(web_client: TestClient) -> None:
    speaker_id = AsyncMock()
    speaker_id.enroll = AsyncMock(return_value=True)
    with (
        patch("core.channels.web_server.aget_speaker_id", AsyncMock(return_value=speaker_id)),
        patch("core.voice.audio.decode_to_pcm16k", return_value=b"\x00\x00" * 16000),
    ):
        resp = web_client.post(
            "/api/voice/enroll",
            json={"identity": "sir", "samples": [_sample(), _sample(), _sample()]},
        )
    assert resp.status_code == 200
    assert resp.json() == {"status": "enrolled", "identity": "sir"}
    pcm_lists: list[Any] = speaker_id.enroll.call_args.args[1]
    assert len(pcm_lists) == 3


def test_enroll_unavailable_without_voice_extra(web_client: TestClient) -> None:
    with patch("core.channels.web_server.aget_speaker_id", AsyncMock(return_value=None)):
        resp = web_client.post(
            "/api/voice/enroll", json={"identity": "sir", "samples": [_sample()] * 3}
        )
    assert resp.status_code == 503


def test_enroll_validates_identity(web_client: TestClient) -> None:
    resp = web_client.post(
        "/api/voice/enroll", json={"identity": "Bad Name!", "samples": [_sample()] * 3}
    )
    assert resp.status_code == 422
```

(The `web_client` fixture in `tests/core/channels/conftest.py` already provides trusted-network + authed cookies.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/channels/test_voice_enroll.py -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Implement**

In `core/channels/voice_models.py` append:

```python
def _get_speaker_id_cls() -> Any:
    """Lazy-import SpeakerID class (requires voice extra deps at embed time)."""
    from core.voice.speaker_id import SpeakerID

    return SpeakerID


async def aget_speaker_id(redis: Any) -> Any | None:
    """Shared SpeakerID singleton, or None if the voice extra is unavailable."""
    cached = _lazy_cache.get("speaker_id")
    if cached is _FAILED:
        return None
    if cached is not None:
        return cached
    try:
        instance = _get_speaker_id_cls()(redis)
    except ImportError:
        logger.warning("speechbrain not installed — speaker ID disabled")
        _lazy_cache["speaker_id"] = _FAILED
        return None
    _lazy_cache["speaker_id"] = instance
    return instance
```

In `web_server.py`, import `aget_speaker_id` from `voice_models`, then add near `OnboardingPayload`:

```python
class VoiceEnrollmentPayload(BaseModel):
    """Voice enrollment samples from the settings page."""

    identity: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    samples: list[str] = Field(min_length=3, max_length=5)
```

and inside `create_app`, next to the other gated routes:

```python
    @app.post("/api/voice/enroll", dependencies=[Depends(require_trusted_network)])
    async def voice_enroll(payload: VoiceEnrollmentPayload, request: Request) -> dict[str, str]:
        """Enroll a voiceprint from mic samples (trusted network + session only)."""
        if not getattr(request.state, "authenticated", False):
            raise HTTPException(status_code=401, detail="Authentication required")
        speaker_id = await aget_speaker_id(app.state.redis)
        if speaker_id is None:
            raise HTTPException(status_code=503, detail="Voice processing unavailable")
        from core.voice.audio import decode_to_pcm16k

        pcm_samples: list[bytes] = []
        for sample in payload.samples:
            audio_bytes, _fmt = _decode_audio(sample)
            pcm_samples.append(await asyncio.to_thread(decode_to_pcm16k, audio_bytes))
        if not await speaker_id.enroll(payload.identity, pcm_samples):
            raise HTTPException(status_code=500, detail="Enrollment failed")
        return {"status": "enrolled", "identity": payload.identity}
```

NOTE: check how `request.state.authenticated` is set by `AuthCookieMiddleware`; if the flag lives elsewhere (e.g. `request.state.auth`), mirror what `core/channels/admin_api.py`'s `require_authenticated` does — reuse that dependency directly if it is importable.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/core/channels/test_voice_enroll.py tests/core/channels/test_web_server.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add core/channels/voice_models.py core/channels/web_server.py tests/core/channels/test_voice_enroll.py
git commit -m "feat(channels): voice enrollment endpoint"
```

---

### Task 11: Wyoming bridge (`SatelliteConnection` + `SatelliteBridge`)

**Files:**
- Create: `core/channels/satellite/bridge.py`
- Modify: `pyproject.toml` (base deps: `wyoming>=1.10`; mypy override `wyoming.*` only if `mypy --strict` complains — the package may ship types)
- Test: `tests/core/channels/satellite/test_bridge.py` (+ helper `tests/core/channels/satellite/fake_satellite.py`)

**Interfaces:**
- Consumes: `SatelliteEntry` (Task 6), `UtteranceCollector`/`CollectorEvent`/`default_collector_factory` (Task 8), `wav_to_pcm` (Task 7).
- Produces:
  - `SatelliteConnection` with: `entry: SatelliteEntry`, `connected: bool` property, `async send_transcript(text: str)`, `async send_synthesize(text: str)`, `async send_error(text: str)`, `async play_wav(wav_bytes: bytes)`.
  - `UtteranceHandler = Callable[[SatelliteConnection, bytes], Awaitable[None]]` type alias.
  - `SatelliteBridge(entries: list[SatelliteEntry], handler: UtteranceHandler, *, collector_factory: Callable[[], UtteranceCollector] = default_collector_factory, reconnect_max_s: float = 60.0)` with `start() -> None`, `async stop() -> None`, `connections() -> list[SatelliteConnection]` (connected only), `async play_wav_all(wav: bytes) -> int`.

**Behavior (from the Protocol facts section):** connect → `Describe` → wait `Info` → `RunSatellite` → event loop. `RunPipeline` → fresh collector, active run. `AudioChunk` during run → `AudioChunkConverter(16000, 2, 1)` → `collector.feed`; `speech_start` → send `VoiceStarted`; `utterance` → send `VoiceStopped`, spawn `handler(conn, pcm)` as a task (read loop keeps draining trailing chunks), deactivate collector; `timeout` → `send_transcript("")` to re-arm. `Ping` → `Pong`. Own `Ping` every 10 s; read with 30 s `asyncio.wait_for` → timeout/EOF/error → reconnect with exponential backoff (1 s doubling, cap `reconnect_max_s`), send `PauseSatellite` best-effort on shutdown. `play_wav` holds a per-connection `asyncio.Lock` so a reply and an announcement never interleave: `AudioStart(rate, width, channels, timestamp=0)` → 1024-sample `AudioChunk`s → `AudioStop()`.

- [ ] **Step 1: Write the fake satellite test helper**

`tests/core/channels/satellite/fake_satellite.py`:

```python
"""In-process fake wyoming-satellite for bridge tests."""

from __future__ import annotations

import asyncio

from wyoming.event import Event, async_read_event, async_write_event
from wyoming.info import Attribution, Describe, Info, Satellite


class FakeSatellite:
    """Accepts one connection; records received events; scripts satellite events."""

    def __init__(self) -> None:
        self.received: list[Event] = []
        self.port = 0
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = asyncio.Event()
        self.run_satellite_seen = asyncio.Event()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_conn, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def wait_connected(self, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def send(self, event: Event) -> None:
        assert self._writer is not None
        await async_write_event(event, self._writer)

    async def wait_for(self, event_type: str, timeout: float = 5.0) -> Event:
        """Wait until an event of the given type has been received; return it."""

        async def _poll() -> Event:
            while True:
                for ev in self.received:
                    if ev.type == event_type:
                        return ev
                await asyncio.sleep(0.01)

        return await asyncio.wait_for(_poll(), timeout)

    async def _on_conn(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writer = writer
        self._connected.set()
        while (event := await async_read_event(reader)) is not None:
            self.received.append(event)
            if Describe.is_type(event.type):
                info = Info(
                    satellite=Satellite(
                        name="fake-sat",
                        attribution=Attribution(name="test", url=""),
                        installed=True,
                        description="fake",
                        version="1.0",
                    )
                )
                await async_write_event(info.event(), writer)
            elif event.type == "run-satellite":
                self.run_satellite_seen.set()
```

(If `Satellite`'s constructor differs — e.g. an `area` field or missing `description` — adjust to the installed wyoming version's dataclass; check with `python -c "from wyoming.info import Satellite; import inspect; print(inspect.signature(Satellite.__init__))"`.)

- [ ] **Step 2: Write the failing tests**

`tests/core/channels/satellite/test_bridge.py`:

```python
"""SatelliteBridge ↔ fake satellite integration tests."""

import asyncio
from collections import deque

import pytest
from wyoming.audio import AudioChunk
from wyoming.pipeline import PipelineStage, RunPipeline

from core.channels.satellite.audio import pcm_to_wav
from core.channels.satellite.bridge import SatelliteBridge, SatelliteConnection
from core.channels.satellite.config import SatelliteEntry
from core.channels.satellite.endpointing import UtteranceCollector

from .fake_satellite import FakeSatellite

FRAME = b"\x01\x00" * 512


def _scripted_collector(probs: list[float]) -> UtteranceCollector:
    q = deque(probs)
    return UtteranceCollector(vad=lambda _f: q.popleft() if q else 0.0)


@pytest.fixture
async def fake_sat() -> FakeSatellite:
    sat = FakeSatellite()
    await sat.start()
    yield sat
    await sat.stop()


def _entry(sat: FakeSatellite) -> SatelliteEntry:
    return SatelliteEntry(name="kitchen", host="127.0.0.1", port=sat.port, area="Kitchen")


async def test_handshake_and_arm(fake_sat: FakeSatellite) -> None:
    bridge = SatelliteBridge([_entry(fake_sat)], handler=lambda c, p: asyncio.sleep(0))
    bridge.start()
    try:
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
        assert [e.type for e in fake_sat.received][:2] == ["describe", "run-satellite"]
        assert len(bridge.connections()) == 1
    finally:
        await bridge.stop()


async def test_wake_utterance_flow_invokes_handler(fake_sat: FakeSatellite) -> None:
    got: asyncio.Future[tuple[SatelliteConnection, bytes]] = asyncio.get_event_loop().create_future()

    async def handler(conn: SatelliteConnection, pcm: bytes) -> None:
        got.set_result((conn, pcm))
        await conn.send_transcript("turn off the lights")

    # 5 speech frames then silence until the 800ms end fires
    bridge = SatelliteBridge(
        [_entry(fake_sat)],
        handler=handler,
        collector_factory=lambda: _scripted_collector([0.9] * 5 + [0.0] * 100),
    )
    bridge.start()
    try:
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
        await fake_sat.send(
            RunPipeline(start_stage=PipelineStage.ASR, end_stage=PipelineStage.TTS).event()
        )
        for _ in range(40):
            await fake_sat.send(AudioChunk(rate=16000, width=2, channels=1, audio=FRAME).event())
        conn, pcm = await asyncio.wait_for(got, 5.0)
        assert conn.entry.name == "kitchen"
        assert len(pcm) >= 5 * len(FRAME)
        await fake_sat.wait_for("voice-started")
        await fake_sat.wait_for("voice-stopped")
        transcript = await fake_sat.wait_for("transcript")
        assert transcript.data["text"] == "turn off the lights"
    finally:
        await bridge.stop()


async def test_play_wav_streams_audio(fake_sat: FakeSatellite) -> None:
    bridge = SatelliteBridge([_entry(fake_sat)], handler=lambda c, p: asyncio.sleep(0))
    bridge.start()
    try:
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
        wav = pcm_to_wav(b"\x00\x01" * 4096, rate=22050)
        delivered = await bridge.play_wav_all(wav)
        assert delivered == 1
        start = await fake_sat.wait_for("audio-start")
        assert start.data["rate"] == 22050
        await fake_sat.wait_for("audio-stop")
    finally:
        await bridge.stop()


async def test_reconnects_after_disconnect(fake_sat: FakeSatellite) -> None:
    bridge = SatelliteBridge([_entry(fake_sat)], handler=lambda c, p: asyncio.sleep(0))
    bridge.start()
    try:
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
        fake_sat.run_satellite_seen.clear()
        fake_sat.received.clear()
        assert fake_sat._writer is not None
        fake_sat._writer.close()  # drop the connection
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 10.0)  # re-handshake
    finally:
        await bridge.stop()


async def test_ping_answered_with_pong(fake_sat: FakeSatellite) -> None:
    from wyoming.ping import Ping

    bridge = SatelliteBridge([_entry(fake_sat)], handler=lambda c, p: asyncio.sleep(0))
    bridge.start()
    try:
        await asyncio.wait_for(fake_sat.run_satellite_seen.wait(), 5.0)
        await fake_sat.send(Ping(text="x").event())
        pong = await fake_sat.wait_for("pong")
        assert pong.data.get("text") == "x"
    finally:
        await bridge.stop()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/core/channels/satellite/test_bridge.py -v`
Expected: FAIL — `core.channels.satellite.bridge` not found. (First add `wyoming>=1.10` to base deps in `pyproject.toml` and `uv pip install -e ".[dev,memory,voice,integrations]"` so the test imports resolve.)

- [ ] **Step 4: Implement `core/channels/satellite/bridge.py`**

```python
"""Wyoming bridge — persistent connections from Alfred to each satellite.

Wyoming inverts the usual direction: satellites LISTEN on :10700 and this
bridge connects out to them. See docs/voice-satellites.md for the event flow.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStart, AudioStop
from wyoming.asr import Transcript
from wyoming.client import AsyncTcpClient
from wyoming.error import Error
from wyoming.info import Describe, Info
from wyoming.ping import Ping, Pong
from wyoming.pipeline import RunPipeline
from wyoming.satellite import PauseSatellite, RunSatellite
from wyoming.tts import Synthesize
from wyoming.vad import VoiceStarted, VoiceStopped

from core.channels.satellite.audio import wav_to_pcm
from core.channels.satellite.endpointing import (
    CollectorEvent,
    UtteranceCollector,
    default_collector_factory,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from wyoming.event import Event

    from core.channels.satellite.config import SatelliteEntry

    UtteranceHandler = Callable[["SatelliteConnection", bytes], Awaitable[None]]

_SAMPLES_PER_CHUNK = 1024
_PING_INTERVAL_S = 10.0
_READ_TIMEOUT_S = 30.0


class SatelliteConnection:
    """One satellite: connect/handshake/event loop with reconnect."""

    def __init__(
        self,
        entry: SatelliteEntry,
        handler: UtteranceHandler,
        collector_factory: Callable[[], UtteranceCollector],
        reconnect_max_s: float,
    ) -> None:
        self.entry = entry
        self._handler = handler
        self._collector_factory = collector_factory
        self._reconnect_max_s = reconnect_max_s
        self._client: AsyncTcpClient | None = None
        self._connected = False
        self._send_lock = asyncio.Lock()
        self._audio_lock = asyncio.Lock()
        self._collector: UtteranceCollector | None = None
        self._converter = AudioChunkConverter(rate=16000, width=2, channels=1)
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def connected(self) -> bool:
        return self._connected

    async def run(self) -> None:
        """Reconnect-forever loop. Cancelled on bridge shutdown."""
        backoff = 1.0
        while True:
            try:
                await self._run_once()
                backoff = 1.0
            except asyncio.CancelledError:
                await self._graceful_close()
                raise
            except Exception as exc:
                logger.warning(
                    "Satellite '{}' connection lost ({}: {}) — retrying in {:.0f}s",
                    self.entry.name,
                    type(exc).__name__,
                    exc,
                    backoff,
                )
            self._connected = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._reconnect_max_s)

    async def _run_once(self) -> None:
        client = AsyncTcpClient(self.entry.host, self.entry.port)
        await client.connect()
        self._client = client
        try:
            await self._send(Describe().event())
            while True:
                event = await asyncio.wait_for(client.read_event(), _READ_TIMEOUT_S)
                if event is None:
                    raise ConnectionResetError("satellite closed connection")
                if Info.is_type(event.type):
                    break
            await self._send(RunSatellite().event())
            self._connected = True
            logger.info("Satellite '{}' connected ({})", self.entry.name, self.entry.host)

            ping_task = asyncio.create_task(self._ping_loop())
            try:
                while True:
                    event = await asyncio.wait_for(client.read_event(), _READ_TIMEOUT_S)
                    if event is None:
                        raise ConnectionResetError("satellite closed connection")
                    await self._handle_event(event)
            finally:
                ping_task.cancel()
        finally:
            self._connected = False
            await client.disconnect()
            self._client = None

    async def _handle_event(self, event: Event) -> None:
        if AudioChunk.is_type(event.type):
            if self._collector is None:
                return  # trailing audio after utterance end / no active run
            chunk = self._converter.convert(AudioChunk.from_event(event))
            for coll_event in self._collector.feed(chunk.audio):
                await self._on_collector_event(coll_event)
        elif RunPipeline.is_type(event.type):
            logger.debug("Satellite '{}': pipeline run started", self.entry.name)
            self._collector = self._collector_factory()
        elif Ping.is_type(event.type):
            await self._send(Pong(text=Ping.from_event(event).text).event())
        elif event.type in ("detection", "played", "pong", "voice-started", "voice-stopped"):
            logger.debug("Satellite '{}': {}", self.entry.name, event.type)
        else:
            logger.debug("Satellite '{}': ignoring event {}", self.entry.name, event.type)

    async def _on_collector_event(self, coll_event: CollectorEvent) -> None:
        if coll_event.kind == "speech_start":
            await self._send(VoiceStarted().event())
        elif coll_event.kind == "utterance":
            await self._send(VoiceStopped().event())
            self._collector = None
            assert coll_event.pcm is not None
            task = asyncio.create_task(self._run_handler(coll_event.pcm))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif coll_event.kind == "timeout":
            logger.debug("Satellite '{}': no speech — re-arming", self.entry.name)
            self._collector = None
            await self.send_transcript("")

    async def _run_handler(self, pcm: bytes) -> None:
        try:
            await self._handler(self, pcm)
        except Exception as exc:
            logger.error("Satellite '{}' pipeline failed: {}", self.entry.name, exc)
            await self.send_error(str(exc))

    async def _ping_loop(self) -> None:
        while True:
            await asyncio.sleep(_PING_INTERVAL_S)
            await self._send(Ping().event())

    async def _send(self, event: Event) -> None:
        if self._client is None:
            raise ConnectionResetError("not connected")
        async with self._send_lock:
            await self._client.write_event(event)

    async def _graceful_close(self) -> None:
        try:
            if self._client is not None:
                await self._send(PauseSatellite().event())
        except Exception:  # noqa: BLE001 — best-effort during shutdown
            pass

    # -- public send API (used by the pipeline handler + announcements) --

    async def send_transcript(self, text: str) -> None:
        """End-of-command signal: satellite stops streaming and re-arms."""
        await self._send(Transcript(text=text).event())

    async def send_synthesize(self, text: str) -> None:
        """FYI event before reply audio (satellite may show/log it)."""
        await self._send(Synthesize(text=text).event())

    async def send_error(self, text: str) -> None:
        """Error event — satellite stops streaming and plays error feedback."""
        try:
            await self._send(Error(text=text).event())
        except Exception:  # noqa: BLE001 — connection may already be gone
            pass

    async def play_wav(self, wav_bytes: bytes) -> None:
        """Stream a WAV to the satellite speaker (reply or announcement)."""
        pcm, rate, width, channels = wav_to_pcm(wav_bytes)
        bytes_per_chunk = _SAMPLES_PER_CHUNK * width * channels
        async with self._audio_lock:
            await self._send(AudioStart(rate=rate, width=width, channels=channels).event())
            timestamp = 0
            for i in range(0, len(pcm), bytes_per_chunk):
                chunk = pcm[i : i + bytes_per_chunk]
                await self._send(
                    AudioChunk(
                        rate=rate, width=width, channels=channels, audio=chunk,
                        timestamp=timestamp,
                    ).event()
                )
                timestamp += (len(chunk) // (width * channels)) * 1000 // rate
            await self._send(AudioStop(timestamp=timestamp).event())


class SatelliteBridge:
    """Owns one SatelliteConnection task per configured satellite."""

    def __init__(
        self,
        entries: list[SatelliteEntry],
        handler: UtteranceHandler,
        *,
        collector_factory: Callable[[], UtteranceCollector] = default_collector_factory,
        reconnect_max_s: float = 60.0,
    ) -> None:
        self._connections = [
            SatelliteConnection(entry, handler, collector_factory, reconnect_max_s)
            for entry in entries
        ]
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        """Spawn one supervisor task per satellite."""
        self._tasks = [asyncio.create_task(conn.run()) for conn in self._connections]
        logger.info("Satellite bridge started ({} satellites)", len(self._connections))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def connections(self) -> list[SatelliteConnection]:
        """Currently-connected satellites."""
        return [c for c in self._connections if c.connected]

    async def play_wav_all(self, wav: bytes) -> int:
        """Play a WAV on every online satellite. Returns delivery count."""
        delivered = 0
        for conn in self.connections():
            try:
                await conn.play_wav(wav)
                delivered += 1
            except Exception as exc:
                logger.warning(
                    "Announcement to satellite '{}' failed: {}", conn.entry.name, exc
                )
        return delivered
```

If `mypy --strict` reports missing stubs for `wyoming`, add `wyoming.*` to the mypy overrides list.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/core/channels/satellite/test_bridge.py -v`
Expected: PASS (5 tests). These are real-socket tests — if flaky on timing, raise the `wait_for` timeouts, never add sleeps.

- [ ] **Step 6: Commit**

```bash
git add core/channels/satellite/bridge.py tests/core/channels/satellite pyproject.toml uv.lock
git commit -m "feat(satellite): Wyoming bridge with reconnect, endpointing, and audio playback"
```

---

### Task 12: SatellitePipeline (utterance → STT → Conscious → TTS)

**Files:**
- Create: `core/channels/satellite/pipeline.py`
- Test: `tests/core/channels/satellite/test_pipeline.py`

**Interfaces:**
- Consumes: `publish_and_wait` (Task 2), `pcm_to_wav` (Task 7), `SpeakerID.identify` (Task 9), `SatelliteConnection` send API (Task 11), `_aget_stt`/`_aget_tts` (Task 3), `UserRequest` fields (Task 1).
- Produces: `SatellitePipeline(redis, *, get_stt, get_tts, speaker_id=None, request_timeout=60.0)` — an `UtteranceHandler` (async callable `(conn, pcm) -> None`). `get_stt`/`get_tts` are `Callable[[], Awaitable[Any]]` (pass `_aget_stt`/`_aget_tts` in production). Session ID is stable per device: `f"sat-{conn.entry.name}"` (per-room 30-min conversation context via the existing SessionManager).

**Flow:** STT → `send_transcript(text)` *immediately* (satellite stops streaming/re-arms before the slow LLM round-trip) → empty text: stop → speaker ID (enrolled match sets `identity_claim`+`identity_confidence`; unknown falls back to `"sir"`/None local-claim) → `UserRequest(source="satellite", channel="satellite", content_type="audio", content=text, device_id=name, area=area)` → `publish_and_wait` → `send_synthesize(resp.text)` → TTS → `play_wav`. STT unavailable → `send_error` + `send_transcript("")`. TTS unavailable → log, skip audio (text still reached the bus/session). SpeakerID failure → log warning, proceed with local-claim.

- [ ] **Step 1: Write the failing test**

```python
"""SatellitePipeline — utterance orchestration with all I/O faked."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from bus.schemas.events import AlfredResponse
from core.channels.satellite.config import SatelliteEntry
from core.channels.satellite.pipeline import SatellitePipeline
from core.voice.speaker_id import SpeakerMatch

PCM = b"\x01\x00" * 16000


def _conn(name: str = "kitchen", area: str | None = "Kitchen") -> AsyncMock:
    conn = AsyncMock()
    conn.entry = SatelliteEntry(name=name, host="h", area=area)
    return conn


def _stt(text: str = "turn off the lights") -> MagicMock:
    stt = MagicMock()
    stt.transcribe = MagicMock(return_value=text)
    return stt


def _tts() -> MagicMock:
    tts = MagicMock()
    tts.synthesize = MagicMock(return_value=b"RIFFfakewav")
    return tts


def _pipeline(
    stt: Any, tts: Any, speaker_id: Any = None, response_text: str = "Done, sir."
) -> tuple[SatellitePipeline, AsyncMock]:
    publish = AsyncMock(
        return_value=AlfredResponse(
            source="conscious-engine", channel="satellite",
            session_id="sat-kitchen", text=response_text,
        )
    )

    async def get_stt() -> Any:
        return stt

    async def get_tts() -> Any:
        return tts

    pipeline = SatellitePipeline(
        AsyncMock(), get_stt=get_stt, get_tts=get_tts, speaker_id=speaker_id
    )
    return pipeline, publish


async def test_happy_path_sends_transcript_then_audio() -> None:
    conn = _conn()
    pipeline, publish = _pipeline(_stt(), _tts())
    with patch("core.channels.satellite.pipeline.publish_and_wait", publish):
        await pipeline(conn, PCM)

    conn.send_transcript.assert_awaited_once_with("turn off the lights")
    conn.send_synthesize.assert_awaited_once_with("Done, sir.")
    conn.play_wav.assert_awaited_once_with(b"RIFFfakewav")

    request = publish.call_args.args[1]
    assert request.channel == "satellite"
    assert request.device_id == "kitchen"
    assert request.area == "Kitchen"
    assert request.session_id == "sat-kitchen"
    assert request.content_type == "audio"
    assert request.identity_claim == "sir"
    assert request.identity_confidence is None


async def test_speaker_match_sets_identity_confidence() -> None:
    speaker_id = AsyncMock()
    speaker_id.identify = AsyncMock(
        return_value=SpeakerMatch(identity="sir", confidence=0.88, enrolled=True)
    )
    pipeline, publish = _pipeline(_stt(), _tts(), speaker_id=speaker_id)
    with patch("core.channels.satellite.pipeline.publish_and_wait", publish):
        await pipeline(_conn(), PCM)
    request = publish.call_args.args[1]
    assert request.identity_confidence == 0.88


async def test_empty_transcript_stops_early() -> None:
    conn = _conn()
    pipeline, publish = _pipeline(_stt(text="  "), _tts())
    with patch("core.channels.satellite.pipeline.publish_and_wait", publish):
        await pipeline(conn, PCM)
    conn.send_transcript.assert_awaited_once_with("")
    publish.assert_not_awaited()
    conn.play_wav.assert_not_awaited()


async def test_stt_unavailable_sends_error() -> None:
    conn = _conn()
    pipeline, publish = _pipeline(None, _tts())
    with patch("core.channels.satellite.pipeline.publish_and_wait", publish):
        await pipeline(conn, PCM)
    conn.send_error.assert_awaited_once()
    conn.send_transcript.assert_awaited_once_with("")
    publish.assert_not_awaited()


async def test_speaker_id_failure_is_nonfatal() -> None:
    speaker_id = AsyncMock()
    speaker_id.identify = AsyncMock(side_effect=RuntimeError("model exploded"))
    conn = _conn()
    pipeline, publish = _pipeline(_stt(), _tts(), speaker_id=speaker_id)
    with patch("core.channels.satellite.pipeline.publish_and_wait", publish):
        await pipeline(conn, PCM)
    request = publish.call_args.args[1]
    assert request.identity_claim == "sir"
    assert request.identity_confidence is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/channels/satellite/test_pipeline.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `core/channels/satellite/pipeline.py`**

```python
"""Satellite utterance pipeline — STT → Conscious Engine → TTS reply."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

from bus.schemas.events import UserRequest
from core.channels.request_bus import publish_and_wait
from core.channels.satellite.audio import pcm_to_wav

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.channels.satellite.bridge import SatelliteConnection


class SatellitePipeline:
    """UtteranceHandler implementation: full voice loop for one utterance."""

    def __init__(
        self,
        redis: Any,
        *,
        get_stt: Callable[[], Awaitable[Any]],
        get_tts: Callable[[], Awaitable[Any]],
        speaker_id: Any = None,
        request_timeout: float = 60.0,
    ) -> None:
        self._redis = redis
        self._get_stt = get_stt
        self._get_tts = get_tts
        self._speaker_id = speaker_id
        self._request_timeout = request_timeout

    async def __call__(self, conn: SatelliteConnection, pcm: bytes) -> None:
        entry = conn.entry

        stt = await self._get_stt()
        if stt is None:
            logger.error("Satellite '{}': STT unavailable", entry.name)
            await conn.send_error("Voice processing unavailable")
            await conn.send_transcript("")  # re-arm the satellite
            return

        wav = pcm_to_wav(pcm)
        text = (await asyncio.to_thread(stt.transcribe, wav, audio_format="wav")).strip()
        # Transcript FIRST: it stops mic streaming and re-arms the satellite
        # before the slow LLM round-trip.
        await conn.send_transcript(text)
        if not text:
            logger.debug("Satellite '{}': empty transcript", entry.name)
            return
        logger.info("Satellite '{}' heard: {}", entry.name, text)

        identity_claim: str = "sir"
        identity_confidence: float | None = None
        if self._speaker_id is not None:
            try:
                match = await self._speaker_id.identify(pcm)
                if match.enrolled:
                    identity_claim = match.identity
                    identity_confidence = match.confidence
            except Exception as exc:
                logger.warning("Satellite '{}': speaker ID failed: {}", entry.name, exc)

        session_id = f"sat-{entry.name}"
        request = UserRequest(
            source="satellite",
            channel="satellite",
            session_id=session_id,
            identity_claim=identity_claim,
            identity_confidence=identity_confidence,
            authenticated=False,
            content_type="audio",
            content=text,
            device_id=entry.name,
            area=entry.area,
        )
        response = await publish_and_wait(
            self._redis, request, session_id, timeout=self._request_timeout
        )

        await conn.send_synthesize(response.text)
        tts = await self._get_tts()
        if tts is None:
            logger.warning("Satellite '{}': TTS unavailable — reply not spoken", entry.name)
            return
        wav_out = await asyncio.to_thread(tts.synthesize, response.text)
        await conn.play_wav(wav_out)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/core/channels/satellite -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add core/channels/satellite/pipeline.py tests/core/channels/satellite/test_pipeline.py
git commit -m "feat(satellite): utterance pipeline (STT -> Conscious -> TTS reply)"
```

---

### Task 13: `SatelliteChannelAdapter` (spoken announcements)

**Files:**
- Create: `core/notifications/adapters/satellite.py`
- Test: `tests/core/notifications/test_satellite_adapter.py`

**Interfaces:**
- Consumes: `SatelliteBridge.play_wav_all` (Task 11), `ChannelAdapter`/`ChannelRegistry` (existing).
- Produces: `SatelliteChannelAdapter(get_bridge: Callable[[], Any | None], get_tts: Callable[[], Any | None])`, `name="satellite"`, `supported_urgencies={Urgency.URGENT}` (mirrors the WebSocket adapter's spoken policy).

- [ ] **Step 1: Write the failing test**

```python
"""SatelliteChannelAdapter — spoken URGENT announcements."""

from unittest.mock import AsyncMock, MagicMock

from core.notifications.adapters.satellite import SatelliteChannelAdapter
from core.notifications.schema import Notification, Urgency


def _notification(urgency: Urgency = Urgency.URGENT) -> Notification:
    return Notification(title="Smoke", body="Kitchen smoke detected", urgency=urgency, source="t")


async def test_urgent_is_synthesized_and_played_everywhere() -> None:
    bridge = AsyncMock()
    bridge.play_wav_all = AsyncMock(return_value=2)
    tts = MagicMock()
    tts.synthesize = MagicMock(return_value=b"RIFFwav")

    adapter = SatelliteChannelAdapter(get_bridge=lambda: bridge, get_tts=lambda: tts)
    await adapter.deliver(_notification())

    tts.synthesize.assert_called_once_with("Smoke: Kitchen smoke detected")
    bridge.play_wav_all.assert_awaited_once_with(b"RIFFwav")


async def test_supports_urgent_only() -> None:
    adapter = SatelliteChannelAdapter(get_bridge=lambda: None, get_tts=lambda: None)
    assert adapter.supports_urgency(Urgency.URGENT)
    assert not adapter.supports_urgency(Urgency.IMPORTANT)
    assert not adapter.supports_urgency(Urgency.INFORMATIONAL)


async def test_no_bridge_or_tts_is_noop() -> None:
    adapter = SatelliteChannelAdapter(get_bridge=lambda: None, get_tts=lambda: None)
    await adapter.deliver(_notification())  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/notifications/test_satellite_adapter.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `core/notifications/adapters/satellite.py`**

```python
"""Satellite channel adapter — speaks URGENT notifications on all online satellites."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency

if TYPE_CHECKING:
    from collections.abc import Callable


@ChannelRegistry.register()
class SatelliteChannelAdapter(ChannelAdapter):
    """Piper-synthesized speech pushed over the satellite bridge connections."""

    name: ClassVar[str] = "satellite"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.URGENT}

    def __init__(
        self,
        get_bridge: Callable[[], Any | None],
        get_tts: Callable[[], Any | None],
    ) -> None:
        self._get_bridge = get_bridge
        self._get_tts = get_tts

    async def deliver(self, notification: Notification) -> None:
        """Speak the notification on every online satellite."""
        bridge = self._get_bridge()
        tts = self._get_tts()
        if bridge is None or tts is None:
            logger.debug("SatelliteChannelAdapter: bridge/TTS unavailable, skipping")
            return
        text = f"{notification.title}: {notification.body}"
        try:
            wav = await asyncio.to_thread(tts.synthesize, text)
        except Exception as exc:
            logger.warning("SatelliteChannelAdapter: TTS failed: {}", exc)
            return
        delivered = await bridge.play_wav_all(wav)
        logger.info("Announcement delivered to {} satellite(s)", delivered)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/core/notifications -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add core/notifications/adapters/satellite.py tests/core/notifications/test_satellite_adapter.py
git commit -m "feat(notifications): satellite announcement adapter (URGENT spoken)"
```

---

### Task 14: Lifespan wiring in the channels process

**Files:**
- Modify: `core/channels/web_server.py:263-327` (`_lifespan`)
- Test: `tests/core/channels/satellite/test_wiring.py`

**Interfaces:**
- Consumes: everything above.
- Produces: on channels startup — `load_satellites()`; when non-empty: `SatellitePipeline` (with `_aget_stt`/`_aget_tts`, `aget_speaker_id(pool)`), `SatelliteBridge(entries, pipeline)`, `bridge.start()`, `app.state.satellite_bridge = bridge`, and `ChannelRegistry.set_instance("satellite", SatelliteChannelAdapter(...))` with getters reading `app.state`/`_get_tts()`. Clean shutdown via `await bridge.stop()`.

- [ ] **Step 1: Write the failing test**

```python
"""Satellite bridge lifespan wiring."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from core.channels.web_server import create_app
from core.notifications.channels import ChannelRegistry


def test_bridge_started_when_satellites_configured(tmp_path: Path) -> None:
    cfg = tmp_path / "satellites.yaml"
    cfg.write_text("satellites:\n  - name: kitchen\n    host: 127.0.0.1\n    area: Kitchen\n")

    with (
        patch.dict("os.environ", {"SATELLITES_CONFIG": str(cfg)}),
        patch("core.channels.web_server.SatelliteBridge") as bridge_cls,
    ):
        bridge_cls.return_value.stop = AsyncMock()
        app = create_app(redis_url="redis://localhost:6379")
        with TestClient(app):
            bridge_cls.assert_called_once()
            entries = bridge_cls.call_args.args[0]
            assert entries[0].name == "kitchen"
            bridge_cls.return_value.start.assert_called_once()
            assert ChannelRegistry.get_instance("satellite") is not None
        bridge_cls.return_value.stop.assert_awaited_once()


def test_no_bridge_without_config(tmp_path: Path) -> None:
    with (
        patch.dict("os.environ", {"SATELLITES_CONFIG": str(tmp_path / "missing.yaml")}),
        patch("core.channels.web_server.SatelliteBridge") as bridge_cls,
    ):
        app = create_app(redis_url="redis://localhost:6379")
        with TestClient(app):
            bridge_cls.assert_not_called()
```

(Note: `ChannelRegistry` is process-global — if the registry asserts collide with other tests, snapshot/restore `ChannelRegistry._instances` in a fixture, or use `ChannelRegistry.reset()` + re-import; follow whatever `tests/core/notifications/test_channel_registry.py` already does.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/channels/satellite/test_wiring.py -v`
Expected: FAIL — `SatelliteBridge` not importable from web_server.

- [ ] **Step 3: Implement**

In `web_server.py` add imports:

```python
from core.channels.satellite.bridge import SatelliteBridge
from core.channels.satellite.config import load_satellites
from core.channels.satellite.pipeline import SatellitePipeline
from core.channels.voice_models import aget_speaker_id
from core.notifications.adapters.satellite import SatelliteChannelAdapter
from core.notifications.channels import ChannelRegistry
```

In `_lifespan`, after the `delivery_task` is created:

```python
    # Satellite bridge — physical voice devices (see docs/voice-satellites.md)
    satellite_bridge: SatelliteBridge | None = None
    satellites = load_satellites()
    if satellites:
        speaker_id = await aget_speaker_id(pool)
        pipeline = SatellitePipeline(
            pool, get_stt=_aget_stt, get_tts=_aget_tts, speaker_id=speaker_id
        )
        satellite_bridge = SatelliteBridge(satellites, pipeline)
        satellite_bridge.start()
        app.state.satellite_bridge = satellite_bridge
        ChannelRegistry.set_instance(
            "satellite",
            SatelliteChannelAdapter(
                get_bridge=lambda: getattr(app.state, "satellite_bridge", None),
                get_tts=_get_tts,
            ),
        )
```

and in the shutdown section (after `warmup_task.cancel()`):

```python
    if satellite_bridge is not None:
        await satellite_bridge.stop()
```

- [ ] **Step 4: Run the whole channels suite**

Run: `.venv/bin/python -m pytest tests/core/channels -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add core/channels/web_server.py tests/core/channels/satellite/test_wiring.py
git commit -m "feat(channels): wire satellite bridge + announcement adapter into lifespan"
```

---

### Task 15: Frontend — voice enrollment card in Settings

**Files:**
- Create: `web/src/pages/VoiceEnrollmentCard.tsx`
- Modify: `web/src/pages/SettingsPage.tsx` (render the card above the integrations grid)
- Test: `web/src/pages/VoiceEnrollmentCard.test.tsx`

**Interfaces:**
- Consumes: `POST /api/voice/enroll` (Task 10), existing `VoiceButton` (`web/src/chat/VoiceButton.tsx`, prop `onAudio: (dataUrl: string) => void`), `api` helper (`@/lib/api`), shadcn `Card`/`Button`.

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { VoiceEnrollmentCard } from "./VoiceEnrollmentCard";

vi.mock("@/chat/VoiceButton", () => ({
  VoiceButton: ({ onAudio }: { onAudio: (d: string) => void }) => (
    <button data-testid="mock-mic" onClick={() => onAudio("data:audio/webm;base64,QUJD")}>
      mic
    </button>
  ),
}));

const apiMock = vi.fn().mockResolvedValue({ status: "enrolled", identity: "sir" });
vi.mock("@/lib/api", () => ({ api: (...args: unknown[]) => apiMock(...args) }));

describe("VoiceEnrollmentCard", () => {
  it("collects three samples then enrolls", async () => {
    render(<VoiceEnrollmentCard />);
    expect(screen.getByText(/0 \/ 3/)).toBeInTheDocument();

    const mic = screen.getByTestId("mock-mic");
    mic.click();
    expect(await screen.findByText(/1 \/ 3/)).toBeInTheDocument();
    mic.click();
    mic.click();

    expect(await screen.findByText(/enrolled/i)).toBeInTheDocument();
    expect(apiMock).toHaveBeenCalledWith(
      "/api/voice/enroll",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          identity: "sir",
          samples: [
            "data:audio/webm;base64,QUJD",
            "data:audio/webm;base64,QUJD",
            "data:audio/webm;base64,QUJD",
          ],
        }),
      }),
    );
  });
});
```

(Check `web/src/lib/api.ts` for the actual `api(path, init?)` signature and match the assertion to it — e.g. it may take `{ method, body }` or a JSON param.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm run test -- VoiceEnrollmentCard`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `web/src/pages/VoiceEnrollmentCard.tsx`**

```tsx
import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { VoiceButton } from "@/chat/VoiceButton";
import { api } from "@/lib/api";

const SAMPLES_NEEDED = 3;
const PROMPTS = [
  "Alfred, what's on my calendar for tomorrow morning?",
  "Turn off the lights in the living room, please.",
  "Remind me to take the bread out of the oven.",
];

type Status = "recording" | "submitting" | "enrolled" | "error";

export function VoiceEnrollmentCard() {
  const [samples, setSamples] = useState<string[]>([]);
  const [status, setStatus] = useState<Status>("recording");

  const onAudio = (dataUrl: string) => {
    const next = [...samples, dataUrl];
    setSamples(next);
    if (next.length >= SAMPLES_NEEDED) void submit(next);
  };

  const submit = async (all: string[]) => {
    setStatus("submitting");
    try {
      await api("/api/voice/enroll", {
        method: "POST",
        body: JSON.stringify({ identity: "sir", samples: all }),
      });
      setStatus("enrolled");
    } catch {
      setStatus("error");
      setSamples([]);
    }
  };

  return (
    <Card className="bg-card">
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle className="font-mono text-xs tracking-widest">VOICE ENROLLMENT</CardTitle>
        <span className="font-mono text-xs text-muted-foreground">
          {samples.length} / {SAMPLES_NEEDED}
        </span>
      </CardHeader>
      <CardContent className="space-y-3 font-mono text-xs text-muted-foreground">
        {status === "enrolled" ? (
          <p className="text-good">Voiceprint enrolled. Satellites will recognize your voice.</p>
        ) : (
          <>
            <p>
              Record {SAMPLES_NEEDED} samples so Alfred can recognize your voice on satellites.
              Read aloud: &ldquo;{PROMPTS[samples.length] ?? PROMPTS[0]}&rdquo;
            </p>
            <div className="flex items-center gap-2">
              <VoiceButton onAudio={onAudio} />
              {status === "submitting" && <span>Enrolling…</span>}
              {status === "error" && <span className="text-bad">Enrollment failed — retry.</span>}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
```

In `SettingsPage.tsx`, import and render `<VoiceEnrollmentCard />` between the SESSION card and the integrations grid. Match existing class conventions (`text-good`/`text-bad` — verify these utility names exist in the codebase; if not, use whatever the SPA uses for success/error text, e.g. check `IntegrationCard.tsx`).

- [ ] **Step 4: Run frontend checks**

Run: `cd web && npm run lint && npm run test && npm run build`
Expected: all PASS (build emits `web/dist/`).

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/VoiceEnrollmentCard.tsx web/src/pages/VoiceEnrollmentCard.test.tsx web/src/pages/SettingsPage.tsx
git commit -m "feat(web): voice enrollment card in settings"
```

---

### Task 16: Documentation + spec amendment

**Files:**
- Create: `docs/voice-satellites.md`
- Modify: `docs/architecture.md` (add satellite bridge to the system diagram)
- Modify: `CLAUDE.md` (key paths + gotchas)
- Modify: `docs/superpowers/specs/2026-07-15-voice-satellite-design.md` (two research-driven corrections)

- [ ] **Step 1: Write `docs/voice-satellites.md`** covering (follow the depth of `docs/event-bus.md`):
  - Overview + the two-halves architecture (this repo = bridge; `alfred-satellite` repo = device)
  - Mermaid sequence diagram of the voice loop (wake → Detection/RunPipeline → audio-chunks → VAD endpoint → Transcript → UserRequest bus round-trip → Synthesize → audio stream → Played)
  - Config: `config/satellites.yaml` format, `SATELLITES_CONFIG`, `SPEAKER_ID_THRESHOLD`
  - Announcements: URGENT-only, broadcast policy, adapter registration
  - Speaker ID: enrollment flow, Redis storage (`alfred:identity:voiceprint`), threshold semantics
  - Operational notes: satellite offline behavior (backoff), keepalive, dev-mode fake satellite on macOS (pointer to the alfred-satellite repo docs)
- [ ] **Step 2: Update `docs/architecture.md`** — add `Satellites[Voice Satellites<br/>wyoming-satellite] <-->|Wyoming TCP| WebChannel` to the mermaid graph.
- [ ] **Step 3: Update `CLAUDE.md`**:
  - Key paths: `core/channels/satellite/` — Wyoming bridge (config, endpointing, bridge, pipeline); `core/channels/request_bus.py`; `core/channels/voice_models.py`; `core/voice/speaker_id.py` (now real).
  - Gotchas: "Wyoming satellites stop mic streaming only on `Transcript`/`Error` — always send `Transcript` even for empty/failed runs, or the satellite streams forever"; "Announcements are bare `AudioStart/Chunk/Stop` streams — no announce event exists"; "`pysilero-vad` frames are exactly 1024 bytes (512 samples @16 kHz)"; "ECAPA cosine same-speaker ≈ 0.4–0.7 — `SPEAKER_ID_THRESHOLD` defaults to 0.45, not 0.7".
- [ ] **Step 4: Amend the spec** (`2026-07-15-voice-satellite-design.md`):
  - §4.5: threshold "~0.7" → "cosine threshold 0.45 default (`SPEAKER_ID_THRESHOLD`); ECAPA same-speaker scores run 0.4–0.7, so 0.7 would reject genuine matches. Match confidence maps to `0.7 + (score − threshold) × 0.5`, capped at 0.95."
  - §8: "silero-vad ONNX — base deps" → "`pysilero-vad` (zero-dep, rhasspy) — voice extra; `wyoming` — base deps."
- [ ] **Step 5: Commit**

```bash
git add docs/voice-satellites.md docs/architecture.md CLAUDE.md docs/superpowers/specs/2026-07-15-voice-satellite-design.md
git commit -m "docs: voice satellite bridge documentation + spec amendments"
```

---

### Task 17: Full verification

- [ ] **Step 1:** `ruff check . --fix && ruff format .` → clean
- [ ] **Step 2:** `mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/` → clean
- [ ] **Step 3:** `.venv/bin/python -m pytest -x -q` → all pass
- [ ] **Step 4:** `cd web && npm run lint && npm run test && npm run build` → all pass
- [ ] **Step 5:** Live smoke (no hardware): follow `docs/voice-satellites.md` dev-mode section — run `wyoming-satellite` on the MacBook (see alfred-satellite repo plan Task 4), point `config/satellites.yaml` at `127.0.0.1`, start the runner, say "Hey Alfred…" (or use `--wake-word-name` with a stock model if `hey_alfred.tflite` isn't trained yet), verify a spoken reply and a `## Location` line in the conscious logs. Fix anything broken before proceeding.
- [ ] **Step 6:** Commit any fixes.

---

### Task 18: Architect review

- [ ] Dispatch `feature-dev:code-architect` on the full diff (`git diff master...HEAD`). Fix EVERY issue it raises. Also dispatch the project's `pillar-reviewer` agent (Five Pillars conformance) and `mypy-checker`. Commit fixes.

### Task 19: Simplify pass

- [ ] Run the `/simplify` skill on the branch diff. Apply every accepted simplification, re-run Task 17 verification, commit.

### Task 20: CLAUDE.md / memory hygiene

- [ ] Run `/claude-md-management:claude-md-improver` to audit CLAUDE.md files touched by this work; apply fixes, commit.

### Task 21: QA backlog generation

- [ ] Dispatch a `general-purpose` subagent: review the session diff, identify features that automated tests cannot fully verify (real-satellite wake accuracy, endpointing feel, announcement audibility, enrollment via real mic, speaker-ID accuracy), and create `docs/qa-backlog/*.md` files per the template in the user's global conventions. Commit.

### Task 22: PR

- [ ] Push the branch and open a PR against `master` titled `feat: voice satellite bridge (Wyoming) — room-aware voice, announcements, speaker ID`, body summarizing the spec link, architecture, and test counts. Use the superpowers:finishing-a-development-branch skill.

---

## Self-Review Notes (already applied)

- Spec §4.2 named the endpointing dep "silero-vad ONNX"; research showed the `silero-vad` package requires torch — replaced with rhasspy's zero-dep `pysilero-vad` (Task 8) and the spec amendment is Task 16.
- Spec §4.5's 0.7 cosine threshold corrected to 0.45 (Task 16) — grounded in ECAPA score distributions.
- The old `_publish_and_wait` hardcoded `channel="web_pwa"` in its timeout fallback; Task 2 fixes it to `request.channel` (pre-existing latent bug for iOS too).
- Session IDs are stable per satellite (`sat-<name>`) so room conversations get 30-min continuity via the existing SessionManager.
