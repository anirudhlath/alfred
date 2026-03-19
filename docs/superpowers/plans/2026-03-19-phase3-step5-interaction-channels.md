# Phase 3 Step 5: Interaction Channels — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the interaction channels (Signal bridge, Web PWA, voice pipeline) so users can talk to Alfred via voice, text chat, and Signal messaging. Each channel is an independent service that translates its native protocol into `UserRequest`/`AlfredResponse` on Redis.

**Architecture:** Signal bridge is a sovereign service in its own repo using `alfred-sdk`. Web PWA is a lightweight Svelte app served from Alfred's server with WebSocket for real-time voice + chat. Voice pipeline uses Whisper (whisper.cpp) for STT and Piper for TTS, both running locally.

**Tech Stack:** Python 3.13+, FastAPI + WebSocket, Svelte (PWA), signal-cli, whisper.cpp, Piper TTS, pytest

**Spec:** `docs/superpowers/specs/2026-03-19-alfred-expanded-vision-design.md` (Section 6, 16)

**Depends on:** Plan 2 (Conscious Engine) must be complete.

---

## File Structure

### New Files — Alfred Monorepo (`alfred/`)

| File | Responsibility |
|------|---------------|
| `core/voice/__init__.py` | Package init |
| `core/voice/stt.py` | `WhisperSTT` — speech-to-text via whisper.cpp |
| `core/voice/tts.py` | `PiperTTS` — text-to-speech via Piper |
| `core/voice/speaker_id.py` | `SpeakerID` — voice fingerprint (stub, full impl Phase 3.5) |
| `core/channels/__init__.py` | Package init |
| `core/channels/web_server.py` | FastAPI server: WebSocket (voice+chat) + static PWA |
| `core/channels/__main__.py` | Entry point (`python -m core.channels`) |
| `web/` | PWA source (Svelte or vanilla JS) |
| `web/index.html` | Main PWA page |
| `web/app.js` | Client-side voice + chat logic |
| `web/style.css` | Styling |
| `tests/core/voice/__init__.py` | Package init |
| `tests/core/voice/test_stt.py` | Whisper STT tests |
| `tests/core/voice/test_tts.py` | Piper TTS tests |
| `tests/core/channels/__init__.py` | Package init |
| `tests/core/channels/test_web_server.py` | WebSocket + REST tests |

### New Files — Signal Bridge (separate repo `signal-bridge/`)

| File | Responsibility |
|------|---------------|
| `signal-bridge/app/__init__.py` | Package init |
| `signal-bridge/app/bridge.py` | Signal bridge main loop |
| `signal-bridge/app/signal_client.py` | signal-cli wrapper |
| `signal-bridge/pyproject.toml` | Dependencies |
| `signal-bridge/Containerfile` | OCI container (includes JRE 17+) |

### Modified Files

| File | Change |
|------|--------|
| `pyproject.toml` | Add optional `[voice]` deps (whisper-cpp-python, piper-tts) |
| `runner/__main__.py` | Add channels service to supervised services |

---

## Task 1: Add Voice Dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add optional voice dependencies**

```toml
# In [project.optional-dependencies], add:
voice = [
    "faster-whisper>=1.0",
    "piper-tts>=1.2",
]
```

Note: `faster-whisper` is a lighter alternative to full `whisper.cpp` bindings, using CTranslate2. If this doesn't work well, switch to `whisper-cpp-python`. Both are local-only.

Also add mypy overrides:
```toml
[[tool.mypy.overrides]]
module = ["faster_whisper.*", "piper.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Install**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv pip install -e ".[dev,voice]"`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add faster-whisper + piper-tts for voice pipeline"
```

---

## Task 2: Whisper STT Wrapper

**Files:**
- Create: `core/voice/__init__.py`
- Create: `core/voice/stt.py`
- Create: `tests/core/voice/test_stt.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/voice/test_stt.py
"""Tests for Whisper STT wrapper."""

from __future__ import annotations

import pytest

from core.voice.stt import WhisperSTT


def test_stt_instantiation() -> None:
    """WhisperSTT can be created with default model."""
    # This test verifies the interface, not actual transcription
    # (which requires audio data and the model downloaded)
    stt = WhisperSTT.__new__(WhisperSTT)
    assert hasattr(stt, "transcribe")
    assert hasattr(stt, "transcribe_file")


