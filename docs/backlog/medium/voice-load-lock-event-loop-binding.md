# voice_models Load Lock Binds to First Event Loop

## Summary

`core/channels/voice_models.py` holds a module-level `asyncio.Lock()`
(`_voice_load_lock`). An asyncio.Lock created at import time binds to whichever event loop
first awaits it; any later use from a different loop raises
`RuntimeError: ... attached to a different loop`.

## Context / Motivation

Latent on master (the lock moved verbatim from `web_server.py` during the voice-satellite
bridge extraction). In production the channels process has one loop, so it never fires. In
tests it is a collection-ordering hazard: any real-lifespan test (TestClient startup runs
the warmup task, which touches the lock) poisons the lock for subsequent test files that
run their own loops. The satellite wiring tests (2026-07) had to mock `_aget_stt`/`_aget_tts`
specifically to dodge this; `tests/core/channels/test_voice_async.py` currently passes only
by alphabetical luck.

## Acceptance Criteria

- The lock is loop-safe: created lazily per running loop, or replaced with a pattern that
  doesn't capture a loop at import (e.g. lock created inside the first coroutine call and
  stored keyed by running loop, or a `threading.Lock` around the blocking construct).
- `tests/core/channels/` passes regardless of test-file collection order (spot-check by
  running `test_wiring.py` and `test_voice_async.py` in both orders without the
  `_aget_stt`/`_aget_tts` mocks that currently mask the issue — then re-add mocks only if
  still desired for speed).
- No behavior change to lazy voice-model loading semantics (single load under concurrency).
