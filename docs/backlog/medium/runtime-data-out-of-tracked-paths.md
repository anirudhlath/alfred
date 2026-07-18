# Move Runtime-Mutated Data Out of Git-Tracked Paths

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** medium
**Severity (audit):** medium
**Source:** Public-release readiness audit 2026-07-18 (findings #11, #30, #69)

## Summary
The running system writes real home behavioral data into git-**tracked** paths:
procedural-memory routine YAMLs get live timestamps bumped by the runner, and
`research/data/` CSVs accrue telemetry rows. Because `anirudhlath/alfred` is already
public (v0.1.0 shipped 2026-07-16), this is a post-exposure risk, not a pre-release
one: any future `git add -A` from the main checkout would publish live personal
routine/telemetry state, and the committed baselines already contain earlier runtime
values. The routine content committed today is still synthetic seed data, but the live
system mutates these same files. Separately, the untracked `data/` directory (ECAPA
model cache and `data/credentials.db`) is not gitignored and is one accidental sweep
away from a commit.

## Context / Motivation
Three runtime-mutated data channels live in tracked paths:

1. **Procedural memory (routine YAMLs)** — `core/memory/routines/evening_dim.yaml`,
   `core/memory/routines/morning_coffee.yaml`. Committed content is currently synthetic
   seed data (`learned_from: ep-dim-0...`, `'Turn on coffee machine 07:10 weekday
   morning'`), but the live system updates these files: `git status` shows both YAMLs
   modified right now with fresh `last_suggested` timestamps (bumped from 2026-05-10 to
   2026-07-16 by the runner). Genuinely learned routines would encode real home behavior.

2. **Research telemetry CSVs** — `research/data/reflex/raw.csv`,
   `research/data/tokens/raw.csv` accrue telemetry rows during runs. Daily notes are
   tracked too (`research/daily/2026-03-10.md`), though the newest
   (`research/daily/2026-07-16.md`) currently sits untracked. Workflow reference:
   `.claude/rules/research/research-workflow.md`.

3. **`data/` cache dir (audit #69, severity low)** — `core/voice/speaker_id.py`
   downloads `speechbrain/spkrec-ecapa-voxceleb` into
   `data/models/spkrec-ecapa-voxceleb` on first use
   (`_MODEL_DIR = Path("data")/"models"/"spkrec-ecapa-voxceleb"`). `.gitignore`
   (origin/feature/voice-satellite-bridge) covers `core/voice/models/` and `*.db` but
   **not** `data/`, so multi-MB checkpoints — and anything else dropped in `data/` — are
   one `git add -A` away from being committed. `data/credentials.db` (WebAuthn credential
   store, real auth material) currently lives there in the working tree, protected only
   by the `*.db` pattern. The `>` incident (commit `e008c04`) proves accidental sweeps
   into commits happen in this repo.

## Acceptance Criteria
- [ ] `core/memory/routines/*.yaml` are gitignored, with a tracked `.example` fixture retained (mirroring the `preferences/` pattern) so a fresh clone still seeds routines.
- [ ] `research/data/` telemetry CSVs (`reflex/raw.csv`, `tokens/raw.csv`) are relocated to an untracked, gitignored data directory; only seed/example fixtures and curated/aggregate notes remain tracked.
- [ ] A research/ policy is decided and documented — either keep only curated/aggregate notes tracked, or add a pre-commit check that rejects `data/` additions containing entity names, room-level events, or conversation content.
- [ ] `data/` is added to `.gitignore` (on the PR branch and/or master) so the ECAPA model cache (`data/models/spkrec-ecapa-voxceleb`) and `data/credentials.db` cannot be swept in by `git add -A`.
- [ ] Documented as a working rule: never commit with `-A` from the main checkout until all runtime-mutable stores are relocated out of tracked paths.
- [ ] Confirm whether the already-public baselines contain any live personal routine/telemetry values (vs. synthetic seed only) and decide if history redaction is warranted, given the repo is already public.