def test_stt_model_name() -> None:
    """Default model is large-v3-turbo."""
    # Don't actually load the model in unit tests
    assert WhisperSTT.DEFAULT_MODEL == "large-v3-turbo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/voice/test_stt.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# core/voice/stt.py
"""WhisperSTT — speech-to-text using faster-whisper (local, GPU-accelerated)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from shared.traced import traced

logger = logging.getLogger(__name__)


class WhisperSTT:
    """Speech-to-text using faster-whisper (CTranslate2 backend).

    Runs entirely locally on GPU or CPU. No cloud dependency.
    """

    DEFAULT_MODEL = "large-v3-turbo"

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL,
        device: str = "auto",
        compute_type: str = "float16",
    ) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        logger.info("Loaded Whisper model: %s (device=%s)", model_size, device)

    @traced(name="voice.stt.transcribe")
    def transcribe(self, audio_bytes: bytes, language: str = "en") -> str:
        """Transcribe audio bytes to text.

        Args:
            audio_bytes: Raw audio data (WAV, MP3, OGG, etc.)
            language: Language code for transcription.

        Returns:
            Transcribed text string.
        """
        # faster-whisper needs a file path
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            return self.transcribe_file(tmp.name, language=language)

    @traced(name="voice.stt.transcribe_file")
    def transcribe_file(self, file_path: str, language: str = "en") -> str:
        """Transcribe an audio file to text."""
        segments, info = self._model.transcribe(
            file_path, language=language, beam_size=5
        )
        text = " ".join(segment.text.strip() for segment in segments)
        logger.debug(
            "Transcribed %.1fs audio → %d chars (lang=%s, prob=%.2f)",
            info.duration, len(text), info.language, info.language_probability,
        )
        return text
```

Also create `core/voice/__init__.py` and `tests/core/voice/__init__.py` (empty).

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/voice/test_stt.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check core/voice/stt.py --fix && ruff format core/voice/ && mypy core/voice/stt.py --strict`

- [ ] **Step 6: Commit**

```bash
git add core/voice/ tests/core/voice/
git commit -m "feat: WhisperSTT wrapper for local speech-to-text"
```

---

## Task 3: Piper TTS Wrapper

**Files:**
- Create: `core/voice/tts.py`
- Create: `tests/core/voice/test_tts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/voice/test_tts.py
"""Tests for Piper TTS wrapper."""

from __future__ import annotations

from core.voice.tts import PiperTTS


def test_tts_instantiation() -> None:
    tts = PiperTTS.__new__(PiperTTS)
    assert hasattr(tts, "synthesize")


def test_default_voice() -> None:
    assert PiperTTS.DEFAULT_VOICE == "en_GB-alan-medium"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/voice/test_tts.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# core/voice/tts.py
"""PiperTTS — text-to-speech using Piper (local, streaming-capable)."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from shared.traced import traced

logger = logging.getLogger(__name__)


