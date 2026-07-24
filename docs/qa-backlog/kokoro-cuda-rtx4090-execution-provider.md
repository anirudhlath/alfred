# Kokoro TTS on the RTX 4090 (CUDA Execution Provider)

**Feature:** Kokoro-82M TTS backend (`core/voice/tts_kokoro.py`), CUDA EP on the CachyOS deployment
**Priority:** high (hardware-gated — RTX 4090 only, cannot run on the Mac dev machine)
**Type:** functional

## Prerequisites
- CachyOS deployment machine with the RTX 4090, NVIDIA drivers + CUDA runtime installed
- Swap the ONNX runtime package (mutually exclusive with the CPU package, per `docs/voice.md`):
  ```bash
  uv pip uninstall onnxruntime && uv pip install onnxruntime-gpu
  ```
- `.env`: `KOKORO_ONNX_PROVIDER=auto` (default — should auto-pick CUDA) or explicitly `cuda`
- Redis + Mosquitto infra up (`bash scripts/dev-up.sh` or the Docker Compose prod stack per
  `CLAUDE.md`), `uv run python -m runner`

## Test Steps
1. Install `onnxruntime-gpu` as above and start the stack.
2. Trigger a spoken reply (web channel chat at whatever port the deployment exposes, or the
   URGENT-notification Redis XADD snippet from
   `voice-satellite-urgent-announcement-audio-quality.md`).
3. Check the server startup/first-synthesis log line: `Loaded Kokoro TTS (voice=..., speed=...,
   provider=CUDAExecutionProvider)` — confirm CUDA was actually selected, not silently CPU.
4. Time synthesis latency for a short reply and a longer multi-sentence reply; compare
   qualitatively against the Mac CPU EP numbers documented in `docs/voice.md`
   (~0.4s / RTF ~0.11–0.19).
5. Listen to the output — audio quality should be effectively identical to the CPU EP output
   (same ONNX graph and weights, only the execution provider differs).
6. Fire several replies back-to-back to rule out CUDA OOM / driver errors under repeated calls.

## Expected Result
- Log line confirms `provider=CUDAExecutionProvider`, not a silent CPU fallback.
- Synthesis is at least as fast as the Mac CPU EP, ideally noticeably faster.
- Audio quality is indistinguishable from the CPU EP output.
- No CUDA errors, OOM, or driver crashes across repeated synthesis calls.

## Notes
- `onnxruntime` and `onnxruntime-gpu` are mutually exclusive installs — mixing them can cause
  import-time provider conflicts; `docs/voice.md` calls this out explicitly.
- `_resolve_provider("auto")` in `tts_kokoro.py` checks `ort.get_available_providers()`; if CUDA
  isn't actually visible to `onnxruntime-gpu` (missing CUDA/cuDNN shared libs, wrong driver
  version), `auto` silently falls back to CPU without raising — the whole point of this ticket is
  confirming CUDA was genuinely engaged, not just that synthesis succeeded.
- No CI runner has a 4090, so this path is entirely unverified by automation.
