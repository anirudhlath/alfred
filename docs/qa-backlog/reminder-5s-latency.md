# "Remind Me In 5 Seconds" Fires Within ~6s (Instant Trigger Visibility + Scheduled Wakeup)

**Feature:** Instant trigger visibility (Redis pub/sub cache coherence) + scheduled-wakeup firing (replaces 1s tick loop)
**Priority:** critical
**Type:** e2e

## Prerequisites
- Full Alfred stack running via `uv run python -m runner` (bridge, reflex, triggers, conscious, channels, memory-ingestor all report started)
- Redis Stack running and reachable
- Ollama/LiteLLM route configured so the Conscious Engine can actually respond (this test measures real LLM latency as part of the budget, not a mock)
- Web SPA open in a browser, authenticated (passkey or trusted network), on the Chat page
- Terminal tailing runner output (or `docs/qa-backlog` convention: watch stdout, which is loguru-prefixed per service, e.g. `[triggers]`, `[conscious]`)

## Test Steps
1. Confirm all services are up: look for `Trigger Engine started` (`[triggers]`) and the conscious process's ready log line.
2. In the web chat, type "remind me in 5 seconds to check the oven" and note the wall-clock time (to the second) you press send — call this `T0`.
3. Watch `[conscious]` logs for the trigger creation (the LLM calling `create_trigger` with a `run_at` ~5s out) — note the timestamp `T1` (this is LLM response latency, the dominant variable, not the thing under test).
4. Watch `[triggers]` logs for the fire: `Trigger '<name>' fired → ActionRequest ...` or `Trigger '<name>' fired → TriggerFired event` (`core/triggers/engine.py`). Note the timestamp `T2`.
5. Confirm the reminder notification/message actually appears in the web chat UI. Note the timestamp `T3` (visible-to-user).
6. Compute `T2 - (T1 + 5s)` — this isolates scheduling latency and should be well under 1s (previously this could be 10–60s late due to the 60s cache refresh window and the fragile 1s cron tick).
7. Compute `T3 - T0` — end-to-end latency including LLM think time. Expect ≤ ~6s.

## Expected Result
- `T3 - T0` (send → visible reminder) is ≤ ~6 seconds total.
- `T2 - (T1 + 5s)` (actual due time → fire) is near-zero — sub-second, not the old 10–20s regression.
- No duplicate fires, no missed fire.
- The trigger is a one-shot `run_at` trigger and is deleted from the store after firing (`core/triggers/engine.py` one-shot delete path) — a second `GET /api/admin/triggers` call should no longer list it.

## Notes
- This is the regression test called out by name in the design doc (`docs/superpowers/specs/2026-07-15-instant-triggers-client-timezone-design.md`, "Root causes") — prior behavior: trigger created in the conscious process was invisible to the triggers process's in-memory cache for up to 60s (full-refresh-only sync), and even once visible, the 1s tick loop plus a `<1.0s` window-match on cron could delay or skip firing.
- Two independent mechanisms are under test together and can't be cleanly isolated in a live E2E: (a) pub/sub cache coherence (`TRIGGERS_CHANGED_CHANNEL`, `TriggerStore.start_sync`) making the new trigger visible to the triggers process in milliseconds instead of up to 60s, and (b) the scheduler loop (`core/triggers/__main__.py::scheduler_loop`) computing an exact wakeup via `next_fire_time()` instead of polling every 1s. If this test regresses, check `[triggers]` logs for `Trigger sync subscriber error` (pub/sub dropped, falling back to the 60s reconciliation net) to tell which mechanism is at fault.
- If the LLM is slow (`T1 - T0` > a few seconds), that's a separate latency budget issue, not this feature's regression — re-run with a faster model/route before filing a bug against trigger latency.
