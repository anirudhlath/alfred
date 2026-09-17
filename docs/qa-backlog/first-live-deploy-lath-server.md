# First Live Deploy to lath-server

**Feature:** Continuous deployment (Phase 2)
**Priority:** high
**Type:** functional
**Status:** PASSED — 2026-08-19, run [`32223745632`](https://github.com/anirudhlath/alfred/actions/runs/32223745632)

## What this verified

The first real merge-triggered deploy ran end to end and was checked against the live box
— the one thing a design document can't prove on its own.

## Steps taken

1. Noted the pre-deploy state: the running container's image, and `docker volume ls | grep alfred`.
2. Merged a PR to `master`. `gate` (`ci-ok`) went green, then `deploy to lath-server` ran
   and finished green in 4m36s.
3. After the run: `docker ps --filter name=alfred`, `docker inspect alfred`,
   `docker volume ls | grep alfred`, `docker images alfred`, and a direct `/health` check.

## Evidence

- Run `32223745632`: every job green, including `deploy to lath-server` (4m36s).
- Exactly one container, named `alfred` — not `alfred-alfred-1`, not two.
- Compose project `alfred` (confirmed via the `com.docker.compose.project` label) — the
  `name: alfred` pin held.
- The same three volumes as before the deploy: `alfred_alfred_data`, `alfred_alfred_models`,
  `alfred_redis_data`. No new `alfred-deploy_*` volume appeared.
- `alfred_alfred_data`'s creation time is still 2026-07-24 — the volume was reused, not
  recreated, so the secrets passphrase survived.
- Container health: `healthy`.
- `GET /health` → `200`.

## Expected result (met)

- Exactly one container named `alfred`.
- A fresh image build, `healthy` status.
- The same three volumes as before the deploy — a new `alfred-deploy_*` volume would have
  meant the `name: alfred` pin failed and the stack was running on empty state.
- `alfred:latest` plus a tag matching the merged commit's sha.
- The secrets passphrase and any stored credentials survived, evidenced by the unchanged
  volume creation time and a healthy `/health` response.

## Notes

This validates the core safety property the whole design exists for: a real deploy
replaced the running container without losing the `alfred_data` volume or the secrets
passphrase inside it.
