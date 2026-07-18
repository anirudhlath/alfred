# Config Surface Unification — kill the false surface, centralize the strays

## Summary

`AlfredConfig` presents a false configuration surface and the real surface is
scattered. Wire it, prune it, and make `.env.example` the truthful, complete
reference. From the 2026-07-18 configurability audit.

## Context / Motivation

- **13 `AlfredConfig` fields are declared but never read from env** in
  `from_env()` (significance weights, decay/pattern/conflict/routine tuning —
  `shared/config.py:46-68`). Worse, the routine/pattern/conflict/decay ones
  are never passed to `Librarian` (`core/conscious/__main__.py:292-302`,
  `core/librarian/__main__.py:74-84`), which uses its own constructor
  defaults — dead config, a pure trap for anyone "tuning" them.
- **Raw `os.getenv` strays outside AlfredConfig:** `CHANNELS_PORT`
  (`core/channels/__main__.py:41`), `TRIGGER_PORT`
  (`core/triggers/__main__.py:294`), `LIBRARIAN_INTERVAL_SECONDS`
  (`core/conscious/__main__.py:305`), `ALFRED_DATA_DIR`
  (`core/identity/credentials.py:50`), `ALFRED_DEBUG`, `LITELLM_LOG`,
  `APNS_*`, `LOG_FORMAT`.
- **Contradictions:** `LOG_JSON` (config.py:118) vs `LOG_FORMAT=json`
  (`shared/logging.py:72`) both claim to control JSON logging;
  `involuntary_recall_threshold` defaults disagree (`0.5` in AlfredConfig vs
  `0.4` in `ConsciousConfig`, `core/conscious/engine.py:71`).
- **SDK footgun:** `sdk/alfred_sdk/client.py:37` reads `REDIS_URL` while core
  reads `REDIS_HOST`/`REDIS_PORT` — setting only `REDIS_HOST` leaves
  SDK-registered services pointed at localhost.
- **`.env.example` drift:** ghost vars never read (`EPISODIC_HOT_DAYS`,
  `EPISODIC_COMPRESS_DAYS`), a `CLAUDE_MODEL` example that wouldn't route
  (`claude-opus-4-6` vs the OpenRouter-prefixed ids the code expects),
  documents `CLAUDE_API_KEY` but not the preferred `OPENROUTER_API_KEY`, and
  ~17 working-but-undocumented vars (audit list in this ticket's history).

## Acceptance Criteria

- [ ] Every `AlfredConfig` field is either wired in `from_env()` AND consumed
      by its user (Librarian receives the tuning params), or deleted. No
      declared-but-dead fields remain.
- [ ] Stray `os.getenv` config reads move into `AlfredConfig` (or are
      documented as deliberate exceptions with a comment — e.g. SDK
      standalone reads). One env var per concern: resolve `LOG_JSON` vs
      `LOG_FORMAT` to a single documented var.
- [ ] `involuntary_recall_threshold` has one default, defined once.
- [ ] SDK/core Redis config unified: either the SDK accepts host/port too, or
      core honors `REDIS_URL` — one documented convention (`create_redis`
      in `shared/redis_streams.py` is the natural core-side seam).
- [ ] `.env.example` regenerated to match the actual read surface: no ghost
      vars, correct example values that actually route, every supported var
      present with a one-line comment, secrets clearly marked.
- [ ] A test guards against future drift: e.g. parse `.env.example` keys and
      assert each is consumed (and vice versa for `from_env()` reads).

## Notes

- home-service's config (plain getenv, hardcoded `:8000` in its advertised
  `/mcp` URL — `alfred_ext/register.py:27`) is superseded by HA Plan 2's
  rewrite; fix there, not here.
- Verified 2026-07-18: `.env` files are git-ignored and absent from history
  in both repos — the on-disk secrets are not leaked. Plan 2 moves
  home-service credentials to the pushed-credential flow.
