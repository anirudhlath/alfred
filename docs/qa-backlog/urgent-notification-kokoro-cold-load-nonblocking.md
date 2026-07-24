# URGENT Notification Speech Survives a Cold Kokoro Load Without Blocking Other WS Traffic

**Feature:** `WebSocketChannelAdapter` async TTS getter
(`core/notifications/adapters/websocket.py`)
**Priority:** high
**Type:** regression

## Prerequisites
- Ability to restart the runner into a genuinely cold in-process TTS cache (a fresh
  `uv run python -m runner` start, acted on before background warmup finishes loading Kokoro)
- Two browser tabs/WebSocket connections to `http://localhost:8081`
- Ability to publish a `Notification` directly to the Redis dispatch stream — adapt the snippet
  from `voice-satellite-urgent-announcement-audio-quality.md`:
  ```bash
  uv run python -c "
  import asyncio
  import redis.asyncio as redis
  from core.notifications.schema import Notification, Urgency

  async def main():
      r = redis.from_url('redis://localhost:6379')
      n = Notification(
          title='Test Alert',
          body='This is an urgent test notification',
          urgency=Urgency.URGENT,
          source='qa-manual-test',
      )
      await r.xadd('alfred:notifications:dispatch', {'notification': n.model_dump_json()})
      await r.close()

  asyncio.run(main())
  "
  ```

## Test Steps
1. Restart the runner (`uv run python -m runner`). Note in the logs when background warmup
   (`start_warmup` → `_warm_tts`) begins loading Kokoro — it's documented as a 10–40s
   construction.
2. Immediately (before warmup finishes) open two browser tabs to `http://localhost:8081`. In
   tab A, send a rapid sequence of ordinary text chat messages (content that doesn't strictly
   need audio, just needs a live reply) to keep WS traffic flowing.
3. While tab A's traffic is in flight and Kokoro is still cold-loading, run the notification
   snippet above from a second terminal so the URGENT notification lands mid-load.
4. Observe tab A: do its chat messages keep getting timely responses, or does the whole
   connection stall while the URGENT notification's TTS backend is still constructing?
5. Observe the URGENT notification: it should eventually arrive with synthesized audio once the
   cold load completes — check devtools WS frames for a `notification` message with a
   base64 `audio` field.
6. Repeat with a warm cache (fire a second URGENT notification once Kokoro is already loaded) —
   this one should synthesize near-instantly with no observable delay to other traffic.

## Expected Result
- Tab A's ordinary chat traffic is **not** blocked or meaningfully delayed by the URGENT
  notification's cold Kokoro construction happening concurrently — the event loop stays
  responsive to other connections while the model loads off-thread.
- The URGENT notification is delayed by the cold-load time (10–40s) but still arrives with
  audio once construction finishes — no crash, no dropped notification, no notification sent
  without audio due to a timeout.
- The warm-cache repeat shows near-zero added latency for the URGENT notification and no
  impact on other traffic.

## Notes
- This branch changed `WebSocketChannelAdapter` from a synchronous `get_tts()` callable plus
  `asyncio.to_thread(tts.synthesize, …)` to an async `aget_tts: Callable[[], Awaitable[TTSBackend
  | None]]` plus `synthesize_async()`. The inline comment in
  `core/notifications/adapters/websocket.py` states the reasoning directly: "TTS construction is
  a cold 10-40s load ... and must never run synchronously on the event loop that's also serving
  WebSockets/notifications." This ticket is the only place that claim gets exercised against a
  real cold load rather than a mock.
- Unit tests (`tests/core/notifications/test_adapters.py`) mock `aget_tts`/`synthesize_async`
  directly, so they cannot observe real event-loop contention during an actual model
  construction — that's exactly the gap this manual ticket covers.
- `_voice_load_lock` in `core/channels/voice_models.py` means a *concurrent chat reply that also
  needs TTS* during the same cold-load window will legitimately wait behind the same lock (by
  design — avoids double-constructing the model). Don't mistake that expected serialization for
  a bug; only traffic that doesn't need TTS (or arrives after TTS is already warm) should stay
  fully responsive.
