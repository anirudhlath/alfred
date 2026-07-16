# Live Telemetry: Events in Rail + Reconnect After Process Restart

**Feature:** TelemetryRail and ActivityPage live feed via /ws/telemetry  
**Priority:** high  
**Type:** integration

## Prerequisites

- Alfred runner fully started (`uv run python -m runner`)
- Home Assistant connected (or ability to trigger events manually via `POST /api/admin/triggers/{id}/fire`)
- Browser authenticated and on Chat page (`/`)
- Access to the terminal to kill/restart the channels process

## Test Steps

### Part A — Events appear in the TelemetryRail within 2 seconds

1. Navigate to Chat page (`/`). The TelemetryRail should be visible on the right side.
2. Observe the green pulse dot next to the "LIVE" label — confirms telemetry WebSocket is `"online"`.
3. Trigger an event: use the ⌘K palette → **Controls** → **DND on** (posts to `/api/admin/dnd`).
4. Within 2 seconds, observe a new entry in the TelemetryRail feed preview (colored `text-trigger` or `text-conscious`).
5. Navigate to Activity page (`/activity`) — the same event should appear at the top of the live feed with timestamp, category badge, and stream label.

### Part B — Multiple processes produce events visible in the rail

1. Trigger a Home Assistant state change (or fire a trigger that invokes a home action).
2. Within 2 seconds, observe a `home` (cyan) entry in the TelemetryRail.
3. Send a text message from Chat and observe a `user` (slate) entry and a `conscious` (green) response entry appear.
4. Confirm entries from different streams (`events`, `user_requests`, `user_responses`, `home_state`) all appear.

### Part C — Reconnect after killing the channels process

1. On the terminal, find the channels process PID and send SIGKILL (`kill -9 <pid>`).
2. Observe: the pulse dot in the TelemetryRail and the pulse dot in the IconRail Activity icon turn red (`bg-bad`).
3. Wait for the runner to restart the channels process (exponential backoff, ~2s).
4. Observe: `ReconnectingSocket` reconnects automatically; pulse dots return to green.
5. Confirm that new events from subsequent activity appear in the feed — confirming subscription was re-sent on reconnect.

## Expected Result

- Part A: DND change produces a visible entry in the TelemetryRail within 2s.
- Part B: Multi-stream events (home, user, conscious) are all color-coded correctly.
- Part C: After kill+restart, sockets reconnect without page reload; feed resumes; no manual intervention.

## Notes

- `TelemetrySocket.subscribe()` stores the subscription set and replays it in `onopen` on reconnect — subscribers never need to re-subscribe manually.
- The FEED_MAX ring buffer is 500 entries. On reconnect, new entries arrive from `$` (no history replay) — older entries in the buffer remain visible.
- The throttled overview refetch (5s gate, VITAL_CATEGORIES only) means the COST/SESSIONS/DND vitals update when a conscious/trigger/user event arrives, not on every home event.
- The pulse dot in `IconRail` at the Activity link reflects `telemetryStatus === "online"` — a quick visual indicator of connectivity.
