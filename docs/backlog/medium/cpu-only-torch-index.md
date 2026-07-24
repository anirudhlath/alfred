# CPU-Only PyTorch Index to Shrink the Fat Image

## Summary

The containerized image measured ~9.01GB during Part 2 containerization review, and the
single largest contributor is `torch` (pulled in transitively by `sentence-transformers`
and `speechbrain` via the `memory`/`voice` extras) resolving to a CUDA-enabled build by
default. Alfred's container never does GPU inference inside the container on either
target platform — dev is Apple Silicon (no CUDA), and prod (CachyOS) runs Ollama on bare
metal outside the container, not GPU-passed-through into it — so the bundled NVIDIA CUDA
runtime libraries are pure dead weight, shipped and never loaded.

## Context / Motivation

- `pyproject.toml`'s `memory` extra (`sentence-transformers`, `transformers`) and `voice`
  extra (`speechbrain`) both pull `torch` transitively; neither declares a CPU-only
  index, so `uv pip install` resolves the default PyPI wheel, which bundles
  `nvidia-*-cu12` shared libraries (cuBLAS, cuDNN, cuFFT, etc. — several GB by
  themselves).
- The Containerfile's dependency layer (`uv pip install --system --no-cache -r
  pyproject.toml --extra voice --extra memory --extra integrations`) installs whatever
  `uv` resolves from the default index — no `--index-url`/`--extra-index-url` override
  for a CPU-only torch build.
- All embedding/STT/TTS/speaker-ID inference the container actually performs
  (`SentenceTransformerProvider`, `WhisperSTT`, `PiperTTS`, `SpeakerID`) runs on CPU in
  this deployment shape today — SLM inference is the only GPU-bound work, and that
  happens externally via Ollama (host or remote), never inside the container.
- PyTorch publishes a dedicated CPU-only wheel index
  (`https://download.pytorch.org/whl/cpu`) specifically for this case.

## Acceptance Criteria

- [ ] Containerfile's Python dependency layer installs `torch` from the CPU-only index
      (`uv pip install --system --index-url https://download.pytorch.org/whl/cpu ...` or
      the equivalent `[tool.uv.sources]` per-package index pin) instead of the default
      CUDA-bundled wheel.
- [ ] Final image size drops meaningfully — target **under ~5GB** (from the measured
      ~9.01GB baseline).
- [ ] `alfredctl smoke` still passes end-to-end (embedding search, STT/TTS, speaker-ID
      all still functional on CPU — this should be a no-op behaviorally, only a wheel
      swap).
- [ ] `container-build.yml` CI (amd64 + arm64) stays green — verify the CPU wheel index
      actually publishes both architectures PyTorch supports today.
- [ ] Native (non-container) dev installs are unaffected — this constraint applies to the
      Containerfile's install step only, not `pyproject.toml`'s own dependency
      resolution, so a host with a real GPU (e.g. the CachyOS 4090 box, if it ever runs
      `sentence-transformers`/`speechbrain` natively outside the container) still gets a
      CUDA-capable torch build.

## Notes

- If GPU-accelerated in-container inference is ever wanted (e.g. embedding on the
  4090 box without externalizing it), this decision would need revisiting — CPU-only is
  correct for the *current* deployment shape only.
