# D28: Fix Double TTS on Trigger Notifications

## Problem

When a trigger fires with URGENT urgency, TTS audio plays twice on web/PWA clients. The D26 fix confirmed single delivery across processes, but the bug is intra-process: both the WebSocket adapter and Voice adapter handle the same URGENT notification in the channels process, each sending audio to the same WebSocket connections. The frontend plays both.

### Root Cause

`get_adapters_for_urgency(URGENT)` returns both `WebSocketChannelAdapter` and `VoiceChannelAdapter`. Both are initialized with `get_sessions=get_web_websockets` in `core/channels/__main__.py`. Both deliver in parallel via `asyncio.gather()`:

1. WebSocket adapter sends `{"type": "notification", ...}` (text-only, no audio currently)
2. Voice adapter synthesizes TTS and sends `{"type": "voice_notification", "audio": ...}`

The frontend handles both message types and calls `playAudio()` on each.

### Scope

Web/PWA only. iOS clients are unaffected — `get_web_websockets()` excludes iOS sessions, and iOS receives notifications via APNs (no TTS audio).

## Design

Consolidate notification delivery for web clients into the WebSocket adapter. The Voice adapter is removed from the channels process but retained in the codebase for future headless/speaker-only use.

### 1. WebSocket Adapter — Add TTS for URGENT

**File:** `core/notifications/adapters/websocket.py`

- Add optional `get_tts: Callable[[], Any | None] | None = None` constructor parameter (same pattern as Voice adapter).
- In `deliver()`, when `notification.urgency == Urgency.URGENT` and TTS is available, synthesize audio and include `"audio"` (base64 WAV) in the JSON payload.
- IMPORTANT and INFORMATIONAL notifications remain text-only (no audio).
- TTS synthesis failure logs a warning but does not block text delivery.

### 2. Channels Process — Remove Voice Adapter for Web Sessions

**File:** `core/channels/__main__.py`

- Remove the `ChannelRegistry.set_instance("voice", ...)` call.
- Pass `get_tts=_get_tts_lazy` to the `WebSocketChannelAdapter` constructor instead.
- The Voice adapter class (`core/notifications/adapters/voice.py`) is unchanged — it remains available for future headless/speaker-only scenarios.

### 3. Frontend — Remove Dead `voice_notification` Handler

**File:** `web/app.js`

- Remove the `data.type === 'voice_notification'` block (lines 59-61). No code sends this message type to web clients anymore.
- The existing `notification` handler already plays audio when `data.audio` is present — no changes needed there.

### 4. Regression Test

- Test that delivering an URGENT notification via `WebSocketChannelAdapter` (with TTS) produces exactly one WebSocket message containing both text fields and audio.
- Test that `get_adapters_for_urgency(URGENT)` in the channels process returns only the WebSocket adapter (Voice adapter not initialized).
- Test that IMPORTANT notifications have no audio in the payload.

## Files Changed

| File | Change |
|---|---|
| `core/notifications/adapters/websocket.py` | Add `get_tts` param, synthesize audio for URGENT |
| `core/channels/__main__.py` | Remove Voice adapter init, pass `get_tts` to WebSocket adapter |
| `web/app.js` | Remove `voice_notification` handler |
| `tests/` | Regression tests for single-delivery and urgency-based audio |

## Non-Goals

- Changing Voice adapter internals (retained for future use)
- Modifying APNs or Signal adapter behavior
- Adding audio to IMPORTANT/INFORMATIONAL notifications
- Frontend audio queue (D23) or streaming TTS (D11)
