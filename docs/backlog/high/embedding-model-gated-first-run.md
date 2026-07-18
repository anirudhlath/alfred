# Fix First-Run 401 from Gated EmbeddingGemma Default Model

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** high
**Severity (audit):** high
**Source:** Public-release readiness audit 2026-07-18 (findings #5, #18)

## Summary
The system-wide default embedding model is `google/embeddinggemma-300m`, a license-gated HuggingFace model: downloading it requires a logged-in HF account that has accepted the Gemma Terms of Use plus an authenticated token. The codebase has no HF token handling and no docs mention HuggingFace, so a fresh clone's first run of memory features fails with an access error, and the Gemma Terms of Use / Prohibited Use Policy are never surfaced to the user. Because `anirudhlath/alfred` is already public (v0.1.0 shipped 2026-07-16), this is a live first-run breakage on the published repo, not a pre-release cleanup — every stranger who installs the `memory` extra and exercises memory features hits it. Scope limit: the failure only affects environments where the `memory` extra is installed and memory features run.

## Context / Motivation
- `core/memory/embedding_provider.py:34` defaults `SentenceTransformerProvider` to `"google/embeddinggemma-300m"`.
- `shared/config.py:42,107` make it the system-wide default via the `EMBEDDING_MODEL` env var (`:107` sets the default value).
- `core/warmup.py` loads this model at startup in model-holding services when the `memory` extra is installed.
- EmbeddingGemma is a gated HF model: users must log in to HuggingFace, click "Acknowledge license" to accept the Gemma Terms of Use, and use an authenticated HF token to download (confirmed via huggingface.co/google/embeddinggemma-300m and its Troubleshooting discussion #10). The codebase has NO HF token handling (grep for `hf_token`).
- `README.md` never mentions HuggingFace at all; `EMBEDDING_MODEL`/`EMBEDDING_DIM` are absent from `.env.example`, so users cannot discover the override.
- Consequence: a fresh clone's first run of memory features fails with an HF access error, with no in-repo guidance on how to authenticate or override.

## Acceptance Criteria
- [ ] `README.md` Prerequisites/Setup documents the gated-model requirement: accept the Gemma Terms of Use on HuggingFace and authenticate (`hf auth login` / `huggingface-cli login`, or set `HF_TOKEN`) before first run of memory features.
- [ ] `README.md` links the Gemma Terms of Use and Prohibited Use Policy.
- [ ] `SentenceTransformerProvider` supports passing an HF token (e.g. via `HF_TOKEN`) through to the model download.
- [ ] `EMBEDDING_MODEL` and `EMBEDDING_DIM` are added to `.env.example` so the override is discoverable.
- [ ] Evaluate switching the default (`core/memory/embedding_provider.py:34` and `shared/config.py:107`) to an ungated Apache-2.0/MIT embedding model (e.g. `BAAI/bge-small-en-v1.5` or `all-MiniLM-L6-v2`); if the gated default is kept, the token setup above is mandatory.
