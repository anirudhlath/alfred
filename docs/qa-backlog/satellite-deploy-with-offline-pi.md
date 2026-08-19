# Satellite Rollout with a Pi Offline

**Feature:** Satellite deployment (Phase 4, `ci.yml` `deploy to the satellite fleet` job)
**Priority:** high
**Type:** functional / failure-mode

## Prerequisites
- At least two satellites registered. Today's fleet
  (`~/code/alfred-deploy/satellites.yaml`) has only `pi1` — add a second device before
  running this drill.
- The `alfred-satellite` runner is Idle at
  github.com/anirudhlath/alfred-satellite/settings/actions/runners.
- Every device passes a normal rollout first (the fleet is currently on a build that
  deployed cleanly — run `32260585256` — so this is already satisfied for `pi1`).
- SSH access as `anirudhlath` with `~/code/alfred-deploy/id_ed25519_satellites`, for
  checking device state directly in step 5 (not for triggering the rollout — that part is
  automatic now).

## Test Steps
1. Power off exactly one Pi. Confirm: `ping -c1 <that-host>` fails.
2. Merge a trivial PR to `alfred-satellite`'s `master` (a docs typo is enough).
3. Watch the run: `gh run watch --repo anirudhlath/alfred-satellite`.
4. Read the per-device table in the `Deploy the fleet` step's log.
5. Check the job's exit status.
6. On a Pi that stayed up: `systemctl is-active wyoming-satellite wyoming-openwakeword`
   and `ls -ld /opt/alfred-satellite /opt/alfred-satellite.prev`.
7. Power the offline Pi back on and merge another trivial PR (or re-run the failed job).

## Expected Result
- Step 3: `gate` (`ci-ok`) green, then `deploy to the satellite fleet` runs — and ends
  **red**, because one device failed.
- Step 4: the table lists EVERY device. The powered-off one shows `FAILED` with a detail
  naming the transport failure; the others show `OK`.
- Step 5: non-zero. A partial rollout is a failure, never a pass — same property alfred's
  deploy job has.
- Step 6: both units active, and `.prev` present on the healthy Pi — it really did deploy.
  This is the property that matters: one dead Pi did not leave the rest of the house on an
  old build.
- Step 7: all devices `OK`, job green.

## Also worth checking
The device that was offline should appear in the file-only warning
(`… is in satellites.yaml but did not answer mDNS`) in the job log during step 3.

## Notes
This drill is about a device being unreachable, not about a bad deploy rolling back a
reachable one — that scenario (an unprivileged rsync against a root-owned target, and a
port-probe racing service startup) has already been exercised for real; see
`docs/qa-backlog/satellite-rollback-exercised.md`.
