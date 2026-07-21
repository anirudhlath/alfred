# Kokoro TTS Backend — Design

- **Status:** Approved
- **Date:** 2026-07-19 (revised 2026-07-20)
- **Author:** Lead Engineer (Claude) + owner
- **Related:** `docs/backlog/medium/voice-model-configurability.md` (realized by this spec), `docs/superpowers/specs/2026-07-15-voice-satellite-design.md`, `docs/voice-satellites.md`

## 1. Motivation

Alfred's text-to-speech runs in-process in the channels process via **Piper** (`core/voice/tts.py`, `PiperTTS`, voice `en_GB-alan-medium`). Its quality is inadequate for an ambient assistant — flat, clipped, obviously synthetic. We want a genuinely **neural** voice.

We first investigated Apple's on-device speech stack (the trigger was `github.com/FI-153/wyoming-apple-speech`). Findings:

- **Apple STT** is feasible but requires a compiled, code-signed **Swift helper** (the good macOS 26 `SpeechAnalyzer` API is Swift-only and unreachable from PyObjC; TCC Speech-Recognition permission fails for a non-`.app` venv `python`).
- **Apple TTS** with real Siri neural voices requires the **private `SiriTTSService.framework`** (reverse-engineered, breaks on OS updates, needs Full Disk Access). The public `AVSpeechSynthesizer` path can't select Siri voices and — verified empirically on the M4 Max — the machine had **zero** enhanced/premium neural voices installed, only the low-quality default set.
- Decisively, **Apple's stack is macOS-only**, which breaks the requirement that **production run on both Linux (RTX 4090) and macOS**.

