# Admin Controls: DND, Drain, Trigger Fire, Librarian Run

**Feature:** TriggersPage admin controls + CommandPalette controls  
**Priority:** high  
**Type:** integration

## Prerequisites

- Alfred runner fully started (all processes: conscious, channels, triggers, reflex)
- Browser authenticated and able to reach `http://localhost:8081`
- At least one trigger registered in Redis (fire a chat request that creates a trigger, or create one via the Conscious Engine)
- Redis CLI or logs accessible for verifying downstream effects

## Test Steps

### Part A — DND toggle affects notification deferral

1. Navigate to Triggers page (`/triggers`).
2. Locate the **DO NOT DISTURB** card in the right column.
3. Note the current DND state (should be OFF — toggle shows unchecked).
4. Toggle the switch to ON.
5. Observe: switch turns on; label changes to "ON". Toast "DND updated" appears.
6. On the Activity page, observe the overview vitals in TelemetryRail: DND shows "ON" (yellow).
7. Trigger a notification (e.g. send a chat message that triggers a cost alert, or use ⌘K → "DND on" for a second toggle test).
8. Navigate back to Triggers. In the **DEFERRED** card, observe the notification appears (deferred because DND is active).
9. Toggle DND OFF.
10. Observe: DND label returns to "OFF".

### Part B — Drain delivers deferred notifications

1. With at least one deferred notification in the **DEFERRED** card (from Part A or pre-staged):
2. Click **DRAIN NOW** (button in the DEFERRED card header).
3. Observe: toast "Drain queued" appears.
4. Within a few seconds, the deferred notifications should be delivered (visible in the chat panel as notification messages or in TelemetryRail as `trigger` category entries).
5. After drain, the **DEFERRED** card should show "Queue empty."

### Part C — Trigger fire executes action

1. In the **TRIGGERS** card, find a trigger with `trigger_type` visible (e.g. `event` or `schedule`).
2. Click **FIRE** on any trigger.
3. Observe: toast "Trigger fired" appears.
4. Navigate to Activity page and observe a new `trigger` (pink) entry for `trigger_fired` in the feed.
5. If the trigger has an associated action (non-null `action` field), also observe an `actions` stream entry for the `ActionRequest`.

### Part D — Librarian run logs consolidation

1. Open the ⌘K command palette (⌘K or Ctrl+K).
2. In the **Controls** group, click **Run Librarian now**.
3. Observe: toast "Done: /api/admin/librarian/run" appears.
4. In the server logs (or via the Activity page), observe a Librarian consolidation log entry within a few seconds (INFO level: "Librarian consolidation started" or similar).
5. After the run completes, navigate to Memory page (`/memory`) → **SCRATCHPAD** tab and confirm the pending queue count has decreased or is 0.

## Expected Result

- Part A: DND flag stored in Redis (`alfred:memory:dnd`); new notifications are deferred.
- Part B: `drain_deferred_notifications` ActionRequest published to `alfred:actions`; conscious process delivers queued notifications.
- Part C: `TriggerFired` or `ActionRequest` published to the appropriate stream; visible in Activity feed.
- Part D: Librarian consolidation triggered immediately (outside its 1-hour schedule); scratchpad observations processed.

## Notes

- DND toggle POSTs to `POST /api/admin/dnd` with `{"active": true/false}`. No `until`/`reason` are sent from the UI toggle (manual DND has no expiry).
- The DRAIN NOW button is disabled when the deferred queue is empty (`deferred?.notifications.length === 0`).
- Trigger enable/disable toggles (Switch components) call `POST /api/admin/triggers/{id}/enabled` and take effect within 60s (Trigger Engine cache refresh window).
- The **HISTORY** card shows the last 20 entries from the `notifications` stream via `GET /api/admin/streams/notifications?count=20`.
- The ⌘K palette's Controls commands (DND on, DND off, Drain, Run Librarian) are the same REST endpoints as the Triggers page controls — just keyboard-accessible.
