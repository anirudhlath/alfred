# Kokoro TTS Backend — Design

- **Status:** Approved
- **Date:** 2026-07-19
- **Author:** Lead Engineer (Claude) + owner
- **Related:** `docs/backlog/medium/voice-model-configurability.md` (realized by this spec), `docs/superpowers/specs/2026-07-15-voice-satellite-design.md`, `docs/voice-satellites.md`

## 1. Motivation

Alfred's text-to-speech runs in-process in the channels process via **Piper** (`core/voice/tts.py`, `PiperTTS`, voice `en_GB-alan-medium`). Its quality is inadequate for an ambient assistant — flat, clipped, obviously synthetic. We want a genuinely **neural** voice.

We first investigated Apple's on-device speech stack (the trigger was `github.com/FI-153/wyoming-apple-speech`). Findings:

- **Apple STT** is feasible but requires a compiled, code-signed **Swift helper** (the good macOS 26 `SpeechAnalyzer` API is Swift-only and unreachable from PyObjC; TCC Speech-Recognition permission fails for a non-`.app` venv `python`).
- **Apple TTS** with real Siri neural voices requires the **private `SiriTTSService.framework`** (reverse-engineered, breaks on OS updates, needs Full Disk Access). The public `AVSpeechSynthesizer` path can't select Siri voices and — verified empirically on the M4 Max — the machine had **zero** enhanced/premium neural voices installed, only the low-quality default set.
- Decisively, **Apple's stack is macOS-only**, which breaks the requirement that **production run on both Linux (RTX 4090) and macOS**.

The Apple investigation is **shelved** and captured as a GitHub Issue so the research is not lost. Instead we adopt **Kokoro-82M**, which satisfies every constraint at once and was validated live on the M4 Max (see §8).

## 2. Goals / Non-Goals

**Goals**

- Replace Piper with **Kokoro-82M** as the default TTS, default voice **`am_michael`**.
- Introduce a **pluggable TTS backend seam** (registry, config-selected) — Kokoro default, **Piper kept as a selectable fallback**. Honors the project's no-hardcoding / registries-over-enums / adapter conventions and realizes `voice-model-configurability.md`.
- **One engine on both prod targets**: the same Kokoro ONNX model runs on macOS (CPU / optional CoreML EP) and on the RTX 4090 (CUDA EP).
- Preserve the existing duck-typed contract (`synthesize(text) -> bytes` WAV) so the satellite, web, and iOS voice paths are **untouched**.
- Auto-download the Kokoro model on first use (matches the existing Piper pattern and the auto-download-models preference).

**Non-Goals**

- Apple STT/TTS integration (separate, shelved — its own GitHub Issue).
- STT changes — faster-whisper (`WhisperSTT`) stays exactly as-is.
- Streaming TTS refactor — Kokoro's `create_stream()` is wired but the pipeline contract stays batch (`bytes` in → `bytes` out) for this change; streaming TTFA is a follow-up.
- Voice cloning / per-channel voice selection.

## 3. Decision Summary

| Axis | Decision |
|---|---|
| Engine | Kokoro-82M via `kokoro-onnx` |
| Default voice | `am_michael` (American male) |
| Default backend | `kokoro` (Piper selectable via `ALFRED_TTS_BACKEND=piper`) |
| License | Apache-2.0 weights + MIT wrapper (clean for public launch) |
| Model precision | fp32 on the server; int8 (~80 MB) documented for the satellite Pis |
| macOS execution provider | CPU EP (CoreML EP optional, flaky — off by default) |
| Linux execution provider | CUDA EP (`onnxruntime-gpu`), CPU fallback |

## 4. Architecture

### 4.1 Backend registry (single source of truth, config-selected)

Today `core/channels/voice_models.py:47` hardcodes the class:

```python
def get_tts() -> Any:
    return _lazy_load("tts", "core.voice.tts", "PiperTTS", "piper-tts not installed")
```

Replace the hardcoded module/class with a **registry** keyed by backend name, so selection is config-driven and extensible (new backend = one registry entry, no edits to callers):

- `core/voice/tts_registry.py` — a lightweight registry mapping backend name → lazy import spec (module + class as **strings**, so registration imports no heavy deps):

  ```python
  # name -> (module, class_name, missing_msg)
  TTS_BACKENDS: dict[str, tuple[str, str, str]] = {
      "kokoro": ("core.voice.tts_kokoro", "KokoroTTS", "kokoro-onnx not installed"),
      "piper":  ("core.voice.tts",        "PiperTTS",  "piper-tts not installed"),
  }
  ```

