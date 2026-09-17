# Move Module-Scope `AlfredConfig.from_env()` Calls Behind a Startup Boundary

## Summary

Three modules call `AlfredConfig.from_env()` at **module scope**, binding the result to a
module-level `_config`. Twenty other call sites in the repo call it inside a function.
Because the module-scope three run at *import* time, any error raised while parsing the
environment surfaces as an aborted import of whatever module happened to pull them in —
a traceback pointing at an unrelated import statement, arbitrarily far from the process's
actual startup boundary — rather than as a clean configuration error the operator can act
on.

This became reachable when `from_env()` gained real validation. It previously did little
more than `os.getenv` with defaults and near enough never raised, so where it ran did not
matter much. `normalize_embedding_backend()` (rejecting an unknown `EMBEDDING_BACKEND`)
and `positive_float_env()` (rejecting a non-numeric or non-positive
`EMBEDDING_TIMEOUT_SECONDS`) both raise `RuntimeError`, and `EMBEDDING_DIM` /
`REDIS_PORT` and friends can still raise a bare `ValueError` from `int()`. Fail-fast on a
config typo is deliberate and correct; the problem is only *where* the failure lands.

## Context / Motivation

- `core/reflex/ollama_client.py:12` — `_config = AlfredConfig.from_env()`. This is a
  production hot-path module, so a bad `.env` breaks importing the reflex client rather
  than starting the reflex service.
- `evals/inference.py:11` — `_config = AlfredConfig.from_env()`.
- `evals/pipeline.py:23` — `_config = AlfredConfig.from_env()`, and this module also
  imports `evals.inference`, so one bad env var aborts the import twice over.
- Every other call site (`core/conscious/__main__.py:440`,
  `core/memory/ingestor_main.py:90`, `core/librarian/__main__.py:34`,
  `core/channels/__main__.py:24`, `runner/__main__.py:202`, `bus/__main__.py:12`,
  `alfredctl/smoke.py:27`, …) reads config inside `main()` or a request/lazy-init path,
  where a raise is attributable to the process that is starting.
- Module-scope config also freezes the values at import time, so `monkeypatch.setenv`
  in a test cannot influence them and the affected modules are effectively untestable
  against alternate configuration — the repo has been bitten by module-scope env parsing
  before (see the `-p no:deepeval` note in `pyproject.toml`, added because importing
  `deepeval` ran `autoload_dotenv()` as an import side effect and handed the suite the
  developer's real `REFLEX_BACKEND`/`OPENAI_COMPAT_HOST`).

## Acceptance Criteria

- [ ] No module in the repo calls `AlfredConfig.from_env()` at module scope; the three
      sites above read config lazily (a `_get_config()` with a module-level cache, or
      config threaded in from the caller) or at their service's startup boundary.
- [ ] A deliberately invalid env var (e.g. `EMBEDDING_BACKEND=banana`,
      `EMBEDDING_TIMEOUT_SECONDS=abc`) produces one actionable error naming the variable,
      raised from the entry point being run — not a traceback whose last frame is an
      `import` statement in an unrelated module.
- [ ] A test asserts the invariant so it cannot regress: import every package module and
      assert none evaluated `from_env()` during import (e.g. patch
      `AlfredConfig.from_env` to raise, then import the modules).
- [ ] The affected modules become configurable under test — `monkeypatch.setenv` before
      first use changes the values the module reads.
- [ ] No behaviour change for a valid environment.

## Notes

- Related: `docs/backlog/medium/config-surface-unification.md` covers the shape of the
  config surface itself; this ticket is only about *when* it is evaluated.
- Worth deciding at the same time whether `from_env()` should raise at all or return an
  errors list a startup boundary reports together, so an operator with three bad vars
  fixes them in one pass instead of three restarts.
