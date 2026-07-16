# Reconcile mypy 2.x --strict Baseline (76 Pre-Existing Errors)

## Summary

Under mypy 2.3.0 (the version a fresh `uv pip install` resolves from the `mypy>=2.1` pin),
`mypy --strict` on the full codebase reports 76 errors in 28 files that do not appear under
the older mypy 1.20.1 some existing venvs still carry. Reconcile the codebase to be clean
under the pinned-floor toolchain.

## Context / Motivation

Discovered during the voice-satellite bridge work (2026-07): a fresh worktree venv resolved
mypy 2.3.0 and surfaced a 76-error baseline on master. The feature branch introduced zero
new errors (verified per-task via before/after stash diffs), but the debt makes "mypy
--strict must pass" unenforceable as an absolute gate. Known clusters:

- Stale `# type: ignore[misc]` idiom on `redis.hgetall`/`hset` calls that newer redis-py
  stubs no longer need (and now flag as unused) — at least `core/identity/ws_auth.py`,
  `core/notifications/adapters/apns.py`, `core/triggers/store.py`,
  `core/reflex/tool_registry.py`, `core/conscious/session.py`, `core/voice/speaker_id.py`
  precedent shows the clean pattern.
- Untyped `xread` iteration pattern (`str-unpack`/`union-attr`) in
  `core/channels/request_bus.py`, `core/channels/telemetry_ws.py`,
  `core/channels/admin_api.py`, `core/notifications/delivery.py` — a small typed wrapper
  for xread results would zero these out in one place.
- `shared/types.py:10` — `"Redis" expects no type arguments` under the new stubs; the
  `AioRedis` alias definition itself needs updating.
- Several mypy overrides in pyproject.toml are now reported as unused sections.

## Acceptance Criteria

- `mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/` exits 0 under
  mypy ≥2.3 in a fresh venv.
- No blanket `ignore_errors`; fixes are typed properly (shared xread wrapper preferred over
  per-site ignores).
- Unused mypy override sections removed from pyproject.toml.
- CI/docs updated if the workflow's mypy invocation changes.
