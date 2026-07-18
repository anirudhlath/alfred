# Restore Green Quality Gates on master (ruff format + mypy)

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** high
**Severity (audit):** medium/high
**Source:** Public-release readiness audit 2026-07-18 (findings #25, #26)

## Summary
Two of the repo's own documented quality gates go red on a fresh checkout of master (`f16114d`): `ruff format --check .` reformats one file, and `mypy --strict` produces 76 errors under an unlocked dependency install. Because `anirudhlath/alfred` is **already public** (v0.1.0 shipped 2026-07-16), this is not a pre-release cleanup — any stranger cloning the repo today and running the gates as documented already hits them, undermining the project's credibility on its very first contribution attempt. The mypy half is a pre-existing, already-tracked issue; the ruff-format half is a trivial one-line fix that should have been caught before publication.

## Context / Motivation
**Finding #25 — ruff format fails on master (medium).** `ruff format --check .`, a documented gate, exits 1 on master (`f16114d`) with one file needing reformatting: `core/memory/sqlite_vec_store.py` (~line 122, an implicit string-concatenation SQL literal that ruff would join onto one line). The working tree is otherwise clean (no uncommitted changes). Reproduced with the repo venv's ruff 0.15.10 and the pyproject-pinned ruff 0.15.16 (and 0.15.22), so every fresh cloner running the gate hits it. Scope limit: `ruff check .` (lint) passes cleanly across all tested ruff versions — only the formatter check is red.

**Finding #26 — mypy --strict fails under unlocked resolution (medium).** Three scenarios were measured on master:
1. Repo's existing `.venv` (mypy 1.20.1 / redis 7.3.0): **PASS** — "Success: no issues found in 171 source files".
2. `uv.lock` versions (mypy 2.1.0 / redis 7.3.0) — what a fresh cloner following the published README/CONTRIBUTING (`uv sync --extra dev` / `--all-extras`) gets: **PASS**, 0 errors.
3. Fresh unlocked install (`uv pip install -e ".[dev,memory,voice,integrations]"`, the workspace-CLAUDE.md workflow, which is **not** the published path) resolves redis 8.0.1 / mypy 2.3.0 and **FAILS** with 76 errors — mostly redis 8 stub drift around `xreadgroup` stream-consumer loops. Affected sites include `core/conscious/__main__.py`, `core/channels/web_server.py`, and `evals/__main__.py`; the loosened pins live in `pyproject.toml` (`redis>=5.0`, `mypy>=2.1`) vs. `uv.lock`. Scope limit: the published clone path (via the lock) passes; only the unlocked/unpublished install workflow goes red.

FIX guidance from the findings: for #25, run `ruff format core/memory/sqlite_vec_store.py` and commit (a one-line join of the concatenated SQL string at ~line 122). For #26, before/while the repo is public, either merge PR #28 (redis>=8.0 pin + typed `shared/redis_streams.py` read surface) or cap `redis<8` on master so unlocked installs match the lock; the mypy half is already tracked in `docs/backlog/medium/mypy-strict-redis8-stub-drift.md` (reference, do not re-solve here).

## Acceptance Criteria
- [ ] `ruff format --check .` exits 0 on master — `core/memory/sqlite_vec_store.py` reformatted (~line 122) and committed.
- [ ] `ruff check .` still passes (confirm the format fix introduces no lint regression).
- [ ] `mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/` passes in a **fresh venv** (`uv venv --python 3.13 && uv pip install -e ".[dev,memory,voice,integrations]"`), via either the redis>=8.0 pin + typed read surface (PR #28) or a `redis<8` cap that keeps unlocked installs in step with `uv.lock`.
- [ ] The unlocked install workflow (`pyproject.toml` extras) resolves the same redis/mypy majors as `uv.lock`, so the documented gates are green on both the published (lock) and unlocked clone paths.

## Related
- [mypy --strict fails with redis 8 / mypy 2.3 stubs](../medium/mypy-strict-redis8-stub-drift.md) — the mypy half is already tracked here; reference, do not re-solve.
