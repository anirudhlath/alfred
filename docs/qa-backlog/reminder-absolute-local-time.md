# "Remind Me At &lt;Local Time&gt;" Fires In Client's Wall Clock, Not UTC

**Feature:** Client timezone awareness — `UserRequest.timezone`, `alfred:user:timezone` persistence, local `run_at` normalization
**Priority:** high
**Type:** e2e

## Prerequisites
- Full Alfred stack running (`uv run python -m runner`)
- Redis Stack running, `redis-cli` available
- Dev/test machine's system timezone is **not** UTC (e.g. `America/Denver`) — run `date +%Z` to confirm. If the machine is UTC, this test cannot distinguish "localized correctly" from "silently still UTC"; use a device/simulator set to a non-UTC zone.
- Web SPA authenticated, on Chat page
- An iOS device or simulator running the Alfred app, connected to the same server, with a non-UTC system timezone

## Test Steps

### Part A — Web
1. Note the current local wall-clock time on the test machine, e.g. `3:45 PM`.
2. In the web chat, send: "remind me at 3:47pm to stretch" (pick a time ~2 minutes out).
3. Immediately after the message completes, run `redis-cli GET alfred:user:timezone` and confirm the value is the browser's IANA zone (e.g. `America/Denver`) — matches `Intl.DateTimeFormat().resolvedOptions().timeZone` in the browser console.
4. Wait until 3:47pm **local time**. Confirm the reminder fires at that moment — not 6–7 hours early/late (which would indicate it was silently interpreted as UTC).

### Part B — iOS
5. From the iOS app (text or voice), send: "remind me at &lt;local time ~2 minutes out&gt; to check the mail".
6. Re-run `redis-cli GET alfred:user:timezone` — confirm it still reflects the correct IANA zone for whichever client sent most recently (single-user design: last writer wins).
7. Wait until the specified local time. Confirm the reminder fires (in-app banner and/or push notification) at that local wall-clock moment on the iOS device.

## Expected Result
- `alfred:user:timezone` is populated after the **first** message of the session (write-on-change persistence happens in `ConsciousEngine.process_request`, before any tool dispatch reads it) and matches the sending client's IANA zone.
- Both web and iOS reminders fire at the specified local wall-clock time, within about a minute of accuracy — never off by a UTC-offset-shaped amount (e.g. exactly N hours early/late, where N is the zone's UTC offset).
- If web and iOS are in different zones, the trigger fires according to whichever timezone was in effect at the moment the trigger's `run_at` was normalized (write time), not whatever the zone was when it fires.

## Notes
- `run_at` is normalized to an aware datetime at the tool boundary (`TimeTrigger.normalize_conditions` in `core/triggers/types/time.py`): a naive `run_at` emitted by the LLM is interpreted in the user's *current* timezone at creation time and stored with an explicit UTC offset. A stale/legacy naive `run_at` (pre-dating this feature) still falls back to UTC at evaluation — that fallback should never be hit by a trigger created during this test.
- The web channel (`core/channels/web_server.py`) only *validates* the client-sent `timezone` field at ingress; persistence to `alfred:user:timezone` happens in the domain layer (`ConsciousEngine.process_request` → `shared/usertime.py::set_user_timezone`), write-on-change only (no-op, no republish, if unchanged).
- Good edge case if time permits: send the web message from a browser with DevTools timezone override set to something like `Pacific/Auckland` or `Asia/Kolkata` (non-integer UTC offset) to catch any code path that assumes whole-hour offsets.
- Signal (no timezone field sent) should keep falling back to `ALFRED_TIMEZONE` env or UTC — not in scope for this ticket but worth a quick sanity check if regressions show up here.