class PiperTTS:
    """Text-to-speech using Piper (local, upgradeable to cloud TTS).

    Piper runs as a subprocess — no Python bindings needed.
    Voice models are downloaded separately to a configurable directory.
    """

    DEFAULT_VOICE = "en_GB-alan-medium"

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        piper_bin: str = "piper",
        model_dir: str = "core/voice/models",
    ) -> None:
        self._voice = voice
        self._piper_bin = piper_bin
        self._model_dir = Path(model_dir)

    @traced(name="voice.tts.synthesize")
    def synthesize(self, text: str) -> bytes:
        """Synthesize text to WAV audio bytes.

        Args:
            text: Text to speak.

        Returns:
            Raw WAV audio bytes.
        """
        model_path = self._model_dir / f"{self._voice}.onnx"

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            cmd = [
                self._piper_bin,
                "--model", str(model_path),
                "--output_file", tmp.name,
            ]
            proc = subprocess.run(
                cmd,
                input=text.encode(),
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0:
                logger.error("Piper TTS failed: %s", proc.stderr.decode())
                raise RuntimeError(f"Piper TTS failed: {proc.stderr.decode()}")

            return Path(tmp.name).read_bytes()

    def synthesize_streaming(self, text: str) -> subprocess.Popen[bytes]:
        """Start a streaming TTS process. Returns Popen with stdout as audio stream.

        The caller reads from proc.stdout in chunks for low-latency streaming.
        """
        model_path = self._model_dir / f"{self._voice}.onnx"
        proc = subprocess.Popen(
            [
                self._piper_bin,
                "--model", str(model_path),
                "--output-raw",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.stdin:
            proc.stdin.write(text.encode())
            proc.stdin.close()
        return proc
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/voice/test_tts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/voice/tts.py tests/core/voice/test_tts.py
git commit -m "feat: PiperTTS wrapper for local text-to-speech"
```

---

## Task 4: Web Channel Server (FastAPI + WebSocket)

**Files:**
- Create: `core/channels/__init__.py`
- Create: `core/channels/web_server.py`
- Create: `core/channels/__main__.py`
- Create: `tests/core/channels/test_web_server.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/channels/test_web_server.py
"""Tests for web channel server."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from core.channels.web_server import create_app


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client() -> TestClient:
    app = create_app(redis_url="redis://localhost:6379")
    return TestClient(app)


def test_health_endpoint(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_static_files_served(client: TestClient) -> None:
    # PWA files should be served from /
    # This will 404 until the static files exist, but the route should be mounted
    resp = client.get("/")
    # Either 200 (if index.html exists) or 404 is acceptable at this stage
    assert resp.status_code in (200, 404)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/channels/test_web_server.py -v`
Expected: FAIL

- [ ] **Step 3: Implement web server**

```python
# core/channels/web_server.py
"""Web channel server — FastAPI with WebSocket for voice + chat."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from bus.schemas.events import AlfredResponse, UserRequest
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM

logger = logging.getLogger(__name__)


def create_app(redis_url: str = "redis://localhost:6379") -> FastAPI:
    """Create the FastAPI application for the web channel."""
    app = FastAPI(title="Alfred Web Channel")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "web-channel"}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        session_id = str(uuid4())
        r: aioredis.Redis[Any] = aioredis.from_url(redis_url)  # type: ignore[type-arg]

        try:
            while True:
                data = await websocket.receive_json()
                content_type = data.get("type", "text")
                content = data.get("content", "")

                # Build UserRequest
                request = UserRequest(
                    source="web-pwa",
                    channel="web_pwa",
                    session_id=session_id,
                    identity_claim=data.get("identity", "guest"),
                    content_type=content_type,
                    content=content,
                )

                # Publish to Redis
                await r.xadd(
                    USER_REQUESTS_STREAM,
                    {"event": request.model_dump_json()},
                )

                # Wait for response (poll responses stream)
                # In production, use a pub/sub or dedicated response channel
                response_text = await _wait_for_response(r, session_id, timeout=30.0)

                await websocket.send_json({
                    "type": "response",
                    "text": response_text,
                    "session_id": session_id,
                })

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected (session=%s)", session_id)
        finally:
            await r.aclose()

    # Mount static files for PWA (if directory exists)
    import os

    web_dir = os.path.join(os.path.dirname(__file__), "..", "..", "web")
    if os.path.isdir(web_dir):
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="static")

    return app


async def _wait_for_response(
    redis: Any,
    session_id: str,
    timeout: float = 30.0,
) -> str:
    """Poll the responses stream for a response matching this session."""
    import time

    start = time.monotonic()
    last_id = "0-0"

    while (time.monotonic() - start) < timeout:
        entries = await redis.xread(
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
```

- [ ] **Step 4: Create entry point**

```python
# core/channels/__main__.py
"""Entry point for the web channel server.

Usage: python -m core.channels
"""

from __future__ import annotations

import uvicorn

from core.channels.web_server import create_app
from shared.config import AlfredConfig
from shared.logging import configure_logging


def main() -> None:
    configure_logging(service="web-channel")
    config = AlfredConfig.from_env()
    app = create_app(redis_url=config.redis_url)
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")


if __name__ == "__main__":
    main()
```

Also create `core/channels/__init__.py` and `tests/core/channels/__init__.py` (empty).

- [ ] **Step 5: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/channels/test_web_server.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add core/channels/ tests/core/channels/
git commit -m "feat: web channel server with WebSocket for voice + chat"
```

---

## Task 5: Web PWA (Minimal Frontend)

**Files:**
- Create: `web/index.html`
- Create: `web/app.js`
- Create: `web/style.css`

- [ ] **Step 1: Create index.html**

```html
<!-- web/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Alfred</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div id="app">
        <header>
            <h1>Alfred</h1>
            <span id="status" class="status-dot disconnected"></span>
        </header>

        <div id="chat-log"></div>

        <div id="input-bar">
            <input type="text" id="chat-input" placeholder="Speak or type..." autocomplete="off">
            <button id="send-btn">Send</button>
            <button id="voice-btn">🎤</button>
        </div>
    </div>
    <script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create app.js**

```javascript
// web/app.js
// Alfred PWA — WebSocket client for voice + chat

const chatLog = document.getElementById('chat-log');
const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const voiceBtn = document.getElementById('voice-btn');
const statusDot = document.getElementById('status');

let ws = null;

function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        statusDot.className = 'status-dot connected';
    };

    ws.onclose = () => {
        statusDot.className = 'status-dot disconnected';
        setTimeout(connect, 3000);
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'response') {
            appendMessage('alfred', data.text);
        }
    };
}

