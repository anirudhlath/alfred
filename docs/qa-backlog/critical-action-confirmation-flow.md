# Critical Action Confirmation Flow (end-to-end)

**Feature:** Tiered autonomy — confirmation through the Door
(`web/src/door/`, `web/src/lib/actions.ts`)
**Priority:** critical
**Type:** e2e

## Prerequisites
- Full stack running (`python -m runner`), home-service registered with a `risk: critical`
  tool (e.g. a lock; Plan 2 risk map)
- Signed into the PWA client with a passkey, standing in the Room

## Test Steps
1. Ask Alfred to actuate a critical entity (e.g. "unlock the front door")
2. Observe the Door banner above the composer — a 34 px fuse ring, the humanised tool name,
   `expires in m:ss · asked by Alfred, for you`, and **Open**. Tap it
3. Observe the full Door: the kicker `CRITICAL · {short id}`, the 168 px fuse ring, the reason
   paragraph, the raw call box, a `‹ Leave it` way out, and a slide track hinting
   `Slide to confirm`
4. Drag the knob half-way and release
5. Drag the knob the whole way (past 85% of the track)
6. Watch the state pill after the POST returns, and again once the house reports
7. Repeat step 1, leave it alone past the TTL, and open the Door again
8. Repeat step 1, then confirm over chat instead: "yes, go ahead", and open the Door
9. Repeat step 1, kill the tab, cold-launch the client, and look at the Room
10. Repeat step 1 and open `/actions/{request_id}` directly in a new tab

## Expected Result
- Step 1: no actuation; the conscious reply says confirmation is required; the banner appears
  **above the composer**, not as a toast
- Step 3: title is derived client-side (`home.lock_unlock` → `Lock unlock`); the raw call box
  shows the tool and parameters actually requested
- Step 4: the knob snaps home and **nothing is confirmed** — a half-slide is not an approval
- Step 5: `POST /api/actions/{request_id}/confirm` fires once
- Step 6: the pill reads `Confirmed · queued` first, with the foot line
  "Sent to Home Assistant. Waiting for it to report (request `abcd`)." It becomes `Applied`
  **only** when the `home_action_results` telemetry stream reports — never on the 200
- Step 7: the pill reads `Expired`, and the Room carries a struck-through tombstone row
  reading `expired HH:MM · not done · asked HH:MM`
- Step 8: the confirm returns 404 (the conscious engine already consumed the pending action);
  the pill reads `Answered`, foot "answered elsewhere". No error is shown to the user as a
  failure — it was answered, just not here
- Step 9: the banner is back after the cold launch, fuse still counting — the
  `GET /api/actions/pending` read rebuilds it
- Step 10: the deep link opens the Room with the Door already over it, on the named action

## Notes
- v1 rule: confirmation is required even for direct user commands
- Three independent feeds keep the Door correct: the `pending-actions` read, a `/ws`
  `notification` frame carrying `metadata.pending_action_id`, and the `home_action_results`
  stream. Notifications without a `pending_action_id` are not confirmable and belong in the
  timeline instead
- The fuse ring is drawn from the server's `ttl_seconds`, not a hard-coded 300 s; under
  `DANGER_SECONDS` the arc changes colour, and nothing pulses (there are no haptics on iOS to
  pair with it)
- Offline, the slide is refused with "Cannot confirm while offline; the fuse is still running
  on the server." — worth one pass with the network off
