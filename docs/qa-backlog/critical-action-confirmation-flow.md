# Critical Action Confirmation Flow (end-to-end)

**Feature:** Tiered autonomy — confirmation through the Door
(`web/src/door/`, `web/src/lib/actions.ts`) and in the conversation
**Priority:** critical
**Type:** e2e

The server half of this flow (park, notify, reply or Door decision, resume, execute) is
scored by the `critical_actions` goldens ([`evals.md`](../evals.md)); none of them drives
the PWA. This list is what is left: the Door's banner, fuse, slide and **Don't**, the
offline and retry behaviour, and how a follow-up lands in the Room. Flow and diagram:
[`autonomy.md`](../autonomy.md#confirmation-flow-for-critical-actions).

## Prerequisites
- Full stack running (`python -m runner`), home-service registered with a `risk: critical`
  tool (e.g. a lock; Plan 2 risk map)
- Signed into the PWA client with a passkey, standing in the Room

## Test Steps
1. Ask Alfred to actuate a critical entity (e.g. "unlock the front door")
2. Observe the Door banner above the composer — a 34 px fuse ring, the humanised tool name,
   `expires in m:ss · asked by Alfred, for you`, and **Open**. Tap it
3. Observe the full Door: the kicker `CRITICAL · {short id}`, the 168 px fuse ring, the reason
   paragraph, the raw call box, a `‹ Leave it` way out, a **Don't** button, and a slide track
   hinting `Slide to confirm`
4. Drag the knob half-way and release
5. Drag the knob the whole way (past 85% of the track)
6. Watch the state pill after the POST returns, and again once the house reports; then look
   at the Room's thread
7. Repeat step 1, leave it alone past the TTL, and open the Door again
8. Repeat step 1, then approve in the conversation instead: "yes, go ahead", and open the Door
9. Repeat step 1, then refuse in the conversation: "no, don't", and open the Door
10. Repeat step 1, kill the tab, cold-launch the client, and look at the Room
11. Repeat step 1 and open `/actions/{request_id}` directly in a new tab
12. Repeat step 1, open the Door, and press **Don't**; then look at the Room's thread
13. Repeat step 1, open the Door with the network off (or the channels process stopped) and
    slide to confirm; bring the network back before the fuse runs out and slide again
14. Repeat step 1, then force the house to fail (stop the home service, or use a device that
    refuses) and confirm
15. Repeat step 1 and, after confirming, background the app for a minute (or let the socket
    drop and reconnect) before the follow-up reply is due

## Expected Result
- Step 1: no actuation; the conscious reply says confirmation is required ("Before I do that,
  I need your confirmation: ... Shall I, sir?"); the banner appears **above the composer**,
  not as a toast
- Step 3: title is derived client-side (`home.lock_unlock` → `Lock unlock`); the raw call box
  shows the tool and parameters actually requested
- Step 4: the knob snaps home and **nothing is confirmed** — a half-slide is not an approval
- Step 5: `POST /api/actions/{request_id}/confirm` fires once
- Step 6: the pill reads `Confirmed · queued` first, with the foot line
  "Sent to Home Assistant. Waiting for it to report (request `abcd`)." It becomes `Applied`
  **only** when the `home_action_results` telemetry stream reports — never on the 200. A few
  seconds later an Alfred row in the thread says what happened; it arrives on its own, as a
  follow-up, and does not clear a turn in flight
- Step 7: the pill reads `Expired`, and the Room carries a struck-through tombstone row
  reading `expired HH:MM · not done · asked HH:MM`
- Step 8: Alfred answers in the conversation and unlocks; the pill reads `Applied` once the
  house reports (the result is published under the approval id however it was approved). A
  slide that still goes out afterwards gets a 404 and reads `Answered`, foot "answered
  elsewhere", with no error shown — it was answered, just not here
- Step 9: Alfred does nothing and says so; the record is gone, so a slide on the Door returns
  404 and the pill reads `Answered`
- Step 10: the banner is back after the cold launch, fuse still counting — the
  `GET /api/actions/pending` read rebuilds it
- Step 11: the deep link opens the Room with the Door already over it, on the named action
- Step 12: `POST /api/actions/{request_id}/deny` fires once; the pill reads `Declined`, foot
  "You declined it. Nothing was done."; the run is told, and Alfred's follow-up in the thread
  acknowledges the decline. Nothing is actuated
- Step 13: the slide is refused offline with "Cannot confirm while offline; the fuse is still
  running on the server." Back online, while the fuse still runs, the same item is still
  `pending` and the second slide goes through. A failed POST that is not a 404 (a 503, no
  network) leaves the item pending and writes `door: could not answer {id}` to the console
- Step 14: the pill reads `Failed`, foot "... reported error ... Ask again to try once more."
  — the owner said yes and the house said it could not. If a late result lands after a decline
  (step 12), the pill shows what the lock did, not `Declined`
- Step 15: the follow-up reaches the Room if the reconnected socket has already sent a
  message; otherwise it arrives as an `Alfred` notification instead (a reconnected socket is
  not back on its session until it sends one — a known limitation,
  [`notifications.md`](../notifications.md#follow-ups-late-answers-and-door-resumes))

## Notes
- v1 rule: confirmation is required even for direct user commands, and the model cannot
  approve its own action — there is no tool for it
- Three independent feeds keep the Door correct: the `pending-actions` read, a `/ws`
  `notification` frame carrying `metadata.pending_action_id`, and the `home_action_results`
  stream. Notifications without a `pending_action_id` are not confirmable and belong in the
  timeline instead
- The fuse ring is drawn from the server's `ttl_seconds`, not a hard-coded 300 s; under
  `DANGER_SECONDS` the arc changes colour, and nothing pulses (there are no haptics on iOS to
  pair with it)
- A decision made on the Door of a System 2 run is answered by the run, not by the server: the
  reply in step 6 and step 12 comes from the resumed conversation
