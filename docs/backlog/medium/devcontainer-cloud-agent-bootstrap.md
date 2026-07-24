# Devcontainer / Environment Bootstrap for Cloud Agents

**Epic:** [GitHub Chores](../epics/github-chores.md)
**Priority:** medium
**Source:** GitHub agent-enablement round-up 2026-07-18

## Summary
Alfred's dev environment needs redis-stack, mosquitto, Python 3.13 (system may be 3.14),
and Node — today that knowledge lives in CLAUDE.md prose (native macOS dev now runs
Homebrew-managed Redis/Mosquitto directly, with no wrapper script; `uv run alfredctl up`
is the containerized alternative for macOS/Linux). Cloud-based agents (Claude Code web
sessions, Codespaces,
`@claude` Actions runners) get a bare Linux box and must rediscover the environment by
trial and error. A `.devcontainer/` (Linux-native, container-based services) gives every
cloud agent a reproducible environment that can run the full system, not just the mocked
test suite.

## Context / Motivation
- The pytest suite passes without live services (fakes/mocks via root `conftest.py`, CI
  proves it) — so plain CI doesn't need this. The gap is **live-system work**: running
  `python -m runner`, smoke tests, evals, or reproducing integration bugs from a cloud
  session.
- Shape: `devcontainer.json` with a docker-compose defining `redis-stack` and
  `eclipse-mosquitto` services; base image with Python 3.13 (uv-managed) + Node 22;
  `postCreateCommand`: `uv venv --python 3.13 && uv sync --all-extras && (cd web && npm ci)`.
- Reuse/align with the existing prod `docker-compose.yml` service definitions where
  possible (but heed [lock-down-compose-redis-mqtt](../high/lock-down-compose-redis-mqtt.md)
  — the devcontainer compose must not inherit its published-ports/no-auth defaults
  verbatim; inside the devcontainer network, unpublished ports are fine).
- Native macOS dev now runs Homebrew-managed Redis/Mosquitto directly (the old
  `scripts/dev-up.sh` wrapper was retired in Part 2 of containerization); `uv run
  alfredctl up` is the containerized path for macOS/Linux; the devcontainer remains the
  dedicated Linux/cloud edit-test-loop path. CLAUDE.md should name all three and say
  which applies where.
- Model downloads (Whisper/Piper/EmbeddingGemma) are auto-fetched on first use and the
  embedding default is gated
  ([embedding-model-gated-first-run](../high/embedding-model-gated-first-run.md)) — the
  devcontainer docs should note `HF_HUB_OFFLINE=1` for test runs and not pre-bake gated
  models.

## Acceptance Criteria
- [ ] `.devcontainer/` in `alfred/` provisions Python 3.13 + uv + Node 22 with redis-stack
  and mosquitto service containers; `postCreateCommand` installs backend + frontend deps.
- [ ] Inside the container: `pytest` passes, `npm run build` works, and `python -m runner`
  starts with Redis/MQTT reachable (voice/embedding warmup failures acceptable/documented).
- [ ] No published host ports and no plaintext secrets in the devcontainer config.
- [ ] CLAUDE.md documents the environment paths (devcontainer for Linux/cloud, `alfredctl`
  for containerized macOS/Linux, native Homebrew-managed Redis/Mosquitto for macOS) so
  agents pick correctly.
- [ ] Verified from at least one real cloud agent session (Claude Code web or Codespace).
