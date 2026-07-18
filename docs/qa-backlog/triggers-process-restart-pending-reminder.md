# Pending Reminder Survives Triggers Process Restart (Rehydration + Catch-Up)

**Feature:** Scheduled-wakeup firing — startup rehydration (`TriggerStore.load`) + exactly-once catch-up fire on a late wakeup
**Priority:** high
**Type:** integration

## Prerequisites
- Full Alfred stack running via `uv run python -m runner` (hot-reload enabled by default)
- Redis Stack running and reachable
- Terminal access to find/kill the `triggers` child process by PID (`ps aux | grep core.triggers` or watch runner's `[triggers]`-prefixed startup line for the PID)
- Web SPA authenticated, on Chat page

## Test Steps

### Part A — restart while the due time is still in the future
1. Send "remind me in 90 seconds to flip the laundry" via web chat. Confirm creation in `[triggers]` logs.
2. At ~30–40 seconds in (well before due), find and kill the triggers process only: `pkill -f "core.triggers"` (or `kill -TERM <pid>`; avoid `-9` first so you can also separately test the harder SIGKILL case if time permits).
3. Confirm the runner supervisor detects the crash/exit and restarts it (exponential backoff, first restart ~1–2s per `runner/supervisor.py`) — look for a fresh `Trigger Engine started` line.
4. Confirm on restart: `Loaded N triggers` includes the pending reminder (rehydrated from Redis `alfred:triggers` via `TriggerStore.load()` — YAML is only a fallback when Redis itself is empty).
5. Let it run to the original due time. Confirm the reminder still fires at (approximately) the originally requested moment, not late by the restart's downtime and not lost.

### Part B — restart *after* the due time has already passed (forces catch-up)
6. Send "remind me in 20 seconds to check the stove."
7. Kill the triggers process within the first ~10 seconds (before due), and this time keep it down (don't let it auto-restart, or restart the whole runner) until at least 30–40 seconds have elapsed — i.e. until the due time is in the past.
8. Bring the triggers process back (let the supervisor auto-restart it, or restart the runner).
9. Confirm the reminder fires **immediately** on restart (within the first scheduler loop iteration) rather than being silently dropped — this exercises `scheduler_loop`'s ordering: `evaluate_tick()` runs before the wait, so a past-due `run_at` fires on the very first pass. Watch `[triggers]` logs for `Trigger '<name>' fired ...` right after `Trigger Engine started`.
10. Confirm exactly one fire (no duplicate), and — since this is a one-shot `run_at` trigger — confirm it's deleted afterward (`GET /api/admin/triggers` no longer lists it, or `redis-cli HGET alfred:triggers <id>` returns nil).

## Expected Result
- Part A: reminder fires at its originally-requested time regardless of the mid-flight restart; no duplicate, no loss.
- Part B: a reminder whose due time elapsed entirely while the triggers process was down fires exactly once, immediately upon restart (catch-up), and is cleaned up as one-shot afterward.
- No stack trace / unhandled exception in `[triggers]` logs during either restart.

## Notes
- This exercises the "no migrations, no data loss" claim from the design doc's Rollout section under real process churn — something the unit-tested `next_fire_time()`/`evaluate()` catch-up logic can't verify end-to-end (those tests fake the clock; they don't restart a real process against real Redis state).
- If Redis itself is also flushed/restarted (not just the triggers process), rehydration falls back to the YAML snapshots in `core/memory/triggers/` (`TriggerStore.rehydrate_from_disk_static`, gitignored, written every 300s + on every save/delete) — worth a follow-up ticket if this scenario becomes a real operational concern, but out of scope here since this ticket only kills the process, not Redis.
- Cron triggers (not just one-shot `run_at`) should also exhibit the catch-up behavior if a cron boundary was missed during downtime — feel free to fold a quick cron variant into this same manual pass if time allows, rather than filing a separate ticket.