- A `TTSBackend` typing `Protocol` (`synthesize(self, text: str) -> bytes`) for mypy-strict conformance of every backend.
- `voice_models.get_tts()` reads `config.tts_backend` (default `"kokoro"`), looks up the entry, lazy-imports + instantiates via the existing `_lazy_load` machinery, and **falls back** to the next available backend if the selected one's optional deps are missing (so a Kokoro-less install still gets Piper, and vice-versa). The existing `_voice_load_lock`, `_FAILED` sentinel, caching, and `asyncio.to_thread` off-loop construction (`aget_tts`) are preserved unchanged.

`synthesize_async(tts, text)` and the satellite/web/iOS callers are **unchanged** — they already go through the duck-typed `synthesize()` seam.

### 4.2 KokoroTTS component

`core/voice/tts_kokoro.py` — `KokoroTTS`, mirroring `PiperTTS`:

- `DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "models" / "kokoro"`.
- `__init__`: ensure model files present (auto-download, §4.3), construct `kokoro_onnx.Kokoro(model_path, voices_path)` with the platform-appropriate execution provider (§4.4). Voice/speed from config (`config.kokoro_voice` default `am_michael`, `config.kokoro_speed` default `1.0`).
- `synthesize(self, text: str) -> bytes`: `samples, sr = kokoro.create(text, voice=..., speed=..., lang="en-us")` → wrap the 24 kHz float32 array as a 16-bit PCM **WAV** (via `soundfile`/`wave`) and return `bytes`. Same output shape Piper returns, so downstream `play_wav`/WebSocket audio is unchanged.

### 4.3 Model auto-download

`_ensure_kokoro_models(model_dir)` downloads the ONNX model + voices pack from Hugging Face on first use (mirrors `core/voice/tts.py::_download_model` using `huggingface_hub`), into `core/voice/models/kokoro/`:

- `kokoro-v1.0.onnx` (fp32; int8 variant documented for satellites)
- `voices-v1.0.bin`

Source repo pinned in implementation (an ONNX-packaged Kokoro repo, e.g. `onnx-community/Kokoro-82M-v1.0-ONNX`); `models/` is already gitignored.

### 4.4 Platform-aware execution provider

Kokoro is a single ONNX graph; only the execution provider differs per host:

- **macOS (M4 Max):** default **CPU EP** (`onnxruntime`). Measured ~0.11 RTF — 9× real-time — reliable. CoreML EP is available but silently converts to FP16 / falls back on unsupported ops (per research), so it is **off by default** and gated behind an explicit opt-in.
- **Linux (RTX 4090):** **CUDA EP** (`onnxruntime-gpu`), CPU fallback.

Selection: `config.kokoro_onnx_provider` (env `KOKORO_ONNX_PROVIDER`, default `"auto"`). `auto` picks CUDA if `onnxruntime-gpu`'s CUDA provider is available, else CPU. `onnxruntime` and `onnxruntime-gpu` are **mutually exclusive** packages — the `voice` extra installs `onnxruntime` (Mac/dev default); the 4090 deployment installs `onnxruntime-gpu` (documented; optionally a `voice-cuda` extra). kokoro-onnx honors provider selection via env/session.

### 4.5 espeak-ng phonemizer wiring (known risk — carry a plan)

Kokoro's g2p uses `misaki` → `phonemizer-fork` → `espeak-ng`. During the spike, **`phonemizer-fork` 3.3.2 + `espeakng_loader` mis-resolved espeak's data directory** to `site-packages/` itself (the `phontab: No such file or directory` error), independent of `ESPEAK_DATA_PATH` / `PHONEMIZER_ESPEAK_LIBRARY` overrides. The spike worked around it by symlinking espeak's data into the resolved location — **not acceptable for the real integration**.

Plan (finalize during implementation with a test on **both** macOS and Linux):

1. Pin a **known-good `phonemizer-fork` + `espeakng_loader` combination** (espeakng_loader bundles complete cross-platform data — no manual system dep), verified by a phonemization smoke test.
2. If pinning doesn't resolve it, pass an explicit `EspeakConfig(lib_path=..., data_path=...)` to `Kokoro(...)`, or depend on a system `espeak-ng` (`brew install espeak-ng` / `apt install espeak-ng`) with the library/data paths set explicitly.
3. Add a **CI smoke test** (macOS + Linux jobs) that synthesizes one short utterance, so this never regresses silently or bites the 4090 deploy.

## 5. Files

**New**
- `core/voice/tts_kokoro.py` — `KokoroTTS` + `_ensure_kokoro_models`.
- `core/voice/tts_registry.py` — `TTS_BACKENDS` map + `TTSBackend` Protocol.
- `docs/voice.md` — TTS subsystem doc (backends, selection, model download, EP selection, espeak wiring), per the "document new features" rule.
- `tests/voice/test_tts_kokoro.py`, `tests/voice/test_tts_registry.py`.

