# Route `evals/__main__.py` preferences path through `core.memory.paths`

## Summary
`evals/__main__.py` still builds its default `--preferences-dir` with a package-relative
path (`Path(__file__).parent.parent / "core" / "memory" / "preferences"`) instead of
resolving it through `core.memory.paths.preferences_dir()`, the single source of truth
introduced for runtime-writable state in the Containerization Part 1 work.

## Context / Motivation
Tasks 1–5 of the containerization plan consolidated all runtime-writable memory state
(scratchpad, routines, preferences, profile, triggers, episodic cold store) under
`ALFRED_DATA_DIR` via `core/memory/paths.py`, and added a regression gate
(`tests/core/memory/test_no_package_state_paths.py`) that fails the build if any file
under `core/` reaches into the installed package for writable state using the
`.parent.parent / "memory"` pattern.

`evals/__main__.py` matches the spirit of that anti-pattern but lives outside `core/`,
so the gate doesn't currently catch it. It was left out of Part 1 scope deliberately:

- The eval preferences dir is a **reproducibility input**, not live runtime state —
  evals are meant to run against a fixed, versioned fixture set of preference files so
  runs are comparable across time, not against whatever a live user's
  `ALFRED_DATA_DIR/preferences` currently contains.
- It's already fully CLI-overridable (`--preferences-dir`), so there's no functional
  bug — this is a consistency/DRY concern, not a correctness one.

Leaving two divergent conventions for "where do preference files live" is a latent
source of confusion for anyone extending the evals harness or the memory subsystem,
so it should be reconciled once Part 1's data-dir model has settled.

## Acceptance Criteria
- `evals/__main__.py`'s `_PREFERENCES_DIR` default either:
  - calls `core.memory.paths.preferences_dir()` (or a new eval-specific helper built on
    the same `shared.config.data_path` primitive), so the default tracks
    `ALFRED_DATA_DIR`/`ALFRED_DATA_MODE` like every other runtime-writable path; **or**
  - if the reproducibility argument means it must stay pinned to a package-shipped
    fixture path, that decision is documented inline with a comment explaining why it
    intentionally diverges from `core.memory.paths`.
- `tests/core/memory/test_no_package_state_paths.py` is extended to also scan `evals/`
  (or a sibling test is added for `evals/`) if the chosen resolution still leaves a
  package-relative path pattern that the gate is meant to catch.
- No change to the `--preferences-dir` CLI override behavior or eval reproducibility
  semantics — this is a default-path plumbing change only.
