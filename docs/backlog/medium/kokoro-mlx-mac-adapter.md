# Kokoro-MLX Apple-Silicon TTS adapter (fast-follow)

**Priority:** medium
**Epic:** voice

## Summary
Add a Mac-only `KokoroMLXTTS(TTSBackend)` adapter using MLX for GPU-accelerated
Kokoro synthesis on Apple Silicon, auto-selected on macOS, ONNX+CUDA on Linux.

## Motivation / evidence
Benchmarked on the M4 Max (am_michael, warm): MLX ≈ **5–8× faster** than ONNX CPU
— short reply **0.076 s / RTF 0.019** vs 0.4 s / RTF 0.15 — with native 48 kHz
output. Both are within the sub-500 ms budget, so this is headroom, not a fix.

## Why deferred (not in the initial Kokoro change)
- `kokoro-mlx` sets `requires-python <3.13`; Alfred is 3.13+ → must **vendor** its
  MIT pure-Python inference (config/generate/istftnet/kokoro/model/modules/
  phonemize/voices ≈ 8 files; deps mlx/numpy/safetensors).
- It drags `torch` + spaCy + `en_core_web_sm` via `misaki[en]` — needs trimming.
- Its espeak init resists the standard fix (misaki re-points to a broken bundled
  dylib) — needs an `espeakng_loader` redirect to a working espeak.
- Alpha (v0.1.2, 13★, single maintainer).

## Acceptance criteria
- [ ] Vendor the MIT MLX inference under `core/voice/` (or a pinned fork) running on 3.13.
- [ ] `KokoroMLXTTS(TTSBackend)` adapter; registry entry `mlx`.
- [ ] Auto-select MLX on Apple Silicon (platform + mlx availability), ONNX on Linux.
- [ ] Resolve espeak + spaCy wiring cleanly (no monkeypatch at runtime).
- [ ] Model source `mlx-community/Kokoro-82M-bf16` via `hf_models.ensure_model`.
- [ ] Benchmark parity check + audio-quality QA vs ONNX.