**Modified**
- `core/channels/voice_models.py` — `get_tts()` reads the registry + config; fallback logic. Signatures unchanged.
- `shared/config.py` + `.env.example` — new settings (§6).
- `pyproject.toml` — `voice` extra deps (§7).
- `docs/architecture.md` — note the pluggable TTS backend in the voice/channels description.
- `docs/PRD.md` — Capability Catalog TTS row (neural voice) + bump "statuses current as of".
- `core/CLAUDE.md` + root `CLAUDE.md` gotchas — replace "Piper synthesizes"/"Piper auto-downloads" references with the backend-registry reality (Kokoro default).
- `core/notifications/adapters/satellite.py` doc/comment — "Piper-synthesized" → backend-agnostic.

## 6. Configuration

| Setting (`shared/config.py`) | Env | Default | Purpose |
|---|---|---|---|
| `tts_backend` | `ALFRED_TTS_BACKEND` | `kokoro` | Select TTS backend (`kokoro`\|`piper`) |
| `kokoro_voice` | `KOKORO_VOICE` | `am_michael` | Kokoro voice id |
| `kokoro_speed` | `KOKORO_SPEED` | `1.0` | Speech rate |
| `kokoro_onnx_provider` | `KOKORO_ONNX_PROVIDER` | `auto` | `auto`\|`cpu`\|`cuda`\|`coreml` |

## 7. Dependencies (`voice` extra)

Add: `kokoro-onnx`, `misaki[en]`, `espeakng_loader` (pinned per §4.5), `soundfile`, `onnxruntime` (CPU default). Keep `piper-tts` (fallback backend). Document `onnxruntime-gpu` for the CUDA deployment (mutually exclusive with `onnxruntime`). All pins resolved on Python 3.13.

## 8. Evidence (spike, M4 Max, macOS 26, same sentence)

| Engine | Device | Short reply (~3.4s audio) | Long (~9s audio) | RTF |
|---|---|---|---|---|
| **Kokoro `am_michael`** | CPU (ONNX) | **0.40 s** | 1.01 s | **0.11–0.15** |
| Piper `en_GB-alan` (current) | CPU | — | — | baseline (quality rejected) |
| Chatterbox Turbo | MPS (warm) | 4.2–7.3 s | 6.6–9.3 s | 0.83–2.4 |

Kokoro has ~zero fixed per-call overhead (short RTF ≈ long RTF), so short assistant replies synthesize in ~0.4 s on CPU alone; on the 4090's CUDA EP, ~3× faster still. Chatterbox (the next-best open option) is ~10–18× slower on short replies on Apple Silicon and breaks the single-engine property (torch/MPS vs torch/CUDA), so it was rejected as the live voice.

## 9. Testing

- `KokoroTTS.synthesize()` returns non-empty, valid 16-bit PCM WAV bytes (gated/skip if the `voice` extra is absent, per existing voice test conventions).
- Registry: `ALFRED_TTS_BACKEND` selects the right class; missing-deps fallback picks the other backend; unknown name errors clearly.
- `TTSBackend` Protocol conformance under `mypy --strict` for both backends.
- espeak phonemization smoke test (CI, macOS + Linux).
- Full suite + `ruff` + `mypy --strict` green before PR.

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| espeak-ng data-path resolution bug (§4.5) | Pin known-good versions; explicit `EspeakConfig`/system espeak fallback; CI smoke on both OSes |
| `onnxruntime` vs `onnxruntime-gpu` conflict | CPU default in extra; document/`voice-cuda` extra for the 4090; runtime provider auto-detect |
| CoreML EP instability on macOS 26 | Default to CPU EP; CoreML gated behind explicit opt-in |
| Model download availability/size | HF hub with pinned repo; int8 (~80 MB) documented for satellites |
| Behavior change for existing Piper users | `ALFRED_TTS_BACKEND=piper` restores Piper; documented in `docs/voice.md` |

## 11. Rollout

Default flips to Kokoro; first run auto-downloads the model. Piper remains one env var away. No data migration. Satellite + web + iOS voice inherit Kokoro automatically via the unchanged `synthesize()` seam.

## 12. Future work

- Streaming TTS via `create_stream()` (per-sentence) to cut time-to-first-audio end-to-end.
- Kokoro int8 backend option for the `alfred-satellite` Pis (footprint).
- Per-channel voice selection.
- Apple STT (Swift helper) — tracked separately (shelved GitHub Issue).
