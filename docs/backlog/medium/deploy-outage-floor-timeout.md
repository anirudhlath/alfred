# Shrink the Failed-Deploy Outage Floor (~10–12 Minutes)

## Summary

When a deploy fails, the job spends up to 300s polling `/health` before it even starts
rolling back, then up to another 300s verifying that the rollback worked — a worst case of
roughly 10 minutes of downtime before the operator even sees a red job, on top of however
long the build itself took. `--timeout` on `alfredctl smoke` (already a supported flag) or
`docker compose up -d --wait` would cut this materially.

## Context / Motivation

- `alfredctl smoke --attach --name alfred` — both the `Verify` step and the `Roll back`
  step's re-check — defaults `timeout` to `300.0` seconds of `/health` polling
  (`alfredctl/smoke.py`, `run_checks(..., timeout=300.0)`). That default is appropriate for
  `alfredctl smoke`'s other caller (a cold seed-mode boot from nothing), but generous for a
  `docker compose up -d` restart of an image that, in the failure case being detected,
  never comes up at all — so the full 300s elapses on both checks in exactly the failure
  case where speed matters most.
- A healthy restart in the passing case is fast: the first real deploy (run
  `32223745632`) took 4m36s total for the whole `deploy to lath-server` job, including the
  build. The 300s number only matters on the failure path.

## Proposed shape

- Pass a shorter `--timeout` (e.g. 60s) to both `smoke --attach` invocations in `ci.yml` —
  long enough for a real restart, short enough that a container that will never come up
  fails fast.
- And/or add `--wait --wait-timeout <n>` to the `docker compose up -d` calls (Docker
  Compose supports this) so compose itself blocks until the container reports healthy, or
  times out, before `Verify` starts polling at all — rather than the two waits stacking.

## Acceptance Criteria

- [ ] A deliberately broken deploy (per `docs/qa-backlog/deploy-rollback-drill.md`)
      completes end to end — build through verified rollback — in materially under 10
      minutes.
- [ ] A normal healthy deploy's total time does not regress.
