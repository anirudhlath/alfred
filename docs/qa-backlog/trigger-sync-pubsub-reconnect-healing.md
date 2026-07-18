# Trigger Cache Self-Heals After a Redis Pub/Sub Disconnect

**Feature:** `TriggerStore` pub/sub cache coherence — resubscribe-on-error + reconciliation `refresh()` safety net
**Priority:** medium
**Type:** integration

## Prerequisites
- Full Alfred stack running (`uv run python -m runner`), Redis Stack running locally (Homebrew service, so it can be stopped/started without tearing down the whole dev environment)
- Terminal tailing `[triggers]` and `[conscious]` log output
- Web SPA authenticated, on Chat page

## Test Steps
1. Confirm both `triggers` and `conscious` processes are up and their `TriggerStore.start_sync()` subscribers are connected (no errors in logs yet).
2. Restart Redis to force every open pub/sub connection to drop: `brew services restart redis-stack-server` (or equivalent for however Redis is running locally).
3. Watch `[triggers]` and `[conscious]` logs for `Trigger sync subscriber error: ... — resubscribing` (`core/triggers/store.py::_sync_loop`), confirming the disconnect was detected.
4. Confirm each process resubscribes and self-heals: after the 1s backoff, expect a `refresh()` call (no explicit log line for this specific refresh, but the process should recover cleanly — no crash, no stuck state).
5. While Redis is restarting (or immediately after), from the web chat create a new trigger, e.g. "remind me in 30 seconds to test recovery."
6. Confirm the reminder still fires correctly despite the disconnect/reconnect churn — either via the freshly-resubscribed pub/sub path, or via the 60s periodic `refresh()` safety net if the create happened to land in the reconnect gap.
7. Separately, verify the steady-state 60s reconciliation net still works even with no disconnect: create a trigger, and confirm (via `[triggers]` debug logs, `Cache refresh complete`) that periodic `refresh()` keeps running every ~60s regardless.

## Expected Result
- No process crashes or hangs when Redis drops and comes back.
- Both `triggers` and `conscious` processes resubscribe automatically (no manual restart required).
- A trigger created during or shortly after the disconnect is not lost — it either arrives via pub/sub after resubscribe or via the reconciliation `refresh()`, worst case ~60s later (matches the design's stated worst-case staleness).
- Firing still works end-to-end once the cache is coherent again.

## Notes
- This exercises the "Failure handling" section of the design doc directly: *"pub/sub subscriber crash → resubscribe + full refresh(); worst-case staleness 60s (reconciliation), same as today's steady state."* This is inherently hard to unit-test faithfully against a real Redis network interruption (existing tests mock the pubsub transport), so it's a good candidate for a manual pass whenever this area changes.
- If step 6's reminder is late by close to 60s instead of firing near-instantly, that's consistent with the pub/sub resubscribe having failed silently and only the reconciliation net saving it — still "working as designed" per the safety net, but worth flagging as a regression in the primary (pub/sub) path if it happens consistently.
- Optional stress variant: kill Redis mid-reconnect-backoff (repeatedly bounce it every couple seconds) to confirm the `_sync_loop`'s retry doesn't tight-loop or leak `pubsub` connections — okay to skip unless there's reason to suspect a resource leak.
