# TTS runtime-failure negative cache / backoff

**Priority:** low
**Epic:** voice

## Summary

Add a short-TTL negative cache (or exponential backoff) to `core/channels/voice_models.get_tts()`
for runtime backend-construction failures, so a *persistently* broken backend does not re-attempt
a full Kokoro + Piper build on every voice reply / URGENT notification.

## Context / motivation

The code-architect review of `feat/kokoro-tts-backend` deliberately made runtime init failures
non-cached (only all-backends-`ImportError` caches `_FAILED` permanently), so a transient HF
download blip or CUDA hiccup can recover on the next request. The flip side: a genuinely broken
backend (e.g. permanently misconfigured CUDA on the 4090) retries construction on each request.
This is bounded and safe today — serialized by `_voice_load_lock`, off-loop via `to_thread`,
`ensure_model` is cache-hit after first download, and request frequency is low — but there is no
backoff or eventual give-up.

## Acceptance criteria

- [ ] Runtime construction failures are negatively cached for a short TTL (e.g. 60s) or retried
      with exponential backoff, instead of on every call.
- [ ] Transient failures still recover automatically after the TTL/backoff window.
- [ ] `ImportError`-only failures keep the existing permanent `_FAILED` semantics.
- [ ] Covered by unit tests (TTL expiry → retry; within TTL → no construction attempt).