function appendMessage(role, text) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.textContent = text;
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
}

function send() {
    const text = chatInput.value.trim();
    if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

    appendMessage('user', text);
    ws.send(JSON.stringify({ type: 'text', content: text, identity: 'sir' }));
    chatInput.value = '';
}

sendBtn.addEventListener('click', send);
chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') send();
});

// Voice: push-to-talk
let mediaRecorder = null;
let audioChunks = [];

voiceBtn.addEventListener('mousedown', async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);
    audioChunks = [];
    mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data);
    mediaRecorder.onstop = async () => {
        const blob = new Blob(audioChunks, { type: 'audio/webm' });
        // TODO: Send audio blob to server for STT
        // For now, show a placeholder
        appendMessage('user', '[voice message]');
        stream.getTracks().forEach(t => t.stop());
    };
    mediaRecorder.start();
    voiceBtn.classList.add('recording');
});

voiceBtn.addEventListener('mouseup', () => {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
        voiceBtn.classList.remove('recording');
    }
});

connect();
```

- [ ] **Step 3: Create style.css**

```css
/* web/style.css */
* { margin: 0; padding: 0; box-sizing: border-box; }

body {
    font-family: 'Georgia', serif;
    background: #1a1a1a;
    color: #e0d8c8;
    height: 100vh;
    display: flex;
    justify-content: center;
}

#app {
    width: 100%;
    max-width: 600px;
    display: flex;
    flex-direction: column;
    height: 100vh;
}

header {
    padding: 1rem;
    text-align: center;
    border-bottom: 1px solid #333;
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 0.5rem;
}

header h1 { font-weight: normal; letter-spacing: 0.2em; }

.status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    display: inline-block;
}
.status-dot.connected { background: #4a7; }
.status-dot.disconnected { background: #a44; }

#chat-log {
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
}

.message {
    margin-bottom: 0.75rem;
    padding: 0.75rem 1rem;
    border-radius: 4px;
    line-height: 1.5;
}

.message.user {
    background: #2a2a2a;
    text-align: right;
}

.message.alfred {
    background: #1e2e1e;
    border-left: 2px solid #4a7;
}

#input-bar {
    padding: 1rem;
    display: flex;
    gap: 0.5rem;
    border-top: 1px solid #333;
}

#chat-input {
    flex: 1;
    padding: 0.75rem;
    background: #2a2a2a;
    border: 1px solid #444;
    color: #e0d8c8;
    font-family: inherit;
    border-radius: 4px;
}

button {
    padding: 0.75rem 1rem;
    background: #333;
    border: 1px solid #555;
    color: #e0d8c8;
    cursor: pointer;
    border-radius: 4px;
}

