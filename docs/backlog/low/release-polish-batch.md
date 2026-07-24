# Release Polish Batch: Bind Address, Model License Docs, AGPL Notice, web README, pytest Warning

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** low
**Severity (audit):** low
**Source:** Public-release readiness audit 2026-07-18 (findings #48, #49, #50, #52, #53)

## Summary
Five low-severity polish items surfaced by the release-readiness audit: a default `0.0.0.0`
bind plus two unauthenticated integration read endpoints, missing third-party model license
documentation, a stock AGPL `LICENSE` with no project notice line, an unmodified Vite-template
`web/README.md`, and a cosmetic "Event loop is closed" pytest warning. Because `anirudhlath/alfred`
is **already public** (v0.1.0 shipped 2026-07-16), these are post-exposure cleanup rather than
pre-release gating — none is a live secret leak or auth-bypass, but each makes the published repo
tidier, more legally coherent, and easier for a stranger to build. The bind/endpoint item is the
only one with any security surface, and it discloses no credential values.

## Context / Motivation

**#48 — bind `0.0.0.0` + unauthenticated integration reads** (`core/channels/__main__.py`, `core/channels/web_server.py`)
The channels process runs uvicorn on `host="0.0.0.0"` unconditionally (`__main__.py:44`), so port
8081 is reachable from any host that can route to it — not just localhost/Tailscale. Two endpoints
have no auth gate at all: `GET /api/integrations` (`web_server.py:465-485`), which discloses the set
of integrations and, per field, whether each credential is configured (a reconnaissance aid), and
`GET /api/integrations/{name}/status` (`web_server.py:544-559`), which triggers an outbound
`health_check` on demand. Scope limit: **no credential values are exposed** by either endpoint —
only presence/config state — which is why this rates low.

**#49 — no third-party ML model license docs** (`core/voice/tts_kokoro.py`, `core/voice/tts.py`, `shared/config.py`, `README.md`, `core/voice/stt.py`)
The repo auto-downloads several third-party models with no NOTICE/docs page recording their
licenses. The Kokoro-82M default voice (`core/voice/tts_kokoro.py`, ~353 MB auto-downloaded from
`huggingface.co/fastrtc/kokoro-onnx`) is Apache-2.0 weights + MIT wrapper — clean, but should be
recorded in the same NOTICE/docs page. The Piper TTS fallback voice `en_GB-alan-medium`
(`core/voice/tts.py`, auto-downloaded
from `huggingface.co/rhasspy/piper-voices`) is trained on MycroftAI mimic3-voices `en_UK/apope`
(Alan Pope) data, which is CC BY-SA 4.0. The `piper-voices` repo labels itself MIT, but the dataset
heritage is a known open question (rhasspy/piper issue #253, "About mimic3-voices dataset license"),
so this is **not a confirmed infringement** — at minimum, attribution is good practice. Separately,
the `OLLAMA_MODEL` default in `shared/config.py` and the default SLM named in the README disagree
(the finding flags the Llama 3 default as conflicting with the README).

**#50 — stock AGPL LICENSE has no project notice line** (`LICENSE`, `README.md`)
`alfred/LICENSE` is the verbatim GNU AGPL-3.0 text (its copyright line is the FSF's own, for the
license document itself). The project's copyright claim exists only in `README.md`
("AGPL-3.0-or-later © 2025–2026 Anirudh Lath"). This is legally workable but weaker than the AGPL's
own "How to Apply These Terms" guidance, which recommends a copyright + license notice. Scope limit:
the README already discloses that the repo was "Briefly published under MIT during initial release
prep (July 13–15, 2026)" — **no action is required on the MIT-window history**.

**#52 — web/README.md is Vite boilerplate** (`web/README.md`, `docs/web-frontend.md`)
The tracked `web/README.md` is the stock "React + TypeScript + Vite" template README (ESLint config
advice, plugin comparison) and says nothing about Alfred's SPA, its build being required for the
runner to serve a UI, or the dev proxy to `:8081` — all of which is properly documented in
`docs/web-frontend.md` instead.

**#53 — pytest "Event loop is closed" warning** (`tests/`)
The full suite passes (947 passed, 0 failed, 12.62s with `HF_HUB_OFFLINE=1`), but among its 12
warnings is a `PytestUnhandledThreadExceptionWarning`: a background thread calls
`call_soon_threadsafe` on a closed asyncio event loop, raising `RuntimeError: Event loop is closed`
after teardown. Cosmetic today, but this class of warning tends to become a flake and looks untidy
in a public repo's CI logs.

## Acceptance Criteria
- [ ] Channels process defaults its bind host to `127.0.0.1` (configurable, e.g. via env/config) instead of unconditional `0.0.0.0` in `core/channels/__main__.py`.
- [ ] `GET /api/integrations` and `GET /api/integrations/{name}/status` enforce `require_trusted_network`/`require_authenticated`, matching the existing credential-write endpoints.
- [ ] A `docs/model-licenses.md` (or README section) lists each auto-downloaded model and its license: EmbeddingGemma (Gemma ToU, gated), Piper `en_GB-alan` (MIT model / CC BY-SA-heritage dataset — credit Alan Pope / MycroftAI), Whisper large-v3-turbo (MIT), Silero VAD (MIT), and the default SLM.
- [ ] The `OLLAMA_MODEL` default in `shared/config.py` is reconciled with the default SLM named in `README.md` (no remaining conflict).
- [ ] `LICENSE` (or a new `NOTICE` file) carries a one-line project copyright/AGPL notice block per the AGPL "How to Apply These Terms" appendix (e.g. "Alfred — Ambient Multi-Agent System, Copyright (C) 2025-2026 Anirudh Lath …"); no change to the disclosed MIT-window history.
- [ ] `web/README.md` is replaced with a short README describing the Alfred SPA, the `npm run build` requirement for the runner to serve a UI, and a pointer to `docs/web-frontend.md`.
- [ ] The background thread that calls `call_soon_threadsafe` on a closed loop is joined/cancelled in its owning fixture teardown, so the pytest suite no longer emits the `PytestUnhandledThreadExceptionWarning`; optionally add a warnings-filter gate to prevent regression.
