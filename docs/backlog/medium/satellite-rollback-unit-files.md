# Satellite Rollback Does Not Restore systemd Unit Files

## Summary

`_rollback()` in `alfred-satellite`'s deploy tooling restores the code tree
(`/opt/alfred-satellite.prev` → `/opt/alfred-satellite`) but never touches
`/etc/systemd/system/*.service`. `scripts/setup.sh` copies new unit files and
`enable --now`s them at its own last step, before three of the four rollback triggers
(restart failed, `is-active` failed, port probe failed) can even fire. A bad *unit file*
therefore rolls the code back but restarts against the still-new unit, which can fail
identically while `DeviceResult.rolled_back` reports `True` — a rollback that looks clean
but did not actually fix anything.

## Context / Motivation

- `dev/satellites/deploy.py` `_rollback()`: `rm -rf REMOTE_ROOT && mv PREV_ROOT
  REMOTE_ROOT`, then `systemctl restart`. `scripts/setup.sh`'s own
  `cp .../systemd/*.service /etc/systemd/system/ && systemctl daemon-reload &&
  systemctl enable --now ...` already ran by the time any of `deploy_one`'s later checks
  (restart, `is-active`, port probe) can fail, and none of that is undone.
- Found during Phase 5 documentation of the CD design
  (`docs/superpowers/specs/2026-08-18-cd-local-runner-design.md` §7.4); not yet reproduced
  against a real bad unit file.

## Proposed shape

- Before step 4 moves `REMOTE_ROOT` aside, snapshot
  `/etc/systemd/system/{wyoming-satellite,wyoming-openwakeword}.service` (they're plain
  files the rsync tree doesn't own).
- `_rollback` restores the snapshot and `daemon-reload`s before restarting — so a bad unit
  file actually rolls back, not just the code it was about to run.

## Acceptance Criteria

- [ ] A device whose `systemd/*.service` files were deliberately broken and then rolled
      back reports `rolled_back=True` AND is verifiably running the previous unit file.
- [ ] A unit test in `tests/test_deploy.py` covers the restore-includes-units case.
