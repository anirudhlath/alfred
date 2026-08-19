# Satellite Rollout with a Pi Offline

**Feature:** Satellite deployment (Phase 4, `dev/deploy_satellites.py`)
**Priority:** high
**Type:** functional / failure-mode

## Prerequisites
- At least two satellites registered. Today's fleet
  (`~/code/alfred-deploy/satellites.yaml`) has only `pi1` — add a second device before
  running this drill.
- A machine that can reach the fleet over SSH as `anirudhlath` with
  `~/code/alfred-deploy/id_ed25519_satellites`, and an `alfred-satellite` checkout on it.
- Every device passes a normal rollout first: `uv run python -m dev.deploy_satellites`.
- Note: as of this writing there is no CI job that runs this on a merge — the rollout is
  invoked by hand (see "Adding a satellite" in `docs/deployment.md`). Re-run this drill via
  the CI job once `alfred-satellite`'s automated trigger lands.

## Test Steps
1. Power off exactly one Pi. Confirm: `ping -c1 <that-host>` fails.
2. Run the rollout: `uv run python -m dev.deploy_satellites` from the `alfred-satellite`
   checkout.
3. Read the per-device table the command prints.
4. Check the exit status (`echo $status` in fish / `echo $?` in bash).
5. On a Pi that stayed up: `systemctl is-active wyoming-satellite wyoming-openwakeword`
   and `ls -ld /opt/alfred-satellite /opt/alfred-satellite.prev`.
6. Power the offline Pi back on and re-run the rollout.

## Expected Result
- Step 3: the table lists EVERY device. The powered-off one shows `FAILED` with a detail
  naming the transport failure; the others show `OK`.
- Step 4: the exit code is non-zero. A partial rollout is a failure, never a pass.
- Step 5: both units active, and `.prev` present — the healthy Pis really did deploy. This
  is the property that matters: one dead Pi did not leave the rest of the house on an old
  build.
- Step 6: all devices `OK`, exit code `0`.

## Also worth checking
The device that was offline should appear in the file-only warning
(`… is in satellites.yaml but did not answer mDNS`) during step 2's log output.
