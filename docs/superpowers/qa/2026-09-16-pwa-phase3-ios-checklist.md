# PWA phase 3 — manual iOS checklist

Spec §7's manual half, for the Memory, Triggers and System benches. The phase-1
checklist (`2026-09-07-pwa-phase1-ios-checklist.md`) still applies to the Room and the
Door, and `2026-09-10-pwa-phase2-ios-checklist.md` to the Workshop shell and the Activity
bench; this one is only what phase 3 added. Gate (spec §8): *full capability parity with
the old SPA*.

Run against the deployed build at `https://alfred.example.com`, signed in, with a house
that is running and writing events. Record the device, iOS version and date at the
bottom.

**Every step below can be answered by looking at the phone.** Anything that would need
Web Inspector, a network panel or a pixel ruler is settled by the unit tests instead and
is deliberately not here — a step whose pass cannot be observed is a step that cannot
fail, and that is worse than no step at all.

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

The phase-2 checklist's *Before you start* applies unchanged — the right build deployed,
and a house producing events on demand. It is not repeated here. What phase 3 needs on
top of it:

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
- [ ] First paint of **System**, in this order top to bottom: **Health** (the 2×2 grid, a
      ticking `live · HH:MM:SS` stamp beside the title, and the Cloud spend card at the
      bottom of the same card), **Quiet**, **Sessions**, **Connected services**,
      **Devices & identity**, **Reflex**, and **Maintenance last**
- [ ] Leave a bench and come back: Memory's sub-tab and search results, Triggers' kind
      chip and open row, and any queued note are all still there. The Activity list is
      back at the top with the row still expanded — that is known and filed
      (`docs/backlog/low/pwa-phase3-followups.md` §15), not a defect to raise again
- [ ] Type into a credential field on System, switch to Memory and back: the field is
      **empty**. A half-typed secret must not survive the trip

## Memory

- [ ] Episodic: rows read as one shape whatever store they came from. A **hot** row runs
      `HH:MM <day> · significance 0.40 · recalled 2× · hot`; a **cold** row is the step
      below. A row from last week says which day; none of them says `Invalid Date` or a
      1970 stamp
- [ ] **A `cold` row says nothing about recalls** — its meta line runs
      `HH:MM <day> · significance 0.40 · cold`, with no `never recalled` and no count,
      because the cold store has no such column and its search path invents one
      (`…/pwa-phase3-followups.md` §16). Any `recalled N×` beside the word `cold` is a
      regression, on a browse or on a search result
- [ ] **`decaying` appears only on `hot` rows**, drawn after the store and *after* the
      recall count, not instead of it:
      `HH:MM <day> · significance 0.34 · never recalled · hot · decaying`. A cold row never
      carries it, however lightly it is weighed. Worth knowing while you look: nothing is
      actually being swept today, because the Librarian's threshold is unreachable
      (`docs/backlog/high/librarian-decay-threshold-unreachable.md`), so a marked row will
      still be there tomorrow. That is the server's, not the bench's
- [ ] Tap the search field. **The layer must not zoom.** The page does not scale, the
      header stays put, and nothing is clipped off the top of the screen
- [ ] With the keyboard up at 360 px: the field and the `model:` pill are both fully on
      screen, the field is not under the keyboard, and the list above it still scrolls
- [ ] **Type a phrase and stop, without submitting.** Wait five seconds: the list
      underneath does not change — not one row, in either direction — and the pill still
      reads `model: unknown`. Typing is not a search
- [ ] Now press the keyboard's **search** key: the list becomes matches, each with
      `match 0.62` at the end of its meta line, and the pill flips to `model: ok` in green
- [ ] Search for gibberish: `Nothing close enough to "<your gibberish>".` above
      `searched by meaning · the server does not report what it rejected`. The list does
      not go blank behind an error, and no score or threshold is quoted
- [ ] Clear the field and submit: back to browse, newest first
- [ ] Semantic: cards unfold to their Markdown and fold again. Leaving the bench and
      returning folds them — that is deliberate
- [ ] Routines: the lifecycle rail shows `candidate · active · dormant · archived` with
      the routine's own stage marked; the sparkline's caption names the number of
      readings it actually drew, never a round "last 8"
- [ ] Scratchpad: two stat cards — the unscored queue, and the next consolidation with
      the last one under it
- [ ] Nothing on this bench offers to forget, redact, edit or promote anything

## Triggers

- [ ] **The kind chips at 360 px.** Five chips share the width. `Composite` is the long
      one: check that the word stays **inside its own outline** — it must not spill past
      the chip's border, touch the chip beside it, or disappear under a neighbour. Repeat
      at the largest Dynamic Type step, and in landscape. (The chips carry
      `whitespace-nowrap`, so they cannot wrap to a second line; overflow is what to look
      for, not wrapping.)
- [ ] Press just above and just below the visible pill of a chip: it still selects. The
      tap target is taller than the pill it draws
