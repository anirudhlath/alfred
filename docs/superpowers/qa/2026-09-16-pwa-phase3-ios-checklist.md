# PWA phase 3 — manual iOS checklist

Spec §7's manual half, for the Memory, Triggers and System benches. The phase-1
checklist (`2026-09-07-pwa-phase1-ios-checklist.md`) still applies to the Room and the
Door, and `2026-09-10-pwa-phase2-ios-checklist.md` to the Workshop shell and the Activity
bench; this one is only what phase 3 added. Gate (spec §8): *full capability parity with
the old SPA*.

Run against the deployed build at `https://alfred.example.com`, signed in, with a house
that is running and writing events. Record the device, iOS version and date at the
bottom.

## Two things already measured — do not re-derive them

Both were measured on a real iPhone in **standalone** mode and are settled:

- `window.innerHeight === visualViewport.height` even with the keyboard up. iOS slides
  the webview itself, invisibly to JS.
- `env(safe-area-inset-top)` is `0`.

So: **never compute a keyboard inset by subtracting the two viewports**, and never
compute one from the safe-area insets either. Anything that has to know about the
keyboard keys off the focused field. No step below asks you to measure either of these
again; the steps ask what the *user* sees.

## Before you start

- [ ] `npm run build` output is what is deployed (`git log -1` on the deploy host)
- [ ] Add the app to the Home Screen and run every step from there, not from Safari —
      standalone is the only mode the two facts above were measured in
- [ ] The house has at least one trigger of each kind on the chips: a one-shot with a
      `run_at`, a recurring one with a `cron`, a sensor and a composite. At least one
      one-shot has **already fired**
- [ ] The house has at least one learned routine and one semantic document, and a memory
      you know the wording of, to search for
- [ ] There is a second signed-in session (another device, or the same device in Safari)
      so Sessions shows a row that is not `current`
- [ ] You can take the phone **off the home network** — mobile data with Wi-Fi off — and
      you can drop the network entirely with Airplane mode
- [ ] Set the phone's text size to the largest Dynamic Type step for the width steps
      below, then back afterwards

## All four benches

- [ ] The switcher walks Activity · Memory · Triggers · System. Nothing anywhere reads
      `not built yet`
- [ ] First paint of **Memory**: the four sub-tabs, the Episodic list or its empty
      sentence, the search field and `model: unknown` at the bottom. Nothing flashes a
      claim about an empty house on the way in
- [ ] First paint of **Triggers**: five chips with a count beside `All`, the rows, and
      the two-line footer note
- [ ] First paint of **System**: Health with its 2×2 grid and a ticking `live · HH:MM:SS`
      stamp, then Quiet, Maintenance, Sessions, Connected services, Devices & identity,
      Reflex — in that order
- [ ] Leave a bench and come back: Memory's sub-tab and search results, Triggers' kind
      chip and open row, and any queued note are all still there. The Activity list is
      back at the top with the row still expanded — that is known and filed
      (`docs/backlog/low/pwa-phase3-followups.md` §15), not a defect to raise again
- [ ] Type into a credential field on System, switch to Memory and back: the field is
      **empty**. A half-typed secret must not survive the trip

## Memory

- [ ] Episodic: rows read as one shape whatever store they came from — the sentence,
      then `HH:MM <day> · significance 0.40 · recalled 2× · hot` (or `cold`). A row from
      last week says which day; none of them says `Invalid Date` or a 1970 stamp
- [ ] Tap the search field. **The layer must not zoom.** The page does not scale, the
      header stays put, and nothing is clipped off the top of the screen
- [ ] With the keyboard up at 360 px: the field and the `model:` pill are both fully on
      screen, the field is not under the keyboard, and the list above it still scrolls
- [ ] Type a phrase and press the keyboard's **search** key: the list becomes matches,
      each with `match 0.62` at the end of its meta line, and the pill flips to
      `model: ok` in green. Typing alone, with no submit, must not fire a search
- [ ] Search for gibberish: `Nothing close enough to "<your gibberish>".` The list does
      not go blank behind an error, and no score or threshold is quoted
- [ ] Clear the field and submit: back to browse, newest first
- [ ] **`decaying` appears only on `hot` rows**, drawn after the store and *after* the
      recall count, not instead of it:
      `HH:MM <day> · significance 0.34 · never recalled · hot · decaying`. A cold row never
      carries it, however lightly it is weighed — decay is what moved it to cold. Worth
      knowing while you look: nothing is actually being swept today, because the
      Librarian's threshold is unreachable
      (`docs/backlog/high/librarian-decay-threshold-unreachable.md`), so a marked row will
      still be there tomorrow. That is the server's, not the bench's
