# PWA phase 2 follow-ups

**Priority:** low
**Source:** `docs/superpowers/plans/2026-09-10-pwa-phase2-workshop-and-activity.md` —
the decisions it took knowingly and the limits it shipped with.

## 1. The Activity bench is plain DOM

Carried from `pwa-phase1-followups.md` §4. The cap in `web/src/lib/feed.ts` is
`MAX_PER_STREAM = 400` *per stream*, and it binds only until the reader presses `↑ older`:
that mode returns the union untrimmed on purpose — "growing downward is what the user
asked for" — and `highWater()` then raises the ceiling to whatever depth was fetched. So
the bound on mounted `EventRow`s is **400 per stream until `↑ older`, unbounded after** —
roughly another 400 rows per press with all eight streams at the horizon, each with its
own expand state, none virtualised, and a `useLayoutEffect` measuring the list's
`scrollHeight` on every render. Nothing gives them back but closing the Workshop.

Fine on an iPhone 15 with a house that writes a few events a second; measure on the oldest
phone in use before raising the cap. **Acceptance:** scroll stays smooth at the un-paged
400-per-stream depth *and* after ten presses of `↑ older` on a busy house — or the list
moves to `@tanstack/react-virtual` with the expand state lifted out of the rows and a
depth bound of its own on what `↑ older` may accumulate.

## 2. Causality is a client-side heuristic

Spec §7 says so; this is the ticket for the other half. `web/src/lib/trace.ts` joins on
ids the events carry (`event_id`, `request_id`, `session_id`, `actions_taken`,
`entity_id`) within a page of 100 per stream and a ±10-minute window, and marks up to six
nearby unjoined entries as `adjacent in time only`.

`trigger_id` is on none of those lists on purpose: it names the trigger, not the firing,
so two firings of one recurring trigger inside the window would join — and because the
walk is transitive it would carry on from the second firing to its own observation,
action and result, drawing solid connectors across two unrelated episodes. `event_id` is
the join that means *this* firing: a reflex observation carries the originating event's
full dump as `trigger_event`, so a fired trigger reaches the observation it caused
through the event id it was written with. A server correlation id would make this
unnecessary rather than merely careful.

What the heuristic still cannot do: it cannot see a cause more than 100 entries back in a
busy stream (`home_state` on a large house) — the sheet now admits that much, appending
`· N stream(s) could not be read back far enough` to its footnote for every stream whose
page both filled and stopped inside the window, but admitting it is not finding it. It
cannot tell an entity's state change *caused by* an action from one that merely followed
it within a minute. And it never says "not caused by". **Acceptance:** the bus stamps a
`correlation_id` on every event a request fans out into (`bus/schemas/events.py`,
`core/reflex/runner.py`), `/api/admin/streams/{name}` can filter by it, and the sheet's
solid links become that id — the heuristic stays for events from before the stamp.

## 3. Memory, Triggers and System are tabs that say so

`BenchSwitcher` has all four tabs; three render `not built yet · phase 3`. Phase 3's plan
replaces the placeholder in `Workshop.tsx`'s `WorkshopPanel` and takes the rest of spec
§10's vocabulary (`unknown since`, `takes effect within 60 s`, `hot / cold`,
`candidate · active · dormant · archived`) with it. Telemetry `status` / `error` frames
(`redis_error`, `invalid JSON`) reach only the console until System exists.

One thing moves with them. The Workshop's status line is deliberately not a live region —
everything it says is announced better elsewhere, and the Activity bench's "Feed status"
banner is what speaks a socket drop. On the other three benches that banner is unmounted
with the bench, so nothing announces one. The live region should move up into the header
when those three land (there is a comment in `Workshop.tsx` saying so).

## 4. Stream names, again

`pwa-phase1-followups.md` §3, one copy larger: `STREAMS` in `web/src/lib/streams.ts` now
carries the chip order, the monograms and the hues, next to `ROOM_STREAMS` in
`history.ts` and `RESULT_STREAM` in `DoorProvider.tsx`. A rename in
`core/channels/stream_catalog.py` still fails silently on the client.

## 5. The Workshop's status rate is the overview's

`live · N ev/s` is `rateText()` over the overview's `streams` counts (`evs()` in
`lib/format.ts`), polled every 30 s — the same number as the Room's status line, not a
rate measured from the frames the feed is receiving. Good enough to tell live from dead;
not a throughput meter.

## 6. `feed.ts` never caps `held`

