# Piper Fallback: Explicit Config Switch and Kokoro Runtime-Failure Fallback

**Feature:** TTS backend selection + fallback (`core/voice/tts_registry.py`,
`core/channels/voice_models.py::get_tts`)
**Priority:** critical
**Type:** functional

## Prerequisites
- `uv run python -m runner` (stopped/restartable), ability to edit `.env`
- Browser at `http://localhost:8081`, authenticated
- For the runtime-failure scenario: a Kokoro model already HF-cached once (run the stack
  successfully on `kokoro` first), and the ability to locate/corrupt that cached file at
  `~/.cache/huggingface/hub/models--fastrtc--kokoro-onnx/snapshots/*/kokoro-v1.0.onnx`

## Test Steps

### A — Explicit `ALFRED_TTS_BACKEND=piper`
1. Set `ALFRED_TTS_BACKEND=piper` in `.env`.
2. Start the runner, open the web chat, trigger a spoken reply.
3. Listen: the voice should be Piper's `en_GB-alan-medium` (UK male) — clearly different in
   voice identity and pacing from Kokoro `am_michael`.
4. Check the server log for `Loaded Piper TTS voice: en_GB-alan-medium`.
5. Revert `.env` (`ALFRED_TTS_BACKEND=kokoro` or unset) when done.

### B — Runtime-failure fallback (configured Kokoro fails → Piper speaks)
1. With `ALFRED_TTS_BACKEND` unset/`kokoro` (default), start the runner once and trigger a
   synthesis so the Kokoro model is HF-cached, then stop the runner.
2. Corrupt the cached Kokoro model to force a *runtime* construction failure — e.g. truncate or
   rename `kokoro-v1.0.onnx` in the snapshot directory above (do **not** just uninstall
   `kokoro-onnx` — that produces an `ImportError`, the silent deps-missing fallback path, not
   the loud runtime-failure path this ticket targets).
3. Restart the runner, trigger a spoken reply.
4. Watch server logs for: an ERROR from `_construct_backend` (`Failed to initialise KokoroTTS:
   …`), followed by a WARNING naming the configured backend, the error, and the fallback now
   active (`Configured TTS backend 'kokoro' failed to initialise (...); falling back to
   'piper'`).
5. Listen: the reply should still be spoken audio, now in the Piper voice — no silent failure,
   no crash, no missing-audio response.
6. Restore the Kokoro model file. Restart the runner and confirm Kokoro resumes (runtime
   failures are not cached across process restarts — the in-memory `_lazy_cache` is
   per-process).

## Expected Result
- A: `ALFRED_TTS_BACKEND=piper` produces a clearly Piper-voiced reply with a matching log line —
  no other change needed anywhere else in the stack (per `docs/voice.md`'s "Switching back to
  Piper" section).
- B: A genuine Kokoro construction failure at runtime logs loudly (ERROR + WARNING, not a
  silent swallow) and falls back to a working Piper voice — the user still gets spoken audio
  instead of a dropped/broken reply.

## Notes
- This exercises `resolve_backend_order()` (`core/voice/tts_registry.py`) and
  `get_tts()`/`_construct_backend()` (`core/channels/voice_models.py`) end to end with real
  model loads — automated unit tests mock backend construction, so a genuine model-load failure
  → fallback → real Piper synthesis is unverified by CI.
- Distinguish "ImportError" (optional dependency missing → silent fallback, by design) from
  "runtime failure" (dependency present, construction raised → loud fallback with a named
  culprit) — this ticket is specifically about the latter, the more regression-prone path.
- Per `docs/backlog/low/tts-runtime-failure-backoff.md`, a *persistently* broken Kokoro
  currently retries full construction on every request (no backoff/negative-cache yet) — that's
  a known, already-filed limitation, not a new bug to report here.