- [ ] `All` carries the house's own count. The other four carry no number
- [ ] Tap a chip: only that kind remains, and the chip reads as pressed. A filter that
      empties the list says `No <kind> triggers.`, never `No triggers yet.`
- [ ] A one-shot that has already fired **recedes but stays readable**: hold the phone at
      normal reading distance and read its name and meta without leaning in. (Whether it
      recedes by token or by opacity is not a question the eye can answer — that one is
      pinned by `src/test/contrast.ts` and the row's own tests, which is why it is not a
      step here.)
- [ ] Meta lines carry **three** clauses. A one-shot reads
      `one-shot · runs 08:40 tomorrow · created from conversation 20:52`; a recurring one
      prints its cron verbatim in the middle clause, `recurring · cron 0 19 * * 4 ·
      created from …`. Neither invents a next fire time, and neither is missing the
      `created from` clause
- [ ] Tap a row's body: it expands to the action and conditions, wrapped, never wider
      than the screen. **Expanding must not toggle the trigger** — the switch has not
      moved
- [ ] **Toggle a trigger.** The switch does *not* move. A note appears under the meta
      line in accent mono: `queued HH:MM · enabling · takes effect within 60 s` (or
      `disabling`). The row does not jump, re-sort or resize the list around it
- [ ] Tap the switch again inside that window: **nothing on screen changes.** The note
      keeps the stamp it already had — no new `queued HH:MM` — the switch has still not
      moved, and there is no flicker. With VoiceOver on, the control announces as dimmed
      but is still focusable and still reads its queued note
- [ ] Wait out the 60 s: the note goes and the switch is now in the state the server
      holds. (The other half of that rule — a switch springing back when the house
      *refuses* the change — needs the house to refuse, which a phone cannot arrange. It
      is unit-tested; the offline step below is the closest a tester gets.)
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

- [ ] Health: four cells — `bus`, `reflex`, `event rate`, `home assistant` — each a dot
      above a mono value. With the house up, the dot for a live cell is filled and the
      value is at full strength; the stamp beside the title ticks `live · HH:MM:SS`
- [ ] **Cloud spend**, at the foot of the same Health card: `Cloud spend today` with
      `$0.42 of $5.00` beside it, a bar under both, and a note reading
      `38 requests · $0.0036 each · …`. A house with no cap says `· no cap set` and draws
      an empty bar; a day with nothing spent says `no spend recorded today` and carries no
      note at all. The bar is never fuller than the numbers above it say
- [ ] **DND on from System.** Watch the switch as you lift your finger: it is still in
      its old position, and moves a moment later when the read comes back. It must never
      move, then move back. The sub-line becomes `on · …` and a note reads `applied`
- [ ] Go back to the Room (`‹ Room`): the Room's DND row agrees, with the same expiry.
      Turn it off from the Room, open the Workshop again: System agrees. The two must
      never disagree
- [ ] With DND on, the `Quiet until` chips appear and the one you chose is marked;
      with it off, the chip row is gone entirely
- [ ] Those four chips at 360 px: `until 22:00` is the long one, and they share the
      card's width four ways inside a section that is itself inset. Same question as the
      Triggers chips, one card narrower — the label must stay inside its own outline, at
      the default text size and at the largest Dynamic Type step
- [ ] Choose the **`1 h`** chip, then leave System and come back with DND still on: the
      `applied` note is **gone** and `1 h` is no longer marked, though the expiry itself is
      unchanged. An instant an hour out is not knowably `1 h` once this client's record of
      the tap has gone with the bench. Do the same with `until 22:00` and the mark *does*
      come back — that instant can be recomputed. Both are correct
- [ ] The calendar footnote is present and legible:
      `A meeting in your calendar can also quiet Alfred; that is not shown here.`
- [ ] `Held back` row shows the count and opens the sheet **over the Workshop**; `Done`
      returns to System exactly as it was. Then close the Workshop and open the same
      sheet from the Room's DND row: it opens over the Room. One sheet, two doors
- [ ] `Send them now`: the note stamps `queued HH:MM · the notifier sends them when it
      next reads the queue`. Open the sheet straight afterwards: it still offers
      `Drain queue now` rather than `Queued`. That is the known narrative gap
      (`…/pwa-phase3-followups.md` §14) — the two surfaces do not share the mark. The
      route is idempotent, so a second press delivers nothing twice; what to check here is
      only that the *words* disagree and nothing else does
- [ ] Sessions: your own row reads `current` and offers no End. Another session's row
      ends on tap, recedes to a dimmed `ended HH:MM · applied`, and is gone on the
      re-read. The ended device really is signed out
- [ ] Connected services: each row's state word and dot agree — filled dot for a service
      that is answering, outline for one that is not. Tap a row: the credential form
      opens, one field per schema entry, secrets masked, and a field the house already
      holds shows the placeholder **`saved · retype to save`** and never a value
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
- [ ] **`N registered` waits for the read.** Reach System for the first time with the
      network down (launch online, then Airplane mode on, then open the Workshop): there is
      **no count at all** beside `Add a passkey on another device` — not `0 registered`.
      Turn the network on and let the read land: `N registered` appears and matches the
      number of passkey rows under it. `0 registered` over a non-empty list is the failure
      this step exists to catch
