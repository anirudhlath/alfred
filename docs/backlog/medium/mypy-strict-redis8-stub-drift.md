# mypy --strict fails with redis 8 / mypy 2.3 stubs (76 pre-existing errors)

## Summary

A fresh `uv pip install` now resolves redis 8.0.1 and mypy 2.3.0 (pins are
`redis>=5.0`, `mypy>=2.1`), whose stricter/changed stubs produce **76 errors in
27 files** on master — mostly `xreadgroup` return-type unions that no longer
unpack (`str-unpack`, `assignment`, `union-attr` around stream consumer loops).
The main checkout's venv (redis 7.3.0 / mypy 1.20.1) still passes clean, so the
breakage only appears in fresh venvs (worktrees, CI, new machines).

## Context / Motivation

Found 2026-07-15 while building the sensor-trigger/warmup fix in a fresh
worktree venv. That PR was verified to add zero NEW errors relative to the
master baseline under the same toolchain, but the baseline itself is red.
Known related note: `project_dev_env_gotchas` tool-pin drift.

## Acceptance Criteria

- [ ] Decide: pin `redis<8` (short-term) OR migrate consumer-loop typing to the
      redis 8 stub shapes (long-term; touches reflex, conscious, triggers,
      channels, notifications delivery, evals).
- [ ] `mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
      passes in a FRESH venv (`uv venv --python 3.13 && uv pip install -e ".[dev,memory,voice,integrations]"`).
- [ ] Also fix the two `Unused "type: ignore"` hits (web_server.py device
      endpoints) that the newer mypy flags.
- [ ] Consider a CI job that runs the type check from a fresh venv so pin drift
      surfaces immediately instead of during unrelated feature work.