The Apple investigation is **shelved** and captured as a GitHub Issue (#154) so the research is not lost. Instead we adopt **Kokoro-82M**, which satisfies every constraint at once and was validated live on the M4 Max (see §8).

## 2. Goals / Non-Goals

**Goals**

- Replace Piper with **Kokoro-82M** (via `kokoro-onnx`) as the default TTS, default voice **`am_michael`**.
- Introduce a **pluggable TTS backend seam** — an **abstract base class (ABC) port** plus a config-selected registry — with Kokoro the default and **Piper kept as a selectable fallback**. Honors the project's adapter / no-hardcoding / registries-over-enums conventions and realizes `voice-model-configurability.md`. The seam is designed so a future Apple-Silicon **MLX** adapter drops in with no caller changes (§12).
- **One engine on both prod targets**: the same Kokoro ONNX model runs on macOS (CPU EP) and on the RTX 4090 (CUDA EP).
- Preserve the existing duck-typed contract (`synthesize(text) -> bytes` WAV) so the satellite, web, and iOS voice paths are **untouched**.
- Auto-download models from the **Hugging Face Hub** on first use, for **both** backends (Kokoro and the refactored Piper), pinned to explicit revisions.

**Non-Goals**

- Apple STT/TTS integration (separate, shelved — GitHub Issue #154).
- STT changes — faster-whisper (`WhisperSTT`) stays exactly as-is.
- Streaming TTS refactor — Kokoro's `create_stream()` is wired but the pipeline contract stays batch (`bytes` in → `bytes` out) for this change; streaming TTFA is a follow-up.
- Voice cloning / per-channel voice selection.
- The **MLX Mac adapter itself** — benchmarked and deferred to a fast-follow backlog ticket (§12); this change only makes the seam MLX-ready.

## 3. Decision Summary

| Axis | Decision |
|---|---|
| Engine | Kokoro-82M via `kokoro-onnx` |
| Default voice | `am_michael` (American male) |
| Backend seam | **`TTSBackend` ABC** (abstract `synthesize`) + config-selected registry; adapters subclass the ABC |
| Default backend | `kokoro` (Piper selectable via `ALFRED_TTS_BACKEND=piper`) |
| Model download | **Hugging Face Hub** (`hf_hub_download`, pinned revision) for **both** backends |
| Kokoro model source | `fastrtc/kokoro-onnx` @ `8d07950c9b6c87ce6809e9bba7bd494336217c2a` (`kokoro-v1.0.onnx` + `voices-v1.0.bin`, MIT) |
| Piper model source | `rhasspy/piper-voices` @ `5b44ec7bab7c5822cfec48fbd5aa99db71a823d6` |
| License | Apache-2.0 weights + MIT wrappers (clean for public launch) |
| Model precision | fp32 on the server; int8 (~80 MB) documented for the satellite Pis |
| macOS execution provider | CPU EP (CoreML EP optional, flaky — off by default) |
| Linux execution provider | CUDA EP (`onnxruntime-gpu`), CPU fallback |
| Phonemizer | `phonemizer-fork` + `espeakng_loader` (explicit `EspeakConfig`) — **no** `misaki`/spaCy/torch |

## 4. Architecture

### 4.1 Backend ABC port + registry (clean, decoupled, config-selected)

Today `core/channels/voice_models.py:47` hardcodes the class:

```python
def get_tts() -> Any:
    return _lazy_load("tts", "core.voice.tts", "PiperTTS", "piper-tts not installed")
```

Replace this with an **adapter architecture**: an abstract port that the rest of the code depends on, concrete adapters that implement it, and a registry that maps a config name to an adapter (new backend = one registry entry + one adapter class, no caller edits).

- **Port** — `core/voice/tts_backend.py`:

  ```python
  from abc import ABC, abstractmethod

  class TTSBackend(ABC):
      """Port every TTS adapter implements. The channels process depends only on this."""

      @abstractmethod
      def synthesize(self, text: str) -> bytes:
          """Synthesize text to 16-bit PCM mono WAV bytes."""
  ```

  This module has **zero heavy deps** — importing the port pulls in no ONNX/torch/HF.

- **Adapters** — `KokoroTTS(TTSBackend)` (`tts_kokoro.py`) and `PiperTTS(TTSBackend)` (`tts.py`, refactored to subclass the port). Each implements `synthesize`.

- **Registry** — `core/voice/tts_registry.py` maps a backend name → lazy import spec (module + class as **strings**, so registration imports no heavy deps):

  ```python
  # name -> (module, class_name, missing_dep_msg)
  TTS_BACKENDS: dict[str, tuple[str, str, str]] = {
      "kokoro": ("core.voice.tts_kokoro", "KokoroTTS", "kokoro-onnx not installed"),
      "piper":  ("core.voice.tts",        "PiperTTS",  "piper-tts not installed"),
  }
  DEFAULT_TTS_BACKEND = "kokoro"
  ```
  plus `resolve_backend_order(selected) -> list[str]` (selected first, then the rest as fallbacks; unknown name warns → default order).

- **Selection** — `voice_models.get_tts() -> TTSBackend | None` reads `config.tts_backend` (default `"kokoro"`), tries that adapter first, and **falls back** to the next registered adapter whose optional deps are installed (Kokoro-less install still gets Piper, and vice-versa). The existing `_voice_load_lock`, `_FAILED` sentinel, caching, and `asyncio.to_thread` off-loop construction (`aget_tts`) are preserved.

`synthesize_async(tts, text)` and the satellite/web/iOS callers are **unchanged** — they already go through the `synthesize()` seam, now typed to the ABC.

### 4.2 KokoroTTS adapter

`core/voice/tts_kokoro.py` — `KokoroTTS(TTSBackend)`:

- `__init__`: resolve model files via the shared HF helper (§4.3), set the platform-appropriate execution provider (§4.4), construct `kokoro_onnx.Kokoro(model_path, voices_path, espeak_config=…)` with an explicit `EspeakConfig` (§4.5). Voice/speed/provider from config (`kokoro_voice` default `am_michael`, `kokoro_speed` default `1.0`, `kokoro_onnx_provider` default `auto`), overridable via constructor args for tests.
- `synthesize(self, text: str) -> bytes`: `samples, sr = kokoro.create(text, voice=…, speed=…, lang="en-us")` → wrap the 24 kHz float32 array as 16-bit PCM **WAV** via stdlib `wave` + `numpy` (no `soundfile` dep) and return `bytes`. Same output shape Piper returns, so downstream `play_wav`/WebSocket audio is unchanged.

### 4.3 Model auto-download (Hugging Face Hub, both backends)

`core/voice/hf_models.py` — a shared, DRY helper both adapters use:

```python
def ensure_model(repo_id: str, filename: str, revision: str) -> Path:
    """Download (cached) a model file from the HF Hub, pinned to a revision."""
    from huggingface_hub import hf_hub_download
    return Path(hf_hub_download(repo_id=repo_id, filename=filename, revision=revision))
```

`hf_hub_download` manages the local cache (`~/.cache/huggingface/hub`), resume, and integrity — cleaner than hand-rolled `urllib`. Both adapters pass the returned path straight to their engine.

- **Kokoro** — `fastrtc/kokoro-onnx` @ pinned revision: `kokoro-v1.0.onnx` (~325 MB, fp32) + `voices-v1.0.bin` (~28 MB). int8 variant documented for satellites (future).
- **Piper** — refactor `PiperTTS._download_model` from `urllib` to `ensure_model` against `rhasspy/piper-voices` @ pinned revision (path `en/en_GB/alan/medium/en_GB-alan-medium.onnx` + `.onnx.json`), reusing the existing voice→path mapping.

### 4.4 Platform-aware execution provider

Kokoro is a single ONNX graph; only the execution provider differs per host. `KokoroTTS._resolve_provider()` sets the `ONNX_PROVIDER` env var kokoro-onnx honours (kokoro's own gpu auto-detect is unreliable — it looks up the hyphenated `onnxruntime-gpu` import spec, which never resolves):

- **macOS (M4 Max):** default **CPU EP** (`onnxruntime`). Measured ~0.11–0.19 RTF — reliable. CoreML EP silently converts to FP16 / falls back on unsupported ops, so it is **off by default** (opt-in via `KOKORO_ONNX_PROVIDER=coreml`).
- **Linux (RTX 4090):** **CUDA EP** (`onnxruntime-gpu`), CPU fallback.

`config.kokoro_onnx_provider` (env `KOKORO_ONNX_PROVIDER`, default `"auto"`): `auto` → `CUDAExecutionProvider` when `onnxruntime.get_available_providers()` exposes it, else CPU. `onnxruntime` and `onnxruntime-gpu` are **mutually exclusive** — the `voice` extra installs `onnxruntime` (Mac/dev default); the 4090 swaps in `onnxruntime-gpu` (`kokoro-onnx[gpu]` exists but layers gpu on top of the base onnxruntime, so the deploy step is `uv pip uninstall onnxruntime && uv pip install onnxruntime-gpu`).

### 4.5 espeak-ng phonemizer wiring (RESOLVED)

Kokoro's g2p is `phonemizer-fork → espeak-ng` (kokoro-onnx uses phonemizer **directly** — no `misaki`/spaCy/torch). During the spike, `phontab: No such file or directory` appeared. Root cause, since **verified**: ambient/compiled-in espeak data-path resolution, **not** a real phonemizer bug. On py3.13/macOS 26 with `kokoro-onnx==0.5.0` + `espeakng_loader==0.2.4` + `phonemizer-fork==3.3.2`, passing an **explicit** `EspeakConfig(lib_path=espeakng_loader.get_library_path(), data_path=espeakng_loader.get_data_path())` to `Kokoro(...)` and setting **no** conflicting espeak env vars makes phonemization deterministic and `kokoro.create()` clean (RTF 0.188, valid float32 @ 24 kHz). A CI smoke test (macOS + Linux) guards against regression.

## 5. Files

**New**
- `core/voice/tts_backend.py` — `TTSBackend` ABC port.
- `core/voice/tts_kokoro.py` — `KokoroTTS(TTSBackend)` + provider/espeak helpers.
- `core/voice/tts_registry.py` — `TTS_BACKENDS` map + `resolve_backend_order`.
- `core/voice/hf_models.py` — shared `ensure_model()` HF-Hub downloader.
- `docs/voice.md` — TTS subsystem doc (ABC seam, backends, HF download, EP selection, espeak wiring).
- `tests/core/voice/test_tts_kokoro.py`, `test_tts_registry.py`, `test_tts_backend.py`, `test_espeak_smoke.py`; `tests/core/channels/test_voice_models_tts.py`; `tests/shared/test_config_tts.py`.
- `.github/workflows/voice-smoke.yml` — non-gating macOS+Linux phonemization smoke.
- `docs/backlog/medium/kokoro-mlx-mac-adapter.md` — MLX fast-follow ticket (§12).

**Modified**
- `core/channels/voice_models.py` — `get_tts()` reads the registry + config; returns `TTSBackend | None`; fallback logic. Signatures unchanged.
- `core/voice/tts.py` — `PiperTTS(TTSBackend)` (subclass the ABC) + HF-Hub download.
- `shared/config.py` + `.env.example` — new settings (§6).
- `pyproject.toml` — `voice` extra deps (§7).
- `docs/architecture.md` — pluggable TTS backend in the voice/channels description.
- `docs/PRD.md` — Capability Catalog voice row + "statuses current as of" date.
- `core/CLAUDE.md` + root `CLAUDE.md` gotchas — Piper references → backend-registry reality (Kokoro default).
- `core/notifications/adapters/satellite.py` — "Piper-synthesized" → backend-agnostic docstring.

## 6. Configuration

| Setting (`shared/config.py`) | Env | Default | Purpose |
|---|---|---|---|
| `tts_backend` | `ALFRED_TTS_BACKEND` | `kokoro` | Select TTS backend (`kokoro`\|`piper`) |
| `kokoro_voice` | `KOKORO_VOICE` | `am_michael` | Kokoro voice id |
| `kokoro_speed` | `KOKORO_SPEED` | `1.0` | Speech rate |
| `kokoro_onnx_provider` | `KOKORO_ONNX_PROVIDER` | `auto` | `auto`\|`cpu`\|`cuda`\|`coreml` |

## 7. Dependencies (`voice` extra)

Add: `kokoro-onnx>=0.5,<0.6`, `espeakng_loader>=0.2.4,<0.3`, `phonemizer-fork>=3.3,<3.4`, `onnxruntime>=1.27`, `huggingface_hub>=0.24`. Keep `piper-tts>=1.2` (fallback backend, now HF-Hub-downloaded), `faster-whisper`, `pysilero-vad`, `speechbrain`, `numpy`. **Do not** add `misaki`/`soundfile` — kokoro-onnx phonemizes via `phonemizer-fork` directly, and WAV wrapping uses stdlib `wave`. Document `onnxruntime-gpu` for the CUDA deployment (mutually exclusive with `onnxruntime`). All pins resolved on Python 3.13.

## 8. Evidence (spike, M4 Max, macOS 26, same sentences, warm)

| Engine | Device | Short reply RTF | Long RTF | Short reply synth | Notes |
|---|---|---|---|---|---|
| **Kokoro-ONNX `am_michael`** | CPU (py3.13) | **0.11–0.19** | 0.11–0.15 | **~0.40 s** | **chosen** — cross-platform, 3.13, lightweight |
| Kokoro-MLX `am_michael` | GPU/MLX (py3.12) | 0.019 | 0.02 | 0.076 s | 5–8× faster but Mac-only, excludes 3.13, torch+spaCy, alpha → deferred (§12) |
| Piper `en_GB-alan` (current) | CPU | — | — | — | quality rejected |
| Chatterbox Turbo | MPS (warm) | 0.83–2.4 | 0.83–1.24 | 4.2–7.3 s | ~10–18× slower; rejected |

Kokoro-ONNX has ~zero fixed per-call overhead (short RTF ≈ long RTF); short assistant replies synthesize in ~0.4 s on Mac CPU alone, ~3× faster on the 4090's CUDA EP — comfortably inside the sub-500 ms budget. Kokoro-MLX is materially faster on Apple Silicon but cannot be the single cross-platform engine and carries heavy integration cost (see §12), so it is a fast-follow, not part of this change.

## 9. Testing

- `KokoroTTS.synthesize()` returns non-empty, valid 16-bit PCM WAV bytes (mock-based; heavy real-synth test gated/skip if model absent).
- `TTSBackend` ABC: both adapters subclass it; `get_tts()` returns a `TTSBackend`.
- Registry: `ALFRED_TTS_BACKEND` selects the right adapter; missing-deps fallback picks the other backend; unknown name warns → default order.
- HF download helper: pinned repo/revision, returns a path (mocked).
- espeak phonemization smoke test (CI, macOS + Linux, no model download).
- `mypy --strict` clean; full suite + `ruff` green before PR.

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| espeak-ng data-path resolution (§4.5) | Explicit `EspeakConfig`; no conflicting env vars; CI smoke on both OSes — verified resolved |
| `onnxruntime` vs `onnxruntime-gpu` conflict | CPU default in extra; documented swap for the 4090; runtime provider auto-detect |
| CoreML EP instability on macOS 26 | Default to CPU EP; CoreML gated behind explicit opt-in |
| Model download availability | Pinned HF repos + revisions; `hf_hub_download` caching/resume; int8 documented for satellites |
| HF repo (`fastrtc/kokoro-onnx`) disappearing | Pinned revision; fallback repos exist (`onnx-community`, `ApacheOne`); Piper fallback still ships |
| Behavior change for existing Piper users | `ALFRED_TTS_BACKEND=piper` restores Piper; documented in `docs/voice.md` |

## 11. Rollout

Default flips to Kokoro; first run auto-downloads the model from HF. Piper remains one env var away. No data migration. Satellite + web + iOS voice inherit Kokoro automatically via the unchanged `synthesize()` seam.

## 12. Future work

- **Kokoro-MLX Mac adapter (fast-follow, backlogged).** Benchmarked on the M4 Max: ~5–8× faster than ONNX CPU (short reply 0.076 s / RTF 0.019 vs 0.4 s / RTF 0.15) with native 48 kHz output. Deferred because: (a) `kokoro-mlx` sets `requires-python <3.13`, so it needs **vendoring** of its MIT pure-Python inference to run on Alfred's 3.13; (b) it drags `torch` + spaCy + `en_core_web_sm` via `misaki[en]`; (c) its espeak init resists the standard fix (needs an `espeakng_loader` monkeypatch to a working espeak); (d) alpha (v0.1.2, single maintainer). The ABC port makes it a drop-in `KokoroMLXTTS(TTSBackend)` adapter (Mac-only, auto-selected on Apple Silicon) once those are settled. Tracked in `docs/backlog/medium/kokoro-mlx-mac-adapter.md`.
- Streaming TTS via `create_stream()` (per-sentence) to cut time-to-first-audio.
- Kokoro int8 backend option for the `alfred-satellite` Pis (footprint).
- Per-channel voice selection.
- Apple STT (Swift helper) — tracked separately (shelved GitHub Issue #154).
