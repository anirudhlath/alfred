# Satellite Rollback — Exercised for Real and Passed

**Feature:** Satellite deployment (Phase 4, `dev/satellites/deploy.py` `_rollback()`)
**Priority:** high
**Type:** functional / failure-mode
**Status:** PASSED — 2026-08-19, during rehearsal of `alfred-satellite#2` and `#3` against
the live `pi1` device.

## Scope — read this before citing this file elsewhere

This covers **only the satellite rollback path**, on the real fleet. **It says nothing
about alfred's own container rollback** (`docker-compose.yml` / `ci.yml`'s `deploy` job on
`anirudhlath/alfred`) — that remains untested; see
`docs/qa-backlog/deploy-rollback-drill.md`, still pending. The two mechanisms share a
design (record state, replace, verify, restore on failure) but are independent
implementations on independent runners, and one being proven live does not verify the
other.

## What was verified

Rehearsing the satellite rollout against real hardware (not just the unit-tested pure
functions in `dev/satellites/deploy.py`) turned up two defects that broke a deploy on
`pi1` — and both times, `_rollback()` recovered the device correctly.

## Incident 1 — rsync into a root-owned target

- **What happened:** `rsync` connected to the Pi as the unprivileged `anirudhlath` user,
  but the destination (`/opt/alfred-satellite-src`) had been created via `sudo mkdir`, so
  it was root-owned. Every file write failed: `mkstemp ... Permission denied (13)`.
- **Where it was caught:** step 2 (staging), *before* `/opt/alfred-satellite` was ever
  moved aside — `deploy_one()`'s own staging boundary held, so this reported as
  "could not stage" with no rollback attempted, because there was nothing to roll back.
  The device was untouched.
- **Fix:** `SshTransport.push()` now runs `--rsync-path="sudo rsync"`, matching every
  other remote command in the sequence (`cp`, `mv`, `setup.sh`), which already ran under
  `sudo`.

## Incident 2 — port-probe race

- **What happened:** the original sequence probed the Wyoming port immediately after
  `systemctl is-active` returned success. `is-active` reports "active" as soon as the
  process starts, not once it has finished loading its wake-word/STT models and bound the
  port — so the immediate probe found the port still closed and rolled back a deploy that
  was, in fact, healthy and about to come up within roughly a minute.
- **Where it was caught:** step 6, *after* `/opt/alfred-satellite` had already been
  replaced — this is the real rollback path, not the early staging guard.
- **What the rollback did:** restored `/opt/alfred-satellite` from `.prev`, restarted both
  units, and reported the device as failed-but-rolled-back
  (`DeviceResult(rolled_back=True)`). Verified directly on the device afterward: install
  dir back to the previous tree, both `wyoming-satellite`/`wyoming-openwakeword` units
  `active`, `config.env` identity intact (`SATELLITE_NAME="living room"`,
  `WAKE_WORD="hey_jarvis"`).
- **Fix:** `_probe_until()` retries the port probe up to 30 times, 2 seconds apart
  (`PROBE_ATTEMPTS`/`PROBE_DELAY_S` in `dev/satellites/deploy.py`), before declaring the
  device dead.

## Why this counts as the rollback drill passing

Incident 2 is exactly the failure-mode scenario `docs/qa-backlog/deploy-rollback-drill.md`
describes for alfred: a deploy that looked broken from one signal (the immediate port
probe) triggered a real rollback, and the rollback restored the device to a fully working
state — correct code, correct running units, correct identity. This wasn't staged; it
happened once, unintentionally, from a genuine race condition, and the rollback path
handled it correctly on the first and only time it has been exercised for real.

## What remains unverified

- A rollback that itself fails partway (e.g. the `mv .prev` step failing) — not yet seen.
- `_rollback()` does not restore `/etc/systemd/system/*.service` — tracked separately in
  `docs/backlog/medium/satellite-rollback-unit-files.md`, and not exercised by either
  incident above (neither involved a bad unit file).
- The offline-Pi partial-fleet scenario — see `docs/qa-backlog/satellite-deploy-with-offline-pi.md`,
  still pending.