- [ ] **A `cold` row says nothing about recalls** — its meta line runs
      `HH:MM <day> · significance 0.40 · cold`, with no `never recalled` and no count,
      because the cold store has no such column and its search path invents one
      (`…/pwa-phase3-followups.md` §16). A `hot` row *does* carry the clause. Any
      `recalled N×` beside the word `cold` is a regression
- [ ] Semantic: cards unfold to their Markdown and fold again. Leaving the bench and
      returning folds them — that is deliberate
- [ ] Routines: the lifecycle rail shows `candidate · active · dormant · archived` with
      the routine's own stage marked; the sparkline's caption names the number of
      readings it actually drew, never a round "last 8"
- [ ] Scratchpad: two stat cards — the unscored queue, and the next consolidation with
      the last one under it
- [ ] Nothing on this bench offers to forget, redact, edit or promote anything

## Triggers

- [ ] **The kind chips at 360 px.** Five `flex-1` chips share the width, about 60.8 px
      each. `Composite` is 13 px medium and is right at that width: check it neither
      clips nor wraps to a second line, and that the chip is still 32 px tall in its
      44 px track. Repeat at the largest Dynamic Type step, and in landscape
- [ ] `All` carries the house's own count. The other four carry no number
- [ ] Tap a chip: only that kind remains, and the chip reads as pressed. A filter that
      empties the list says `No <kind> triggers.`, never `No triggers yet.`
- [ ] A one-shot that has already fired **recedes but stays readable** — it dims by
      colour, not by a whole-row fade, and you can still read its name and meta at
      arm's length
- [ ] Meta lines: a one-shot reads `one-shot · runs 08:40 tomorrow`; a recurring one
      prints its cron verbatim. Neither invents a next fire time
- [ ] Tap a row's body: it expands to the action and conditions, wrapped, never wider
      than the screen. **Expanding must not toggle the trigger** — the switch has not
      moved
- [ ] **Toggle a trigger.** The switch does *not* move. A note appears under the meta
      line in accent mono: `queued HH:MM · enabling · takes effect within 60 s` (or
      `disabling`). The row does not jump, re-sort or resize the list around it
- [ ] Tap the switch again inside that window: nothing happens — no second request, no
      flicker. With VoiceOver on, the control announces as dimmed but is still focusable
      and still reads its queued note
- [ ] Wait out the 60 s: the list re-reads, the note goes, and the switch is now in the
      state the server holds. If the change did not take, the switch goes back to where
      it was — it must never keep showing what you asked for
- [ ] **Toggle over a dropped connection.** Airplane mode on, tap a switch: the row keeps
      its old state and the note becomes a failure sentence, not a queued one. No number
      is quoted when nothing answered. Airplane mode off: the next read settles it, and
      no phantom change appears
- [ ] `Fire now` on a safe trigger: the button becomes `Fire again` and its note reads
      `queued HH:MM · look for trigger.fired on the events stream to know it ran`. It
      never says `Fired`. Check the Activity bench: the firing is on the events stream
- [ ] The footer's two sentences are both there, and the second one
      (`Nothing here edits a trigger…`) is legible without zooming

## System

- [ ] Health: four cells, each a dot above a mono value. With the house up, the dot for
      a live cell is filled and the value is at full strength; the stamp beside the title
      ticks `live · HH:MM:SS`
- [ ] **DND on from System.** The switch moves only after the read comes back — a visible
      beat after the tap, which is the point. The sub-line becomes `on · …` and a note
      reads `applied`
- [ ] Go back to the Room (`‹ Room`): the Room's DND row agrees, with the same expiry.
      Turn it off from the Room, open the Workshop again: System agrees. The two must
      never disagree
- [ ] With DND on, the `Quiet until` chips appear and the one you chose is marked;
      with it off, the chip row is gone entirely
- [ ] Those four chips at 360 px: `until 22:00` is the long one, and they share the card's
      width four ways inside a section that is itself inset. Check it neither clips nor
      wraps, at the default text size and at the largest Dynamic Type step — the same
      question the Triggers chips ask, one card narrower
- [ ] The calendar footnote is present and legible:
      `A meeting in your calendar can also quiet Alfred; that is not shown here.`
- [ ] `Held back` row shows the count and opens the sheet **over the Workshop**; `Done`
      returns to System exactly as it was. Then close the Workshop and open the same
      sheet from the Room's DND row: it opens over the Room. One sheet, two doors
- [ ] `Send them now`: the note stamps `queued HH:MM · the notifier sends them when it
      next reads the queue`. Known gap: opening the sheet afterwards still offers
      `Drain queue now` (`…/pwa-phase3-followups.md` §14) — confirm it is only the words
      that disagree and nothing is sent twice
- [ ] Maintenance: `Run consolidation now` becomes `Run again` with a queued note.
      Nothing claims the consolidation finished
