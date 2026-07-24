# Seed Mode Fixtures Pack (dummy HA snapshot + sample user + sample memories)

## Summary

`ALFRED_DATA_MODE=seed` exists as a switch (`shared.config.data_mode()`,
`alfredctl up --mode seed`) but does not yet load any fixture data — today it behaves
identically to `ephemeral` (a throwaway `/data`, no bind mount, no Redis/Mosquitto
persistence). The containerization design spec's §4 called for `seed` to additionally
copy a bundled `fixtures/` directory into the data dir on boot, so a fresh demo/QA run
comes up with realistic-but-disposable state instead of a genuinely empty system.

## Context / Motivation

- Spec: `docs/superpowers/specs/2026-07-19-alfred-containerization-design.md` §4 — "
  `fixtures/` ships a dummy HA snapshot, a sample authenticated user, and a handful of
  memories/routines so a fresh worktree comes up with realistic *disposable* state."
- `alfredctl smoke` currently boots `--mode seed` purely to get a throwaway `/data`
  volume for its health checks — none of its checks actually exercise fixture content,
  because none exists.
- Without this, demoing Alfred to someone new (or writing an eval/QA scenario against a
  seeded state) requires manually driving onboarding + creating memories/routines by
  hand every time, which defeats the point of `seed` mode as distinct from `ephemeral`.

## Acceptance Criteria

- [ ] A `fixtures/` directory (package-shipped, read-only, same pattern as
      `core/memory/preferences/.example`) contains: a dummy Home Assistant entity/state
      snapshot usable by evals' context fixtures or a lightweight fake home-service, a
      pre-registered sample WebAuthn-authenticated user (or a documented bypass for
      environments without a real authenticator), and a handful of seeded episodic
      memories + at least one procedural routine.
- [ ] `core.memory.paths.seed_defaults()` (or a sibling function reserved for `seed`
      mode specifically) copies `fixtures/` content into `$ALFRED_DATA_DIR` only when
      `ALFRED_DATA_MODE=seed` — `ephemeral` stays genuinely empty, `persistent` is
      unaffected.
- [ ] `docs/containerization.md` §4's data-mode table is updated once this lands (it
      currently documents the honest gap: "seed mode does not yet load fixtures").
- [ ] `alfredctl smoke` gains at least one check that the fixture content is actually
      present/queryable post-boot (e.g. a memory recall or trigger list call), not just
      that `/data` exists.
