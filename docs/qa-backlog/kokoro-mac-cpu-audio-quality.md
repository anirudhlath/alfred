# Kokoro TTS Audio Quality on Mac (CPU Execution Provider)

**Feature:** Kokoro-82M TTS backend (`core/voice/tts_kokoro.py`), CPU EP on macOS
**Priority:** high
**Type:** functional

## Prerequisites
- Infra up: `bash scripts/dev-up.sh` (Redis + Mosquitto)
- Default config — `.env` has `ALFRED_TTS_BACKEND` unset or `kokoro`, `KOKORO_ONNX_PROVIDER`
  unset or `auto` (resolves to CPU on the M4 Max, no CUDA present)
- `uv run python -m runner` running
- Browser at `http://localhost:8081`, authenticated (passkey), real speakers/headphones,
  quiet room
- (Optional) stopwatch or browser devtools timing for latency observation

## Test Steps
1. Open the Chat page (`/`) and send several short text messages that will produce a spoken
   reply — vary content: a short factual answer, a longer multi-sentence answer, one with
   numbers/dates, one with a proper noun or two.
2. Listen to each reply. Judge by ear: does it sound like a natural (if synthetic) male US
   voice, free of clipping, crackling, robotic artifacts, or dropped syllables?
3. For the very first reply after a fresh runner start, note that synthesis may be slow
   (cold Kokoro model construction, 10–40s) — this is expected, not a defect.
4. For subsequent replies (model already warm), roughly time from "reply text appears" to
   "audio starts playing" for a short (~1 sentence) reply.
5. Check the server log for the line `Loaded Kokoro TTS (voice=am_michael, speed=1.0,
   provider=CPUExecutionProvider)` to confirm the CPU EP was actually used.

## Expected Result
- Voice is recognizably `am_michael` (US male) per `docs/voice.md`'s "Popular voices" table —
  not silently on a different voice or a Piper fallback.
- No audible clipping, crackling, or robotic glitches across the varied test sentences.
- Warm-model synthesis is fast — `docs/voice.md` documents ~0.4s per short reply
  (RTF ~0.11–0.19) on the M4 Max CPU EP; latency should feel close to that, noticeably
  snappier than the old Piper backend.
- First-reply-after-cold-start latency is materially higher (model construction) — that is
  expected and should not be reported as a regression.

## Notes
- This is a pure by-ear + stopwatch judgment call — no automated test asserts Kokoro "sounds
  good" or hits the sub-500ms warm-synthesis budget on real hardware.
- CoreML EP (`KOKORO_ONNX_PROVIDER=coreml`) is opt-in and documented as silently FP16-converting
  / falling back — do not use it for this ticket unless specifically investigating that known
  limitation; test the default `auto` (CPU) path.
- If a prior Piper-backend memory of the voice is available for comparison, note whether Kokoro
  is a clear quality/naturalness improvement, as the migration intends.