- [ ] Sessions: your own row reads `current` and offers no End. Another session's row
      ends on tap, recedes to a dimmed `ended HH:MM · applied`, and is gone on the
      re-read. The ended device really is signed out
- [ ] Connected services: each row's state word and dot agree — filled dot for a service
      that is answering, outline for one that is not. Tap a row: the credential form
      opens, one field per schema entry, secrets masked, and a saved field shows the
      placeholder `saved` and **never** a value
- [ ] **The credential form at 360 px with the keyboard up.** Every field and the
      `Save & test` button are reachable by scrolling; nothing is trapped under the
      keyboard; the labels are not truncated; and the form does not zoom the layer on
      focus. Repeat at the largest Dynamic Type step
- [ ] **`Save & test` from off the home network** (Wi-Fi off, mobile data only): the row
      says `Credentials can only be changed from the home network.` and **keeps what you
      typed**. The Denied gate also rises over the layer; dismiss it and the bench, the
      row and the typed values are exactly as they were. Every other read on the bench
      kept working throughout
- [ ] Back on the home network, `Save & test` on a real service: the button reads
      `Testing…`, then the row's state word and dot follow the probe's answer
- [ ] **The pairing code at arm's length.** `Add a passkey on another device`: the code
      is mono, large and letter-spaced — hold the phone at normal reading distance and
      type it into a second device without leaning in. Its note says when it expires
- [ ] Leave the bench and come back: the code is **gone**, and there is no way to
      re-show it without minting a new one
- [ ] `N registered` appears only once the passkey list has actually been read
- [ ] Reflex: the attention set's domains, each with its allow and ask members. Changing
      one shows its own busy state, and a failure is attached to that domain rather than
      to the section

## Under a 60 s socket outage

Airplane mode on, then wait a full minute on each bench before turning it off.

- [ ] **Activity**: the header reads `last true HH:MM · not live` and the bench's stale
      banner says the feed stopped
- [ ] **Memory**: rows already on screen stay. A read that is refused says
      `<subject> could not be read.` — never `No episodic memories yet.` A search
      attempted offline fails without emptying the browse list underneath it
- [ ] **Triggers**: rows stay; the `Read errors` line appears above them; no row silently
      flips state
- [ ] **System**: the Health grid's values fall back to `?`/`—`, the whole grid recedes
      to a dimmed colour, **every dot goes out** — including one that was green a moment
      ago — and the stamp turns to `unknown since HH:MM` in accent. A green light over a
      dimmed number is the failure this step exists to catch
- [ ] Network back: all four recover on their own, with no manual refresh and no
      duplicate rows

## VoiceOver

- [ ] **One announcement per socket drop, not two.** VoiceOver on, sit on the Activity
      bench and drop the network: you hear the state change **once**. Repeat from Memory,
      Triggers and System — you still hear it once from each, because the announcement
      now lives in the Workshop's header rather than in Activity's banner
- [ ] **Three minutes on Activity with VoiceOver on, doing nothing.** The house must be
      writing events, so the rate and the held count are moving. Count the announcements
      from the header: there should be **none** — the state word has not changed, and the
      numbers beside it are outside the live region. `role="status"` implies
      `aria-atomic="true"`, and jsdom cannot tell whether iOS diffs the accessibility
      tree or the DOM, which is why this is a stopwatch step and not a unit test. Any
      repeat of `live` is a finding; write down how often it repeated
- [ ] Pause the feed and wait another minute: still nothing announced, even though the
      held count is climbing
- [ ] With a read failing, each bench's `Read errors` line is announced once, and it is
      distinguishable from the connection announcement above it
- [ ] Walk a trigger row with VoiceOver: name, kind, meta, then the switch announcing its
      **stored** state and its queued note. Nothing announces a state the server has not
      confirmed
- [ ] The Workshop's tab set still behaves: four tabs, one stop, ←/→ move and wrap

## Nothing lies

- [ ] No bench says a word of system state outside spec §10's vocabulary: `queued`,
      `applied`, `last true HH:MM`, `unknown since HH:MM`, `hot / cold`,
      `candidate · active · dormant · archived`, `expired · not done`,
      `takes effect within 60 s` — plus the status line's `live` / `paused`. Plain
      English about the screen itself is fine and expected
- [ ] Nothing green is showing next to anything the app is no longer reading
- [ ] No control claims an outcome the server only queued
- [ ] No empty list claims the house is empty unless the read that says so has landed
- [ ] There is no Reach card, no push toggle and no notification permission prompt
      anywhere. Web Push is phase 5, and an inert card would be the defect the phase-2
      placeholder tabs were

---

Device: ______________  iOS: ______  Build: ______________  Date: ____________
Tester: ______________