`MAX_PER_STREAM` guards `entries` only. Every live frame that arrives while the feed is
paused is appended to `FeedState.held` with no limit (`web/src/lib/feed.ts`, the `live`
case), so a pause left on overnight grows the array unbounded, the footer reads
`Resume · 12480 new`, and Resume merges all of it through `withLive` in one dispatch.
Nothing is wrong with the result — the trim happens per stream on the way in — but the
memory in between is the whole night, and the count is a number no one can act on.
**Acceptance:** `held` is capped the way `entries` is, oldest dropped, and the label says
what the cap means (`Resume · 400+ new`).

## 7. The unaudited `.t-meta` sites

Phase 2 added `.t-meta-strong` (`--fg2`) and moved eight sites onto it, but the roughly
seventeen `.t-meta` uses `index.css` flags are still `--muted` — 3.46:1 on `--bg` in the
light theme, under AA at 11 px. `web/src/sheets/HeldBackSheet.tsx:85` is a confirmed
failure at 14.5 px: "Nothing is being held back." is the only sentence in the sheet when
the queue is empty, and it is set in `--muted` directly.

This is not a find-and-replace. Some of those sites sit on `--ink` — the Door's inverted
surface — where `--muted` is the wrong *family* of token and `--paper-muted`, not
`--fg2`, is the answer. **Acceptance:** every one measured in both themes against the
ground it actually sits on, and each either moved to `.t-meta-strong` (or
`--paper-muted`) or written down as decorative under the rule `index.css` states.

## 8. `FuseRing`'s arc is `--accent` on `--ink`

The same 1.77:1 pair in the dark theme that `--on-accent` was added to fix
(`web/src/door/FuseRing.tsx`). WCAG 1.4.11 does not bite: the arc is an `aria-hidden`
stroke and both call sites carry the countdown as text beside it, which is the
text-alternative exemption. But the fuse reads faint on ink above `DANGER_SECONDS`, which
is most of a two-minute fuse — under thirty seconds it flips to `--paper` and is fine.
**Acceptance:** an `--accent-on-ink` decision taken alongside the `.t-meta` audit above,
so the Door's palette is settled in one pass rather than two.

## 9. The Workshop's `memo` guard is documentary

`Room.tsx`'s `closeWorkshop` `useCallback` and `memo(Workshop)` are load-bearing
together: without both, every Room render — a chat frame, the 30 s overview poll, the
Door's once-a-second `now` while a fuse counts — walks `WorkshopPanel` →
`ActivityBench` → every mounted `EventRow`, all of them deliberately unmemoised, each
re-running `summarise`. That is 400 of them per stream with nothing solo'd — the cap is
per stream and `mergeRows` merges eight — and no ceiling at all once `↑ older` has been
pressed (§1). Removing either leaves all 812 tests passing. The only thing protecting the
pair is the comments in `Workshop.tsx` and `Room.tsx` pointing at each other.
**Acceptance:** a render-count assertion on `EventRow` under an unrelated Room re-render,
or a lint rule — worth writing the first time this regresses, not before.

## 10. `history.ts` duplicates the JSON helpers

`web/src/lib/history.ts` carries its own `str` and `record` alongside `scalar`, `record`
and `strings` in `streams.ts`, and inlines a third copy of the string-array filter in
`alfredItem`. `record` is verbatim identical; `str` and `scalar` are *not* — `scalar`
trims and accepts numbers and booleans, `str` does neither — so this is a merge with a
behaviour decision in it, not a deletion. **Acceptance:** one set, either imported from
`streams.ts` or lifted to a shared `lib/json.ts`, with the trim-and-coerce question
answered once and the Room's rows re-tested against it.

## 11. The Room has no conversation-turn `why?`

Spec §5.1 defines Causality as "correlate one conversation turn with the system activity
it caused", and §7 names `user_requests` / `user_responses` among the streams it is drawn
from. Phase 2 answers that in the Workshop only: `WHY_STREAMS` in
`web/src/workshop/ActivityBench.tsx` offers `Why · causal thread` on `user_responses` (AL)
rows as well as reflex observations, because a reply's `actions_taken` names the tools it
ran and `trace.ts` joins that to an action's `tool_name` — a server-held cause, which is
the bar the plan's decision 4 set.

The Room's own chat bubbles do not offer it. An Alfred bubble is the same turn seen from
the other end, and it is where a user would look first — but putting a pill on it is a
change to the handoff's visual for the Room's most-looked-at surface (bubble geometry, the
`why?` affordance the act rows already own, and what a turn with no tools should do), not
a wiring change. Deferred on purpose rather than improvised in a review.

**Acceptance:** an Alfred bubble whose reply named at least one tool offers the same
sheet, drawn as the handoff would draw it, with a turn that ran no tools offering nothing
rather than an empty thread.
