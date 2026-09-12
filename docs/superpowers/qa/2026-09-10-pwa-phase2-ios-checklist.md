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
- [ ] With the Workshop up, produce a pending critical action: the Door rises **over**
      the Workshop; leaving it returns to the Workshop, not the Room

## The status line

- [ ] Reads `live · N ev/s` with the socket up (`N` may be `0.0` on a quiet house)
- [ ] Pause: reads `paused · 0 new`, then counts as events arrive
- [ ] Turn off Wi-Fi and mobile data: within a few seconds it reads
      `last true HH:MM · not live` with a real time, and the bench shows the stale
      banner `Feed stopped at HH:MM. Nothing below is live.` Turn the network on: both
      clear on their own, and rows arrive again
- [ ] Open the Workshop while already offline: the banner reads
      `Feed has not been live yet. Nothing below is live.`

## The bench

- [ ] Eight chips in the handoff's order (`UR AL EV AC RX NT HS HR`), each with a count
- [ ] Flip a light: an `HS` row appears at the top within a second, monogram in the
      stream's hue, line and meta in mono
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
      house it may end `· N stream(s) could not be read back far enough` — that is the
      sheet being honest about its own reach, not a failure
- [ ] Open a passive observation's thread (one with no action, from the bench): the
      column is the single row and the sheet says
      `Nothing else in the eight streams is joined to this row.`
- [ ] Close the sheet and re-open the same row straight away: the column is there at
      once, with no `reading 8 streams…` pass
- [ ] `Done` and the scrim both close it; the Room is under it unchanged
- [ ] From the Workshop, expand an RX row and tap `Why · causal thread`: the same sheet
      opens **over the Workshop**; `Done` returns to the Workshop with the row still
      expanded
- [ ] Turn the network off, tap `why?`: the sheet shows the intro and a one-line
      failure (`Unreachable.` or the fetch error), no column, and still closes

## Nothing lies

- [ ] Nowhere does the Workshop show a word outside spec §10 and the handoff's status
      line (`live`, `paused`, `last true … · not live`)
- [ ] Nothing pretends to be a badge, a count of unread, or a throughput meter beyond
      `ev/s`
- [ ] No screen is reachable that has no way out

---

Device: ______________  iOS: ______  Build: ______________  Date: ____________
Tester: ______________
