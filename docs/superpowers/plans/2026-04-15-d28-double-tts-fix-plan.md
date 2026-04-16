# D28: Fix Double TTS on Trigger Notifications — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix double TTS playback on URGENT trigger notifications by consolidating audio delivery into the WebSocket adapter.

**Architecture:** Move TTS synthesis from the Voice adapter into the WebSocket adapter for URGENT notifications. Remove the Voice adapter from the channels process. Clean up the dead `voice_notification` frontend handler.

**Tech Stack:** Python 3.13+, pytest, asyncio, Pydantic v2

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `core/notifications/adapters/websocket.py` | Modify | Add `get_tts` param, synthesize audio for URGENT |
| `core/channels/__main__.py` | Modify | Remove Voice adapter init, pass `get_tts` to WebSocket adapter |
| `web/app.js` | Modify | Remove dead `voice_notification` handler |
| `tests/core/notifications/test_adapters.py` | Modify | Add regression tests for single-delivery TTS |

---

### Task 1: WebSocket Adapter — Add TTS for URGENT

**Files:**
- Modify: `core/notifications/adapters/websocket.py`
- Modify: `tests/core/notifications/test_adapters.py`

- [ ] **Step 1: Write failing test — URGENT notification includes audio**

Add to the `TestWebSocketChannelAdapter` class in `tests/core/notifications/test_adapters.py`:

```python
@pytest.mark.asyncio
async def test_urgent_notification_includes_audio(self) -> None:
    from core.notifications.adapters.websocket import WebSocketChannelAdapter

    tts = MagicMock()
    tts.synthesize.return_value = b"\x00\x01\x02\x03"
    ws = AsyncMock()
    session_getter = MagicMock(return_value=[ws])

    adapter = WebSocketChannelAdapter(get_sessions=session_getter, get_tts=lambda: tts)
    await adapter.deliver(_make_notification(Urgency.URGENT))

    tts.synthesize.assert_called_once_with("Test: Hello world")
    payload = ws.send_json.call_args[0][0]
    assert payload["type"] == "notification"
    assert payload["audio"] == base64.b64encode(b"\x00\x01\x02\x03").decode()
```

- [ ] **Step 2: Write failing test — IMPORTANT notification has no audio**

Add to the `TestWebSocketChannelAdapter` class:

```python
@pytest.mark.asyncio
async def test_important_notification_has_no_audio(self) -> None:
    from core.notifications.adapters.websocket import WebSocketChannelAdapter

    tts = MagicMock()
    ws = AsyncMock()
    session_getter = MagicMock(return_value=[ws])

    adapter = WebSocketChannelAdapter(get_sessions=session_getter, get_tts=lambda: tts)
    await adapter.deliver(_make_notification(Urgency.IMPORTANT))

    tts.synthesize.assert_not_called()
    payload = ws.send_json.call_args[0][0]
    assert "audio" not in payload
```

- [ ] **Step 3: Write failing test — TTS failure does not block text delivery**

Add to the `TestWebSocketChannelAdapter` class:

```python
@pytest.mark.asyncio
async def test_tts_failure_still_delivers_text(self) -> None:
    from core.notifications.adapters.websocket import WebSocketChannelAdapter

    tts = MagicMock()
    tts.synthesize.side_effect = RuntimeError("TTS crashed")
    ws = AsyncMock()
    session_getter = MagicMock(return_value=[ws])

    adapter = WebSocketChannelAdapter(get_sessions=session_getter, get_tts=lambda: tts)
    await adapter.deliver(_make_notification(Urgency.URGENT))

    ws.send_json.assert_called_once()
    payload = ws.send_json.call_args[0][0]
    assert payload["type"] == "notification"
    assert "audio" not in payload
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest tests/core/notifications/test_adapters.py::TestWebSocketChannelAdapter -v`
Expected: 3 new tests FAIL (TypeError on `get_tts` kwarg / missing `audio` key)

- [ ] **Step 5: Implement WebSocket adapter TTS support**

Replace the full contents of `core/notifications/adapters/websocket.py`:

```python
"""WebSocket channel adapter — pushes notifications to connected web sessions."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger

from core.notifications.channels import ChannelAdapter, ChannelRegistry
from core.notifications.schema import Notification, Urgency

if TYPE_CHECKING:
    from collections.abc import Callable


@ChannelRegistry.register()
class WebSocketChannelAdapter(ChannelAdapter):
    """Push notification JSON to all connected WebSocket sessions."""

    name: ClassVar[str] = "websocket"
    supported_urgencies: ClassVar[set[Urgency]] = {Urgency.IMPORTANT, Urgency.URGENT}

    def __init__(
        self,
        get_sessions: Callable[[], list[Any]] | None = None,
        get_tts: Callable[[], Any | None] | None = None,
    ) -> None:
        self._get_sessions = get_sessions
        self._get_tts = get_tts

    async def deliver(self, notification: Notification) -> None:
        """Push notification to all active WebSocket connections."""
        if self._get_sessions is None:
            logger.debug("WebSocketChannelAdapter: no session getter, skipping")
            return
        sessions = self._get_sessions()
        if not sessions:
            logger.debug("WebSocketChannelAdapter: no active sessions, skipping")
            return

        payload: dict[str, Any] = {
            "type": "notification",
            "title": notification.title,
            "body": notification.body,
            "urgency": notification.urgency.value,
            "notification_id": notification.notification_id,
        }

        if notification.urgency == Urgency.URGENT and self._get_tts is not None:
            tts = self._get_tts()
            if tts is not None:
                try:
                    text = f"{notification.title}: {notification.body}"
                    wav_bytes: bytes = tts.synthesize(text)
                    payload["audio"] = base64.b64encode(wav_bytes).decode()
                except Exception as exc:
                    logger.warning("WebSocketChannelAdapter: TTS synthesis failed: {}", exc)

        for ws in sessions:
            try:
                await ws.send_json(payload)
            except Exception as exc:
                logger.warning("Failed to push notification to WebSocket: {}", exc)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/core/notifications/test_adapters.py::TestWebSocketChannelAdapter -v`
