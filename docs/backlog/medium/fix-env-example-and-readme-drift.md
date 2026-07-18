# Fix .env.example & README/CONTRIBUTING Config Drift

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** medium
**Severity (audit):** medium
**Source:** Public-release readiness audit 2026-07-18 (findings #19, #20, #23, #24)

## Summary
The onboarding surface a stranger reads first — `.env.example`, `README.md`, and
`CONTRIBUTING.md` — has drifted from the code it is supposed to describe. `.env.example`
documents dead variables and omits ~13 real ones; the README config table lists wrong
defaults, never tells the user to create `.env`, gives the System-2 (Claude) engine no
setup path, and still calls in-repo/sibling components "not yet public"; and
`CONTRIBUTING.md` prints a mypy command that does not match CI despite claiming "CI runs
the same checks." Because `anirudhlath/alfred` is **already public** (v0.1.0 shipped
2026-07-16), this drift is live in the published tree — a fresh clone that follows the
docs literally lands on a misconfigured or non-starting server today, not prospectively.

## Context / Motivation

### `.env.example` drift (finding #19)
- Dead/wrong entries:
  - `EPISODIC_HOT_DAYS` and `EPISODIC_COMPRESS_DAYS` are read nowhere — removed per
    `docs/superpowers/specs/2026-03-24-phase3-memory-completion-design.md:529`.
  - `LOG_JSON` is read into `config.log_json`, which has no consumer; the actual
    JSON-logging switch is the **undocumented** `LOG_FORMAT=json` (`shared/logging.py:72`).
  - `CLAUDE_MODEL` example `claude-opus-4-6` contradicts the code default
    `openrouter/anthropic/claude-sonnet-4` (`shared/config.py:100`) — a different
    provider/key format.
- Omissions: at least 13 real, code-read variables are missing from `.env.example`,
  including `OPENROUTER_API_KEY`, which takes **precedence** over the documented
  `CLAUDE_API_KEY`.
- LOC: `alfred/.env.example`, `alfred/shared/config.py`, `alfred/shared/logging.py`.

### README config/setup drift (finding #20)
- The config table claims `OLLAMA_MODEL` defaults to `gpt-oss:20b`, but the code default
  is `llama3:8b` (`shared/config.py:91`). Combined with the README never instructing the
  user to create `.env` from `.env.example`, a user who pulls `gpt-oss:20b` per
  Prerequisites gets a Reflex engine requesting the missing `llama3:8b`.
- The table has **no System-2 rows** — `CLAUDE_API_KEY` / `OPENROUTER_API_KEY` /
  `CLAUDE_MODEL` are absent, so the "Conscious Engine (Claude)" advertised in the same
  file has no documented setup path.
- `HA_HOST` default differs between README and code (`shared/config.py`).
- The docs reference a stale "Four Pillars" (the architecture is Five Pillars) — the
  pillar count is out of sync.
- LOC: `alfred/README.md`, `alfred/shared/config.py`, `alfred/docs/architecture.md`,
  `alfred/.claude/rules/architecture.md`.

### CONTRIBUTING vs CI mismatch (finding #23)
- `CONTRIBUTING.md` tells contributors to run `uv run mypy .`, but CI runs
  `uv run mypy bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`. `mypy .`
  additionally sweeps `tests/`, `conftest.py`, and scripts under `--strict` — surface
  that is never type-checked in CI and not maintained to strict standards — so a
  contributor following the doc sees failures (or passes locally on a different surface
  than CI checks). The doc explicitly says "CI runs the same checks," which is inaccurate
  for the mypy step.
- LOC: `alfred/CONTRIBUTING.md`, `alfred/.github/workflows/ci.yml`.

### Stale "not yet public" labels (finding #24)
- `README.md:119` lists the required dependency `home-service` as "(separate repo, not
  yet public)" while `README.md:205` links it as a normal GitHub repo — self-contradictory
  today and stale now that the repos are public (the quickstart would be impossible for a
  stranger if the claim were true).
- `README.md:109`/`README.md:207` describe Signal as a "separate bridge service (not yet
  public)," but a complete Signal bridge ships **in this repo**
  (`python -m core.channels.signal_bridge`, plus the `SignalChannelAdapter` used for
  notification delivery), both shelling out to `signal-cli`.
- The `signal-cli` prerequisite is undocumented.
- LOC: `alfred/README.md`, `alfred/core/channels/signal_bridge/bridge.py`,
  `alfred/core/notifications/adapters/signal.py`.

## Acceptance Criteria
- [ ] `.env.example` is regenerated from `shared/config.py` and the per-service `getenv`
  sites: dead vars (`EPISODIC_HOT_DAYS`, `EPISODIC_COMPRESS_DAYS`, `LOG_JSON`) are removed.
- [ ] Every code-read variable is present in `.env.example`, including `OPENROUTER_API_KEY`
  (with a note that it takes precedence over `CLAUDE_API_KEY`), `LOG_FORMAT`,
  `EMBEDDING_MODEL`/`EMBEDDING_DIM`, and the other missing vars.
- [ ] `CLAUDE_MODEL` in `.env.example` is aligned with the code default
  (`openrouter/anthropic/claude-sonnet-4`, `shared/config.py:100`).
- [ ] README adds an explicit `cp .env.example .env` step to the setup flow.
- [ ] The README config table matches `shared/config.py` — correct `OLLAMA_MODEL` default
  (`llama3:8b`) and `HA_HOST` default.
- [ ] The README documents the System-2 API-key requirement
  (`CLAUDE_API_KEY`/`OPENROUTER_API_KEY`/`CLAUDE_MODEL`) so the Conscious Engine has a
  setup path.
- [ ] The stale "Four Pillars" reference is corrected to the actual pillar count across
  README and the architecture docs.
- [ ] `CONTRIBUTING.md` reproduces the exact CI mypy invocation
  (`mypy bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`) so local checks match
  CI (optionally via a shared make/uv script both invoke).
- [ ] The three "not yet public" labels in the README are removed/updated, the in-repo
  Signal bridge entry point (`python -m core.channels.signal_bridge`) is documented, and
  `signal-cli` is listed as an optional prerequisite.