button:hover { background: #444; }

#voice-btn.recording { background: #a44; border-color: #c66; }
```

- [ ] **Step 4: Commit**

```bash
git add web/
git commit -m "feat: minimal web PWA for Alfred chat + voice"
```

---

## Task 6: Signal Bridge — Scaffold (Separate Repo)

**Files:**
- Create: `signal-bridge/` directory (in workspace root, not in alfred/)

This is a sovereign service in its own repo. We create the scaffold here; the full implementation requires `signal-cli` and JRE 17+.

- [ ] **Step 1: Create signal-bridge scaffold**

Create the directory structure at the workspace level:

```
/Users/anirudhlath/code/private/alfred/signal-bridge/
├── app/
│   ├── __init__.py
│   ├── bridge.py        # Main bridge loop
│   └── signal_client.py # signal-cli wrapper
├── pyproject.toml
├── Containerfile
├── .env.example
└── README.md
```

- [ ] **Step 2: Create `signal-bridge/pyproject.toml`**

```toml
[project]
name = "alfred-signal-bridge"
version = "0.1.0"
description = "Signal messaging bridge for Alfred"
requires-python = ">=3.13"
dependencies = [
    "alfred-sdk @ file:///${ALFRED_SDK_PATH}",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.4", "mypy>=1.10"]
```

- [ ] **Step 3: Create `signal-bridge/app/bridge.py`**

```python
# signal-bridge/app/bridge.py
"""Signal bridge — translates Signal messages to/from Alfred UserRequest/AlfredResponse."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class SignalBridge:
    """Bridges Signal messages to Alfred via the SDK.

    Uses signal-cli (requires JRE 17+) as a linked device.
    Only accepts messages from the registered phone number.
    """

    def __init__(
        self,
        registered_phone: str,
        signal_cli_path: str = "signal-cli",
        alfred_sdk_redis_url: str = "redis://localhost:6379",
    ) -> None:
        self._phone = registered_phone
        self._signal_cli = signal_cli_path
        self._redis_url = alfred_sdk_redis_url

    async def listen(self) -> None:
        """Listen for incoming Signal messages and forward to Alfred."""
        logger.info("Signal bridge listening for messages from %s", self._phone)

        # signal-cli receive --json outputs one JSON object per line
        proc = await asyncio.create_subprocess_exec(
            self._signal_cli, "-u", self._phone, "receive", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        if proc.stdout is None:
            raise RuntimeError("Failed to start signal-cli")

        async for line in proc.stdout:
            try:
                msg = json.loads(line.decode())
                await self._handle_message(msg)
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.error("Error handling Signal message: %s", e)

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Process a single Signal message."""
        envelope = msg.get("envelope", {})
        source = envelope.get("source", "")
        data_msg = envelope.get("dataMessage", {})
        body = data_msg.get("message", "")

        if not body:
            return

        if source != self._phone:
            logger.warning("Dropping message from unregistered number: %s", source[:4] + "****")
            # TODO: Log for security audit (timestamp, sender hash, no content)
            return

        logger.info("Received message from sir via Signal")

        # TODO: Publish UserRequest via AlfredClient SDK
        # TODO: Subscribe to AlfredResponse and send reply via signal-cli

    async def send(self, text: str) -> None:
        """Send a message to the registered phone number."""
        proc = await asyncio.create_subprocess_exec(
            self._signal_cli, "-u", self._phone, "send",
            "-m", text, self._phone,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
```

- [ ] **Step 4: Create Containerfile**

```dockerfile
# signal-bridge/Containerfile
FROM python:3.13-slim

# signal-cli requires JRE 17+
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jre-headless \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Install signal-cli
ARG SIGNAL_CLI_VERSION=0.13.0
RUN wget -q "https://github.com/AsamK/signal-cli/releases/download/v${SIGNAL_CLI_VERSION}/signal-cli-${SIGNAL_CLI_VERSION}.tar.gz" \
    && tar xf signal-cli-${SIGNAL_CLI_VERSION}.tar.gz -C /opt \
    && ln -s /opt/signal-cli-${SIGNAL_CLI_VERSION}/bin/signal-cli /usr/local/bin/signal-cli \
    && rm signal-cli-${SIGNAL_CLI_VERSION}.tar.gz

WORKDIR /app
COPY . .

# Install alfred-sdk from source (not on PyPI)
COPY --from=alfred-sdk /sdk /opt/alfred-sdk
RUN pip install /opt/alfred-sdk

RUN pip install .

CMD ["python", "-m", "app.bridge"]
```

- [ ] **Step 5: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/signal-bridge
git init
git add -A
git commit -m "feat: Signal bridge scaffold with signal-cli + alfred-sdk"
```

---

## Task 7: Add Channels to Runner

**Files:**
- Modify: `runner/__main__.py`

- [ ] **Step 1: Add channels service**

```python
# In SERVICES list, add:
ServiceSpec(name="channels", module="core.channels", delay=2.0),
```

- [ ] **Step 2: Run ruff + mypy**

Run: `ruff check runner/__main__.py --fix && mypy runner/__main__.py --strict`

- [ ] **Step 3: Commit**

```bash
git add runner/__main__.py
git commit -m "feat: add web channel server to unified runner"
```

---

## Task 8: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -v`

- [ ] **Step 2: Run full linting + type checking**

Run: `ruff check . --fix && ruff format . && mypy bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