- [ ] Reflex: each domain lists its **members** first — the filled chips, the entities
      Alfred acts on without asking — and then everything else it has **seen**, as
      outlined chips. The intro says exactly that:
      `Alfred acts on these without asking. Everything else it asks about first.`
      Tapping either kind moves it to the other. A chip mid-change shows its own busy
      state, and a failure is attached to that domain rather than to the section

## Under a 60 s socket outage

Airplane mode on, then wait a full minute on each bench before turning it off.

Two things to know before reading the passes below, because they decide what is and is
not a defect. Neither Memory nor Triggers polls — a bench sitting still issues no read,
so there is nothing for the network to refuse. And react-query **pauses** a read with no
network rather than failing it: the data it already had is kept and no error is raised.
So on those two benches the correct behaviour under an outage is that **nothing changes
at all**, and only the Workshop header says the house went away.

Three steps below need a bench whose **first ever read of this session happens offline**.
Do not force-quit into Airplane mode to get there — the sign-in check pauses too and the
app never leaves its opening field. The manoeuvre is: **launch online, let the Room paint,
then turn Airplane mode on and open the Workshop.** A bench only reads when it is first
shown, so everything you have not visited yet is still unread.

- [ ] **Activity** is phase 2's step (`…phase2-ios-checklist.md`, *The status line*) and
      is not repeated. What phase 3 changed is where the announcement comes from — see
      VoiceOver below
- [ ] **Memory**: every row already on screen stays, unchanged, for the full minute. No
      error line appears, nothing empties, and no sentence about the house appears that
      was not there before
- [ ] **Memory, a search attempted offline**: type a phrase and submit. The browse list
      underneath is **exactly as it was** — not empty, not replaced by
      `Nothing close enough to "…"`, not behind an error. Turn the network on: the search
      then answers normally
- [ ] **Memory, read for the first time offline** (the manoeuvre above): it reads
      `Episodic memory has not been read yet.` — **never** `No episodic memories yet.` A
      bench that has read nothing knows nothing about the house
- [ ] **Triggers**: rows already on screen stay for the full minute, with no error line
      and no row silently flipping state
- [ ] **Triggers, read for the first time offline**: same manoeuvre. Today this reads
      `No triggers yet.` over a house that has triggers — the one place on the three
      benches where an empty sentence outruns its evidence. It is filed
      (`…/pwa-phase3-followups.md` §22); record what you see and move on rather than
      raising it again
- [ ] **System**: the Health grid's values fall back to `?`/`—`, the whole grid recedes
      to a dimmed colour, **every dot goes out** — including one that was green a moment
      ago — and the stamp turns to `unknown since HH:MM` in accent. A green light over a
      dimmed number is the failure this step exists to catch. (System is the one bench
      that can tell: its grid goes stale on the *age* of the last overview read, not on an
      error)
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
- [ ] **Pause the feed**: you hear the state word change **once**, to `paused` — that is
      correct, it is news. Now wait another minute with the held count climbing: you hear
      **nothing further**, because the count is a sibling of the region and not inside it
- [ ] With a read failing, each bench announces it **once**. The region is named
      `Read errors` to a screen reader, but what is spoken — and what a sighted reader
      sees above the list — is the failure's own sentence, not the words "Read errors".
      It must be distinguishable from the connection announcement above it
- [ ] Walk a trigger row with VoiceOver. The order is **kind, then name, then meta** —
      `one-shot`, the trigger's name, then the meta line — and then the switch, announcing
      its **stored** state and its queued note. Nothing announces a state the server has
      not confirmed
- [ ] The Workshop's tab set still behaves: four tabs, one stop, ←/→ move and wrap.
      (Phase 2 checks the same thing with an external keyboard; this is the VoiceOver
      half)

## Nothing lies

- [ ] No bench says a word of system state outside spec §10's vocabulary: `queued`,
      `applied`, `last true HH:MM`, `unknown since HH:MM`, `hot / cold`,
      `candidate · active · dormant · archived`, `expired · not done`,
      `takes effect within 60 s` — plus the status line's `live` / `paused`, and
      `decaying`, which the handoff coined and `docs/web-frontend.md` defines. Plain
      English about the screen itself is fine and expected
- [ ] Nothing green is showing next to anything the app is no longer reading
- [ ] No control claims an outcome the server only queued
- [ ] No empty list claims the house is empty unless the read that says so has landed —
      with the one filed exception recorded under the outage section above
- [ ] There is no Reach card, no push toggle and no notification permission prompt
      anywhere. Web Push is phase 5, and an inert card would be the defect the phase-2
      placeholder tabs were

---

Device: ______________  iOS: ______  Build: ______________  Date: ____________
Tester: ______________
