# Cron Trigger Re-Arms Under a New Timezone (tz-changed Poke)

**Feature:** Dynamic cron evaluation in the user's current timezone + `tz-changed` pub/sub poke re-arming sleeping alarms
**Priority:** medium
**Type:** integration

## Prerequisites
- Full Alfred stack running (at minimum `triggers` and `channels`/`conscious` processes), Redis Stack running, `redis-cli` available
- Terminal tailing `[triggers]` log output
- A way to create a trigger (web chat, or `POST` via the admin API)

## Test Steps
1. Create a cron trigger with a short interval for practical QA turnaround, e.g. `*/2 * * * *` (every 2 minutes) — a real "daily" cron uses the identical mechanism but is impractical to wait a full day for. Ask the assistant to create it, or use the admin trigger-creation path directly.
2. Confirm creation: `redis-cli HGET alfred:triggers <trigger_id>` shows the trigger with the cron condition, and `[triggers]` logs show it loaded/synced.
3. Note the current resolved timezone: `redis-cli GET alfred:user:timezone` (e.g. `America/Denver`). If unset, send one chat message first so it gets populated (see `reminder-absolute-local-time.md`).
4. Let the trigger fire once under the current timezone; confirm the fire's wall-clock time in `[triggers]` logs (`Trigger '<name>' fired ...`) lines up with the current zone's minute boundary.
5. In one terminal, subscribe to the coherence channel to directly observe the poke: `redis-cli SUBSCRIBE alfred:triggers:changed`.
6. In another terminal, change the timezone directly: `redis-cli SET alfred:user:timezone "Asia/Tokyo"`. **Note:** a raw `SET` alone does *not* publish — only the app's `set_user_timezone()` helper does both atomically. To simulate the real coherence poke (what a client message with a new IANA timezone would trigger via the conscious engine), also run: `redis-cli PUBLISH alfred:triggers:changed '{"op":"tz-changed"}'`.
7. Confirm the subscriber terminal from step 5 receives the `{"op": "tz-changed"}` message.
8. There is no dedicated "tz-changed" log line in the triggers process (`TriggerStore._apply_sync_message` handles it silently — cache invalidation only, no log). The observable proof is behavioral: the next cron fire should land on the new zone's minute boundary, not the old one. Watch for the next 1–2 fires in `[triggers]` logs and confirm the wall-clock fire time now corresponds to `Asia/Tokyo`, not `America/Denver`.
9. As a more production-realistic alternative to steps 6–7, instead send a chat message from a client whose `Intl.DateTimeFormat`/`TimeZone.current` reports `Asia/Tokyo` (e.g. browser DevTools timezone override) — this exercises the full `ConsciousEngine.process_request` → `set_user_timezone` path rather than a manual Redis poke.

## Expected Result
- The `tz-changed` pub/sub message is observed on `alfred:triggers:changed` within the same second as the `SET` (via the manual `PUBLISH` in step 6, or automatically via a real client message in step 9).
- The engine's cached timezone (`TriggerEngine._tz_cache`) is invalidated and the scheduler re-arms — the next cron fire happens at the new zone's minute boundary, not the old one, without needing to wait for the 60s reconciliation `refresh()` safety net.
- No duplicate fire is produced by the re-arm itself (re-arming only recomputes the wakeup target; it doesn't force an extra evaluation pass that fires early).

## Failure Modes To Watch For
- If the fire still lands on the old timezone's boundary after the poke was confirmed on the pub/sub channel, the bug is in `TriggerEngine.invalidate_tz_cache` wiring or `TimeTrigger.next_fire_time`'s use of `context.tz` — file against `core/triggers/engine.py` / `core/triggers/types/time.py`.
- If the fire only corrects itself after ~60s, the pub/sub poke path is broken and only the periodic `refresh()` safety net (`core/triggers/__main__.py`, `_periodic(store.refresh, 60.0, ...)`) is healing it — still a bug, just a different one to report.

## Notes
- `croniter` computation happens fresh on every `next_fire_time()` call, anchored at `last_fired or created_at` converted into the current `ZoneInfo(tz)` — DST correctness across the change is covered by unit tests (`core/triggers/tests/test_types_time.py`), not manual QA. This ticket exists to verify the *cross-process wake-up plumbing*, which unit tests can't exercise against a live scheduler loop.
- `alfred:user:timezone` is single-key/single-user by design (no per-identity keys yet) — changing it affects every pending trigger's cron evaluation, which is expected.