Expected: All tests PASS (existing + 3 new)

- [ ] **Step 7: Commit**

```bash
git add core/notifications/adapters/websocket.py tests/core/notifications/test_adapters.py
git commit -m "feat(d28): add TTS synthesis to WebSocket adapter for URGENT notifications"
```

---

### Task 2: Remove Voice Adapter from Channels Process

**Files:**
- Modify: `core/channels/__main__.py`

- [ ] **Step 1: Update channels `__main__.py`**

Replace the `main()` function and imports in `core/channels/__main__.py`:

```python
"""Entry point for the web channel server.

Usage: python -m core.channels
"""

from __future__ import annotations

import os
import time

import uvicorn
from loguru import logger

from core.channels.web_server import create_app, get_web_websockets
from core.notifications.adapters.websocket import WebSocketChannelAdapter
from core.notifications.channels import ChannelRegistry
from shared.config import AlfredConfig
from shared.logging import configure_logging


def _get_tts_lazy() -> object:
    """Lazy TTS getter matching web_server pattern."""
    from core.channels.web_server import _get_tts

    return _get_tts()


def main() -> None:
    configure_logging(service="web-channel")
    config = AlfredConfig.from_env()

    # Wire channel adapters — only push to web/PWA clients.
    # iOS receives notifications via APNs; notification_id dedup is a safety net.
    # WebSocket adapter handles both text and TTS audio (URGENT only).
    ChannelRegistry.set_instance(
        "websocket",
        WebSocketChannelAdapter(get_sessions=get_web_websockets, get_tts=_get_tts_lazy),
    )

    app = create_app(redis_url=config.redis_url)
    port = int(os.getenv("CHANNELS_PORT", "8081"))
    for attempt in range(5):
        try:
            uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
            break
        except OSError as e:
            if e.errno == 48 and attempt < 4:
                wait = attempt + 1
                logger.warning("Port {} in use, retrying in {}s...", port, wait)
                time.sleep(wait)
            else:
                raise


if __name__ == "__main__":
    main()
```

Key changes:
- Removed `VoiceChannelAdapter` import
- Removed `ChannelRegistry.set_instance("voice", ...)` call
- Passed `get_tts=_get_tts_lazy` to `WebSocketChannelAdapter`

- [ ] **Step 2: Run full test suite to check for regressions**

Run: `pytest tests/core/notifications/ -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add core/channels/__main__.py
git commit -m "refactor(d28): remove Voice adapter from channels process, route TTS through WebSocket adapter"
```

---

### Task 3: Remove Dead Frontend Handler

**Files:**
- Modify: `web/app.js`

- [ ] **Step 1: Remove `voice_notification` handler**

In `web/app.js`, remove these lines (currently lines 59-62):

```javascript
        if (data.type === 'voice_notification') {
            if (data.audio) playAudio(data.audio);
            return;
        }
```

The existing `notification` handler at line 54-57 already handles audio via `if (data.audio) playAudio(data.audio)`.

- [ ] **Step 2: Commit**

```bash
git add web/app.js
git commit -m "cleanup(d28): remove dead voice_notification handler from frontend"
```

---

### Task 4: Integration Regression Test

**Files:**
- Modify: `tests/core/notifications/test_adapters.py`

- [ ] **Step 1: Write regression test — URGENT produces exactly one WebSocket message**

Add a new test class at the end of `tests/core/notifications/test_adapters.py`:

```python
class TestD28NoDoubleTTS:
    """Regression: URGENT notification must produce exactly one WS message with audio."""

    @pytest.mark.asyncio
    async def test_urgent_single_delivery_with_audio(self) -> None:
        """Simulate channels-process adapter setup: only WebSocket adapter initialized."""
        from core.notifications.adapters.websocket import WebSocketChannelAdapter

        ChannelRegistry.reset()

        tts = MagicMock()
        tts.synthesize.return_value = b"\xff\xd8audio"
        ws = AsyncMock()
        session_getter = MagicMock(return_value=[ws])

        adapter = WebSocketChannelAdapter(get_sessions=session_getter, get_tts=lambda: tts)
        ChannelRegistry.set_instance("websocket", adapter)

        adapters = ChannelRegistry.get_adapters_for_urgency(Urgency.URGENT)
        assert len(adapters) == 1
        assert adapters[0].name == "websocket"

        await adapters[0].deliver(_make_notification(Urgency.URGENT))

        assert ws.send_json.call_count == 1
        payload = ws.send_json.call_args[0][0]
        assert payload["type"] == "notification"
        assert "audio" in payload
```

- [ ] **Step 2: Run the regression test**

Run: `pytest tests/core/notifications/test_adapters.py::TestD28NoDoubleTTS -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `pytest -x -q`
Expected: All tests PASS

- [ ] **Step 4: Lint and type-check**

```bash
ruff check . --fix && ruff format .
mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
```

Expected: Clean

- [ ] **Step 5: Commit**

```bash
git add tests/core/notifications/test_adapters.py
git commit -m "test(d28): add regression test for single URGENT notification delivery"
```
