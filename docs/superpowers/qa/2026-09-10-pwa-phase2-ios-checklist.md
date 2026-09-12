# PWA phase 2 — manual iOS checklist

Spec §7's manual half, for the Workshop and the Activity bench. The phase-1 checklist
(`2026-09-07-pwa-phase1-ios-checklist.md`) still applies to the Room and the Door; this
one is only what phase 2 added. Gate (spec §8): *debugging is usable on the phone*.

Run against the deployed build at `https://alfred.example.com`, signed in, with a house
that is running and writing events. Record the device, iOS version and date at the bottom.

## Before you start

- [ ] `npm run build` output is what is deployed (`git log -1` on the deploy host)
- [ ] Something in the house produces events on demand (flip a light; the
      `home_state` stream should move)
- [ ] A reflex observation with an action exists from today (start a media player
      with a reflex rule on it, or wait for one)

## The handle and the layer

- [ ] Under the composer: a small chevron, mono `workshop`, and the bar. The whole
      thing is one tap target at least 44 px tall
- [ ] Tap the field: the keyboard rises and the handle is **gone**, the composer sits
      on the keyboard with no empty strip below it. Dismiss the keyboard: the handle is
      back and the home-indicator gap is not doubled
- [ ] Tap the handle: the Workshop rises over the Room in about 400 ms and the Room
      cannot be tapped through it
- [ ] `‹ Room` closes it, the Room is exactly as it was left (scroll position, draft
      in the field)
- [ ] Reduce Motion on: the Workshop cross-fades in about 200 ms instead of rising
- [ ] With the Workshop up, produce a pending critical action: **nothing interrupts.**
      Nothing in the build opens the Door unprompted — the banner waits in the Room
      underneath, and `‹ Room` reveals it there. Tapping the banner opens the Door over
      the Room, as always
- [ ] The one thing that raises the Door from outside the Room is the `/actions/:id`
      deep link. There is no notification to tap yet — Web Push is phases 4 and 5 — so
      open `https://alfred.example.com/actions/<request_id>` directly while the approval
      is still pending: the Door is open over the Room on arrival. It arrives as a fresh
      page load, so the app starts over and the Workshop is closed behind it; that is
      the build, not a defect

## The status line

- [ ] Reads `live · N ev/s` with the socket up. A quiet house reads a bare
      `live · 0 ev/s`, not `0.0`; `live · — ev/s` means the overview has not answered
      yet or came back with no streams at all, which is Redis down rather than quiet
- [ ] Pause: reads `paused · 0 new`, then counts as events arrive
- [ ] Turn off Wi-Fi and mobile data: within a few seconds it reads
      `last true HH:MM · not live` with a real time, and the bench shows the stale
      banner `Feed stopped at HH:MM. Nothing below is live.` Turn the network on: both
      clear on their own, and rows arrive again
- [ ] Open the Workshop while already offline: the banner reads
      `Feed has not been live yet. Nothing below is live.`

## The bench

- [ ] Eight chips in the handoff's order (`UR AL EV AC RX NT HS HR`), each with a count
- [ ] Flip a light: an `HS` row appears at the top within a second — monogram in the
      stream's hue, the line in sans, the stamp and meta in mono
- [ ] Tap a chip: only that stream's rows remain and the chip reads as selected; tap it
      again: all eight are back. The list returns to the top each time — a different
      stream is a different list
- [ ] Tap a row: it expands to its payload, wrapped, never wider than the screen; the
      RX row carries `Why · causal thread`; tap again to collapse
- [ ] Open a row and keep reading it while events arrive at the top: the payload stays
      put on the screen and does not walk down under the new rows
- [ ] An observation the Reflex did not act on reads `observed … · watched, took no
      action` (spec §10) — the Room never shows these, the bench always does
- [ ] `Pause feed`: new rows stop appearing and the status counts them; `Resume`: they
      slot in at the top, newest first, none lost, none twice
- [ ] `↑ older` at the top of the list: its label names the cursor it will read before;
      tapping appends older rows at the **bottom** and moves the label; the list does
      not jump to the top. It disappears when nothing can page further
- [ ] Solo a stream with three entries: no `↑ older` button
- [ ] A stream that has never been written reads `XX · 0 entries · nothing has been
      written` when solo'd
- [ ] Scroll the list to the bottom: the footer sits above the home indicator with a
      clear gap; the list does not bounce the whole layer
- [ ] Background the app for two minutes while the house is busy, return: the top of
      the list catches up on its own, no manual refresh; no row appears twice
- [ ] Walk the four tabs with an external keyboard's ← and →: the selection follows the
      focus, wraps past System back to Activity, and one Tab press reaches the control
      rather than four

## Why Alfred did that

- [ ] In the Room, a reflex act row (RX mark) carries `why?` at the right; a
      notification row (NT mark) does not
- [ ] Tap `why?`: the sheet rises **over** the Room in about 380 ms; its title is
      `Why Alfred did that`, then the intro, then `reading 8 streams…`, then the column
- [ ] The column: the state change that triggered it, the action, its result, and the
      observation — each with a monogram in its stream's hue, time to the second, and a
      meta line ending in how it was joined (`joined by request_id 4b1d` /
      `this row` / `adjacent in time only`); solid connectors between joined rows,
      dashed to and from adjacent ones
- [ ] The footnote reads `searched 8 streams · 100 entries each · ±10 min`. On a busy
      house it may end `· 1 stream could not be read back far enough` (or
      `· 3 streams …`) — that is the sheet being honest about its own reach, not a
      failure
- [ ] From the bench, open the thread of a passive observation — an
      `observed … · watched, took no action` row. On a quiet house the column is **two**
      rows, not one: the `HS` state change that caused it, `joined by event_id …`, then
      the `RX` anchor reading `this row`. A passive observation still carries the
      originating event's id, so it joins
- [ ] To see the lone-anchor line you need an anchor with nothing left to reach: open
      an old observation whose `HS` row has already aged past that stream's hundred
      entries (page well back with `↑ older` on a busy house first). The column is the
      single row and the sheet says
      `Nothing else in the eight streams is joined to this row.`
- [ ] Close the sheet and re-open the same row straight away: the column is there at
      once, with no `reading 8 streams…` pass
- [ ] `Done` and the scrim both close it; the Room is under it unchanged
- [ ] From the Workshop, expand an RX row and tap `Why · causal thread`: the same sheet
      opens **over the Workshop**; `Done` returns to the Workshop with the row still
      expanded
- [ ] Turn the network off, tap `why?`: the sheet shows the intro and a one-line
      failure — the browser's own message, which on Safari is `Load failed` — then no
      column, and it still closes. `Unreachable.` is the Room's headline and never
      appears here

## Nothing lies

- [ ] Nowhere does the Workshop say a word of **system state** outside spec §10 and the
      handoff's status line (`live`, `paused`, `last true … · not live`). Plain English
      about the screen itself is not system state and is expected —
      `not built yet · phase 3`, `nothing has been written`,
      `N of 8 streams could not be read`
- [ ] Nothing pretends to be a badge, a count of unread, or a throughput meter beyond
      `ev/s`
- [ ] No screen is reachable that has no way out

---

Device: ______________  iOS: ______  Build: ______________  Date: ____________
Tester: ______________
