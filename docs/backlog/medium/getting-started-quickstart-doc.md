# Getting-Started Quickstart Doc (zero-to-Alfred for the current dev path)

## Summary

Write `docs/getting-started.md`: a single linear guide taking a capable
self-hoster from clone to their first conversation with Alfred, on the current
(dev) path. Today this journey exists only as fragments across README Setup,
internal CLAUDE.md files, and session memory.

## Context / Motivation

Launch-readiness assessment (2026-07-18): setup is engineer-grade and takes
1–2 hours with insider knowledge. The README covers install commands but not
the journey: brew services (redis-stack, mosquitto), Ollama model pull, `.env`
keys (which are documented only internally), `scripts/dev-up.sh`, the runner,
first-run onboarding wizard, and what "working" looks like. The in-product
onboarding is already good — the gap is everything before the browser opens.

## Acceptance Criteria

- [ ] `docs/getting-started.md` covers: prerequisites with versions and WHY
      (Redis Stack not vanilla, GPU expectations for the SLM + embeddings),
      install (`uv sync` extras explained), `.env` setup with a complete
      annotated `.env.example` cross-reference (OpenRouter key, model choice),
      infrastructure start, runner start, onboarding wizard walkthrough,
      "verify it works" checks (health endpoint, first chat message).
- [ ] Troubleshooting section for the five most common failures (Redis Stack
      missing modules, Ollama model absent, port 8081 busy, `web/dist` not
      built, missing `.env` keys).
- [ ] A fresh reader on a clean Mac can reach a working chat without touching
      CLAUDE.md or asking questions (verified once by following it verbatim).
- [ ] README Setup section links to it instead of duplicating steps.
- [ ] Revisit after HA Plan 2 lands to add the "connect your Home Assistant"
      step (URL + token via Settings).
