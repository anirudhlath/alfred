# PWA Phase 6: Desktop Composition and Keyboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the same client out for a laptop: the Room, the Workshop and the detail side by side from 1100 px, the detail as a sheet over the bench from 760 px, the phone below that, the Door over everything, and the handoff's keyboard shortcuts.

**Architecture:** One `Layout` value comes from `matchMedia` in `Room` and travels by context, defaulting to `"phone"`, so stories, tests and everything outside the Room keep the phone with no stubbing. `shell/Desk.tsx` is one DOM tree at every width (the Room never remounts) whose CSS turns into the handoff's grid; the Workshop renders in place from 760 px, and `Sheet` portals into a slot `Desk` owns. Nothing is modal at width except the Door and the gates, which stay `Layer`s on `<body>` and make `#root` inert. That one attribute is what silences every shortcut under them.

**Tech Stack:** React 19, Tailwind v4, Vitest + Testing Library, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md`, phase 6 row. **Binding design:** `docs/design/2026-09-04-pwa-client-handoff/README.md` §11 and §5, and `Alfred Desktop.dc.html` (read both before any task that draws something; values are cited here and not restated where the handoff states them).

## Global Constraints

- Below 760 px, every pixel and every behaviour stays as it is. Existing tests pass unmodified, apart from `intro` moving into `Sheet` (Task 3).
- Handoff copy is verbatim. The one invented string is in the Rulings table.
- Colours come from tokens in `web/src/index.css`, never a literal in a component.
- The repo is public: placeholders only, no real hosts, addresses or names.
- Comments follow the surrounding code: they say why, and cite `index.css` or the handoff section for a value.
- Tests: `cd web && npx vitest run <file>`. Before each commit: `cd web && npm run lint && npm run test && npm run build`.
- Commits are conventional (`feat(web): …`). No model identifiers anywhere.

## Rulings

The handoff draws one composition, the Workshop open with a thread beside it. These settle what it leaves open.

| # | Ruling | Why |
|---|---|---|
| R1 | From 760 px the Workshop is always in the middle column: no handle, no `‹ Room`, and `open` is ignored | It is the only composition drawn, and a closed Workshop would be an undrawn empty column |
| R2 | The third column exists only while a detail is open; closed, the grid is `420px 1px minmax(0,1fr)` | Architecture doc: "its detail views open as a third column" |
| R3 | The handoff's grid is literal, except the bench's minimum is `min(380px, calc(100vw - 782px))` | Its minimums sum to 1162 px and its breakpoint is 1100 px. At 1100 to 1161 the bench gives up the difference, with no sideways scroll. Below 1100 nothing competes, so `minmax(0,1fr)` |
| R4 | Both breakpoints also require `min-height: 500px` | A phone on its side is 852 px wide and under 450 px tall; it stays the phone |
| R5 | `useLayout()` reads a context defaulting to `"phone"`; only `Room` provides it | Stories in a wide frame and every existing test stay phone. The Door, sheets and benches are all inside the Room |
| R6 | One detail at a time (`useDetail`): opening one closes the other | Nothing is modal at width, so `why?` can be pressed with Held back open, and both would land in one column |
| R7 | `esc closes` is a real button | An iPad in landscape reaches 1100 px with no Escape key |
| R8 | The column keeps the title `Why Alfred did that` | The mock's `Why Alfred spoke about the door` is sample data; nothing generates such a sentence |
| R9 | `Causal thread` is the thread's eyebrow only; Held back has none | No other eyebrow is drawn, so none is invented |
| R10 | In the column, focus stays where it was on open, and returns to the opener on close only if it was inside the column. Over the bench, focus moves in and always returns | The column is beside the reader's place, so J/K must keep working. The sheet covers the bench it came from |
| R11 | J/K move real focus onto the row's own button, so Enter is native and needs no code. They skip dimmed rows and do not wrap | A separate cursor would duplicate focus and hide from assistive tech. A soloed stream is the one being read |
| R12 | Space pauses when focus is on a row or on nothing; on any other control it stays that control's | A row's Space would toggle it, which Enter does. A chip's Space is its own (WCAG 2.1.1) |
| R13 | The bench's mock annotation ships split: the keyboard sentence always, `Soloed {mono}: …` only while soloed. The third column's design note does not ship | It is the only way to discover the shortcuts. The other note links to prototype files |
| R14 | `⌘K` is shown verbatim on every OS, and Ctrl+K works too. Escape in the composer blurs it | Verbatim copy. After `⌘K` the field keeps focus, and J would type a j with no way back but Tab |
| R15 | The Door's and gates' contents sit in a centred `.phone-column` (max 480 px) at every width | "Nothing is redesigned": a slide 1200 px long is unusable. No phone is wider than 480, so phones do not change |
| R16 | D9 stands: the prompt row, self-opening gate and Act rows stay iPhone only | The handoff draws no desktop wording, and their copy names Safari, iOS and this iPhone. Documented in Task 7 |
| R17 | Light `--detail` is the midpoint of light `--bg` and `--surface`, `#F3EFE8` | Dark `#2A2723` is that midpoint in dark; "dark theme shown" leaves light open |
| R18 | At width a failed bench says `This bench stopped working.` / `Nothing on the Room is affected.` with a button `Start it over`, which remounts it. **Invented string** | "Opening the Workshop again" has no meaning when it never closes |
| R19 | At width the chips keep their hue while another is soloed | The mock draws them so; the phone's fading exists for the 44 px stacked chip |
| R20 | The chip row wraps rather than overflows: a chip's count drops under its monogram when narrow, and `All`/`Pause` drop to a second line under 352 px | Real counts run to 400, and the bench can be 270 px of content at 1100 px. The mock has single digits |
| R21 | `esc closes` uses `t-meta-strong`, not the mock's muted | It is a control, and `--muted` is 3.46:1 in light (index.css, `.t-meta-strong`) |
| R22 | `orientation: portrait` in the manifests stays | Desktop ignores it, and removing it changes phones. Android tablets stay portrait for now |
| R23 | The Act row whose thread is open keeps its `why?` button as the same DOM node, `opacity-0` unless `focus-visible`, instead of removing it | The mock draws none, and that is what a pointer sees. Removing the node would throw a keyboard reader's focus to `<body>` the moment they pressed it |

## Review Focus

1. **A Door or gate rises over the open desk:** no shortcut acts through it. Task 3 (Escape), Task 5 (J/K, Space, digits, `⌘K`).
2. **Typing:** a j, k, space or digit in the composer or any bench field is just text. Task 5.
3. **Crossing a breakpoint mid-sentence:** the Room's DOM node survives, so the draft, the scroll position and the socket are untouched. Task 2.
4. **A bench that throws at width:** the reader can start it over, since there is no "open again". Task 2.
5. **Real numbers in a narrow bench:** four-digit counts in eight chips at 1100 px with a thread open do not overflow. jsdom has no layout, so this is the browser check in Task 8.

---

### Task 1: The layout value

**Files:**
- Create: `web/src/shell/layout.ts`
- Test: `web/src/shell/layout.test.tsx`

**Interfaces:**
- Produces:
  - `type Layout = "phone" | "sheet-over-bench" | "columns"`
  - `readLayout(): Layout`
  - `useViewportLayout(): Layout`, `useSyncExternalStore` over both queries, the way `useReducedMotion` is in `shell/presence.ts`
  - `LayoutContext = createContext<Layout>("phone")`
  - `useLayout(): Layout`
  - Queries: `(min-width: 760px) and (min-height: 500px)` and `(min-width: 1100px) and (min-height: 500px)`

- [ ] **Step 1: Write the failing tests.** Stub `window.matchMedia` with a helper that evaluates those two query strings against a `(width, height)` and can fire `change`:
  - `readLayout` gives `phone` at 393×852, 759×900 and 852×393; `sheet-over-bench` at 760×900 and 1099×900; `columns` at 1100×900 and 1920×1080
  - `readLayout` gives `phone` when `matchMedia` is not a function
  - `useViewportLayout` re-renders with the new value when the stub resizes across 760 and across 1100
  - `useLayout` gives `phone` with no provider, and the provided value inside `LayoutContext.Provider`
- [ ] **Step 2:** `npx vitest run src/shell/layout.test.tsx`. Expected: FAIL, module not found.
- [ ] **Step 3:** Implement. The default of `phone` in the context carries R5; say so in its comment.
- [ ] **Step 4:** Run it. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(web): add the layout value for the desktop composition`.

### Task 2: The desk: Room and Workshop side by side

**Files:**
- Create: `web/src/shell/Desk.tsx`, test `web/src/shell/Desk.test.tsx`
- Modify: `web/src/index.css` (`--detail` in both themes, `.desk*` rules), `web/src/test/contrast.test.ts`, `web/src/room/Room.tsx`, `web/src/room/Timeline.tsx`, `web/src/room/Composer.tsx`, `web/src/workshop/Workshop.tsx`
- Test: `web/src/workshop/Workshop.test.tsx`

**Interfaces:**
- Consumes: `useLayout`, `useViewportLayout`, `LayoutContext` (Task 1)
- Produces:
  - `DeskSlotsContext = createContext<{ overBench: HTMLElement | null; detail: HTMLElement | null }>({ overBench: null, detail: null })`, `useDeskSlots()`
  - `Desk({ room, bench, detailOpen }: { room: ReactNode; bench: ReactNode; detailOpen: boolean })`
  - Token `--detail` (dark `#2A2723`, light `#F3EFE8`)

`Desk` always renders the same tree, whatever the layout:

```
.desk[data-layout][data-detail only when columns and detailOpen]
  {room}
  .desk-line (aria-hidden)
  .desk-bench  ← state-held node, overBench slot; position relative, overflow hidden
    .desk-bench-body[inert when sheet-over-bench and detailOpen]  {bench}
  .desk-line (aria-hidden)
  .desk-detail ← state-held node, detail slot; background var(--detail)
```

CSS, in `@layer components`:
- `.desk` is `display: flex; flex-direction: column; flex: 1; min-height: 0`.
- `.desk-line`, `.desk-bench` and `.desk-detail` are `display: none` by default.
- `[data-layout="sheet-over-bench"]` and `[data-layout="columns"]` get `display: grid; grid-template-rows: minmax(0, 1fr); grid-template-columns: 420px 1px minmax(0, 1fr)` and show the first line and the bench.
- `[data-layout="columns"][data-detail]` gets `grid-template-columns: 420px 1px minmax(min(380px, calc(100vw - 782px)), 1fr) 1px minmax(360px, 520px)` (R3) and shows the second line and the detail.
- Lines are `background: var(--line)`.
- `.desk-bench` is `position: relative; min-width: 0; overflow: hidden`. `.desk-bench-body` and `.desk-detail` are flex columns with `min-height: 0; overflow: hidden`, and the body is `height: 100%`: the Workshop panel is a fragment that relies on a flex column parent, as it has in `Layer`.

- [ ] **Step 1: Write the failing tests.**

`Desk.test.tsx`:
- `keeps the Room's DOM node across every layout`: render with `LayoutContext` `phone`, rerender `sheet-over-bench`, then `columns`; the `room` element is the same node each time
- `sets data-detail only in columns with a detail open`
- `makes the bench body inert only over the bench`: `sheet-over-bench` + `detailOpen` has `inert`; `columns` + `detailOpen` does not; `overBench` slot itself never does
- `hands out both slots`: a consumer of `useDeskSlots` gets two connected `HTMLElement`s after mount

`Workshop.test.tsx`, wrapped in `LayoutContext.Provider value="columns"`:
- `renders in place with no dialog, whatever open says`: `queryByRole("dialog")` is null with `open={false}`, the Activity tab is present
- `labels the column`: `Workshop` text is shown and no button named `Room`
- `does not close on Escape`: `onClose` not called
- `a bench that throws can be started over`: make one bench throw once (`vi.mock` of `./ActivityBench`; no existing test covers the boundary), expect `This bench stopped working.`, `Nothing on the Room is affected.`, press `Start it over`, expect the switcher back

`contrast.test.ts`: in both themes `fg`, `fg2` and `accent-text` on `detail` are ≥ 4.5.

- [ ] **Step 2:** Run the three files. Expected: FAIL (missing module, missing token).
- [ ] **Step 3: Implement `Desk` and the CSS.** Slots are `useState<HTMLElement | null>` ref callbacks.
- [ ] **Step 4: Implement the Workshop.** `useLayout()` in `Workshop`: `phone` is today's `Layer`; anything else is the panel directly. `ErrorBoundary` takes `key={attempt}`, a `useState` counter; `BenchFailed` gets `onRestart` and its R18 copy at width. In `WorkshopPanel`'s header at width (handoff §11): padding `40 24 0`, gap 12, `t-label` `Workshop` left instead of the `‹ Room` button, the same status spans right, the switcher in a `max-w-[520px]` wrapper. The Escape effect already no-ops with no dialog, so leave it.
- [ ] **Step 5: Implement the Room.** `useViewportLayout()` wraps everything in `LayoutContext.Provider`. `<main>` is `Desk`'s `room`; `<Workshop>` moves out of it to `bench`. `detailOpen` is `sheetOpen || why !== null`. Header `px-7 pt-10` at width. `handle` only on the phone. `Timeline`'s `px-6` becomes `px-7` at width. Composer's row `pb-2` becomes `pb-6` at width.
- [ ] **Step 6:** Run the three files, then the gate. Expected: PASS, with no existing test edited.
- [ ] **Step 7:** Commit: `feat(web): lay the Room and the Workshop side by side from 760 px`.

### Task 3: The detail: a column, or a sheet over the bench

**Files:**
- Create: `web/src/shell/Sheet.test.tsx`, `web/src/room/useDetail.ts`, `web/src/room/useDetail.test.tsx`
- Modify: `web/src/shell/Sheet.tsx`, `web/src/shell/presence.ts`, `web/src/sheets/WhySheet.tsx`, `web/src/sheets/HeldBackSheet.tsx`, `web/src/room/Room.tsx`, `web/src/room/Timeline.tsx`, `web/src/room/rows/ActRow.tsx`
- Test: `web/src/sheets/WhySheet.test.tsx`, `web/src/room/Timeline.test.tsx`

**Interfaces:**
- Consumes: `useLayout`, `LayoutContext`, `useDeskSlots`, `DeskSlotsContext`, `--detail`
- Produces:
  - `SheetProps` gains `eyebrow?: string` and `intro?: string`. `intro` moves out of both sheets' children and renders first in the body on the phone, with the classes it has today.
  - `useAsideFocus(active: boolean, panel: RefObject<HTMLElement | null>, take: boolean): void` in `presence.ts` (R10). On cleanup, restore to the captured element when `take`, or when `document.activeElement` is `<body>`, and only if it is connected and outside `[inert]`.
  - `useDetail(): { why: StreamRef | null; heldOpen: boolean; openWhy(ref: StreamRef): void; openHeld(): void; closeWhy(): void; closeHeld(): void }`, all stable through `useCallback` (the memoised Workshop depends on it)
  - `ActRow` gains `threadOpen?: boolean`; `Timeline` gains `openThread?: string | null`, a `rowKey`

`Sheet` picks its mode from `useLayout()` and `useDeskSlots()`: `columns` with a `detail` slot is the column; `sheet-over-bench` with an `overBench` slot is the sheet over the bench; everything else, including no slot, is today's modal. Split into small components so each mode's hooks are its own.

- **Over the bench:** today's panel, shared rather than copied, with the outer box `absolute inset-0 z-20` in place of `viewport-fill`, portalled to `overBench`, `role="dialog"` without `aria-modal`, `useAsideFocus(mounted, panel, true)`.
- **Column:** an `<aside aria-labelledby>` portalled to `detail`, drawn from `Alfred Desktop.dc.html:93-98`: header padding `40 24 14`, gap 6, 1 px `--line` bottom; eyebrow `t-label` left, `esc closes` button right (R7, R21, a 44 px target by an `after` box like `EventRow`'s `PILL`); title `t-title`; intro 13.5/1.5 `--fg2`. Body padding `18 24 40`, `overflow-y-auto`, the `gap-3` column the sheet has today. No rise (R2 is a grid change, and `null` the moment `open` is false). `useAsideFocus(open, panel, false)`.
- **Both non-modal modes:** a `document` keydown closes on Escape unless `panel.closest("[inert]")`.

`WhySheet` passes `eyebrow="Causal thread"` and, at `columns` only, the mock's intro `Solid links are joined by an id the server holds. Dashed means adjacent in time only.`; otherwise today's `INTRO`. `HeldBackSheet` passes its `INTRO` as `intro`.

`ActRow` with `threadOpen`, from `Alfred Desktop.dc.html:32`: background `--surface`, `margin 0 -12`, horizontal padding 12, radius 10; text `--fg`; meta gains ` · thread open →`; `why?` per R23.

- [ ] **Step 1: Write the failing tests.**

`Sheet.test.tsx` (real slot divs in `document.body`, under both contexts):
- `phone: a modal dialog on body`: `aria-modal="true"`, parent is `body`, `#root` inert
- `columns: an aside in the detail slot`: inside the slot, no `dialog` role, `#root` not inert, shows eyebrow, `esc closes`, title, intro
- `columns: gone the moment open is false`
- `columns: Escape anywhere closes, and so does esc closes`
- `columns: ignores Escape while #root is inert`
- `columns: leaves focus where it was on open`
- `columns: returns focus to the opener when closed from inside`
- `sheet-over-bench: a dialog in the overBench slot, not modal`: no `aria-modal`, Done and the scrim's `Close` dismiss it
- `sheet-over-bench: takes focus and gives it back`
- `no slot: falls back to the modal`

`useDetail.test.tsx`: `opening the thread closes Held back`; `opening Held back closes the thread`; `re-pointing the thread keeps it open`; the callbacks keep their identity across renders.

`WhySheet.test.tsx`: `columns` shows `Causal thread` and the mock's intro; the phone shows today's `INTRO` and no eyebrow.

`Timeline.test.tsx`: only the act row whose `why` matches `openThread` reads `· thread open →`; `its why? stays the same node, so focus stays`: focus it, rerender with `openThread` set, `document.activeElement` is that node and it has `opacity-0`.

- [ ] **Step 2:** Run the four files. Expected: FAIL.
- [ ] **Step 3:** Implement `useAsideFocus`, then `Sheet`, then the two sheets.
- [ ] **Step 4:** Implement `useDetail` and use it in `Room`, whose existing comment on `why` moves with the state. Pass `openThread={layout !== "phone" && why ? rowKey(why) : null}`.
- [ ] **Step 5:** Run the four files, then the gate. Expected: PASS. Only `intro`'s move may touch existing tests, and it should not need to.
- [ ] **Step 6:** Commit: `feat(web): open the detail as a third column, or a sheet over the bench`.

### Task 4: The Activity bench at width

**Files:**
- Modify: `web/src/workshop/useActivity.ts`, `web/src/workshop/StreamChips.tsx`, `web/src/workshop/EventRow.tsx`, `web/src/workshop/ActivityBench.tsx`, `web/src/workshop/Workshop.tsx`
- Test: `web/src/workshop/useActivity.test.tsx`, `web/src/workshop/StreamChips.test.tsx`, `web/src/workshop/EventRow.test.tsx`, `web/src/workshop/ActivityBench.test.tsx`

**Interfaces:**
- Consumes: `useLayout`
- Produces:
  - `useActivity(enabled: boolean, dimSolo?: boolean)`: `targets` is `STREAMS` whenever `dimSolo`, so rows and `cursor` ignore solo. `solo` still reports the choice.
  - `StreamChipsProps` gains `compact?: boolean` and `trailing?: ReactNode`
  - `EventRowProps` gains `desk?: boolean` and `dimmed?: boolean`. A prop, not `useLayout()`: one subscription per row at 400 rows a stream is the cost `Workshop.tsx` warns about. The row button gets `data-event-row`, plus `data-dimmed` when dimmed (Task 5 selects on these).

**Compact chips** (`Alfred Desktop.dc.html:53-64`): 36 px (`h-9`), monogram and count inline with `gap-1`, count opacity .7 (.8 when on), `flex-wrap` inside the chip with `min-w-0 overflow-hidden` so a narrow one stacks (R20), no fading (R19). The outer row is `flex flex-wrap gap-1.5`; the eight sit in a `flex` group with `flex-[1_1_240px] min-w-0 gap-1.5`, then `trailing`, `flex shrink-0 gap-1.5` (240 + gap + the 106 of `All` and `Pause` is the 352 of R20). `aria-label`, `aria-pressed` and toggling are unchanged.

**The bench at width:** chips first, the padding `12 24 0` on a wrapper and `max-w-[520px]` on the row inside it (the mock's 520 is content width), trailing the two buttons. The footer goes. The list is `px-6 pt-3 max-w-[568px]`. The banner and error line take `mx-6`.
- `All` is `h-9 rounded-lg border border-line px-3 text-[12px] font-medium`, disabled and .4 with no solo, as the phone's is.
- `Pause` is `h-9 rounded-lg px-3 text-[12px] font-medium whitespace-nowrap`, filled like the phone's; its paused label is the phone's `Resume · N new` / `Resume`.
- Under the list, padding `10 24 24`, `t-meta`, line-height 1.5, verbatim (R13): when soloed `Soloed {mono}: other streams dimmed rather than removed, so the neighbourhood stays visible.`, always `Keyboard: J/K move, Enter opens payload, Space pauses, 1–8 solo a stream.`

**Solo at width (README line 183):** every row stays; others get `dimmed` (`opacity-35`, full on `focus-within` and while `expanded`). The solo scroll-to-top effect skips width, since it is the same list. `emptyNote` and the empty heading treat `solo` as null at width: an empty list there means all eight are empty.

**`desk` row open** (`Alfred Desktop.dc.html:70-80`): `<li>` background `--surface`, `margin 0 -12`, padding `0 12`, radius 10; `pre` background `--bg`.

- [ ] **Step 1: Write the failing tests.**
  - `useActivity`: `dimSolo keeps every stream's rows when one is soloed`; `dimSolo keeps the shared cursor`; the plain call still filters (already covered; leave it).
  - `StreamChips`: `compact is 36 px and keeps every hue while one is soloed` (style `borderColor` equals `ringText(hue)`); `compact renders trailing`; `aria-label` unchanged.
  - `EventRow`: `dimmed marks the button and the row`; `an open desk row sits on surface with its payload on bg`.
  - `ActivityBench` under `LayoutContext` `columns`: `chips come before the list`; `offers All and Pause, not All streams and Pause feed`; `solo dims the rest and removes nothing`; `solo leaves the scroll position`; `says the keyboard sentence always and the solo sentence only while soloed`; `paused, the button reads Resume · 3 new`. Under `phone`, these strings and the footer stay as today (existing tests).
- [ ] **Step 2:** Run the four files. Expected: FAIL.
- [ ] **Step 3:** Implement. `Workshop` passes `layout !== "phone"` to `useActivity`.
- [ ] **Step 4:** Run the four files, then the gate. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(web): draw the Activity bench for the desktop, solo by dimming`.

### Task 5: The keyboard

**Files:**
- Create: `web/src/shell/keys.ts`, `web/src/workshop/useBenchKeys.ts`, `web/src/workshop/useBenchKeys.test.tsx`
- Modify: `web/src/workshop/ActivityBench.tsx`, `web/src/room/Composer.tsx`, `web/src/index.css`
- Test: `web/src/room/Composer.test.tsx`

**Interfaces:**
- Consumes: `Activity`, `data-event-row`, `data-dimmed` (Task 4), `useLayout`, `STREAMS`
- Produces:
  - `isEditing(target: EventTarget | null): boolean` (`input`, `textarea`, `select`, `[contenteditable]`)
  - `useBenchKeys(activity: Activity, list: RefObject<HTMLElement | null>, enabled: boolean): void`

`useBenchKeys` listens on `document` for `keydown` only while `enabled`. It ignores the event when any of these holds: the list is inside `[inert]`; `isComposing`; ctrl, meta or alt held; `isEditing(event.target)`.
- `j` and `k` (case-insensitive): `querySelectorAll("[data-event-row]:not([data-dimmed])")`, focus the next or previous from `document.activeElement`; from anywhere else, the first; no wrap. `preventDefault`.
- Space: if `event.target` is inside a `button, a, [role="tab"], [role="switch"]` that is not a `[data-event-row]`, leave it alone. Otherwise `preventDefault`, toggle pause unless `repeat`, and `preventDefault` the matching `keyup` too (Firefox clicks a focused button on keyup regardless).
- `1` to `8`: unless `repeat`, `setSolo(solo === STREAMS[n - 1] ? null : STREAMS[n - 1])`.

`ActivityBench` calls it with `layout !== "phone"`; being unmounted on the other three benches is what scopes the keys to Activity.

`Composer` at width: a `document` keydown for meta-or-ctrl K focuses the field unless the field is inside `[inert]`, with `preventDefault`. `onKeyDown` blurs on Escape, after the existing `isComposing` guard. The field sets `aria-keyshortcuts="Meta+K Control+K"`. The hint `⌘K` (`Alfred Desktop.dc.html:38`) sits inside the field's right edge, `aria-hidden`, `pointer-events-none`, `t-meta`, only with no draft; wrap the input in a `relative min-w-0 flex-1` box and give the field room with right padding at width.

CSS: `[data-event-row]:focus-visible` gets a 2 px `--accent-text` outline at offset -2 px. The app has no focus rule, and the UA ring vanishes on `--surface`.

- [ ] **Step 1: Write the failing tests.**

`useBenchKeys.test.tsx`, a fixture with a list of row buttons and a fake `activity`:
- `J focuses the first row, then the next; K goes back; neither wraps`
- `J skips dimmed rows`
- `Enter on a focused row presses it` (`user.keyboard("{Enter}")`, its `onClick` fires, no handler of ours)
- `Space pauses from a row and from nothing, without pressing the row`
- `Space resumes when paused`
- `Space on another button is that button's` (no pause, its `onClick` fires)
- `holding Space toggles once`
- `1 solos the first stream, 1 again clears, 9 does nothing`
- `is silent in a field`: focus an `input`, type `j 1`, the value is `j 1`, no focus move, no pause, no solo
- `is silent with ctrl, meta or alt`
- `is silent under an inert ancestor` (set `inert` on a wrapper)
- `is silent when not enabled`

`Composer.test.tsx`, under `LayoutContext` `columns`: `⌘K and Ctrl+K focus the field`; `neither acts inside an inert root`; `shows ⌘K with no draft, hides it with one`; `Escape blurs the field`; under `phone`: `no hint, ⌘K does nothing, Escape keeps focus`.

- [ ] **Step 2:** Run both. Expected: FAIL.
- [ ] **Step 3:** Implement `keys.ts`, the hook, then `Composer` and the CSS.
- [ ] **Step 4:** Run both, then the gate. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(web): add the desktop keyboard shortcuts`.

### Task 6: The Door and the gates on a wide screen

**Files:**
- Modify: `web/src/index.css`, `web/src/door/DoorLayer.tsx`, `web/src/gates/Gate.tsx`
- Test: `web/src/door/DoorLayer.test.tsx`, `web/src/gates/Gate.test.tsx`

**Interfaces:**
- Produces: `.phone-column` (`width: 100%; max-width: 480px; margin-inline: auto`), and a comment saying why: R15, no phone reaches it.

The Door's and `Gate`'s backgrounds stay full-bleed. What goes inside the column: the Door's three content blocks (`DoorLayer.tsx:125` on), and `Gate`'s scrolling copy and footer (`Gate.tsx:147`, `:170`). Keep each block's existing flex behaviour: the column must be `flex flex-1 flex-col` with `overflow-hidden` so nothing scrolls differently on a phone. `PresenceField` in `Gate`, if drawn, stays full-width.

- [ ] **Step 1: Write the failing tests:** `the Door's question sits inside .phone-column`; `a gate's action sits inside .phone-column` (`closest(".phone-column")` is not null).
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run them, then the gate. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(web): keep the Door and the gates to a phone's column on a wide screen`.

### Task 7: Docs, PRD, coverage and the design system

**Files:**
- Modify: `docs/web-frontend.md`, `docs/PRD.md`, `evals/coverage.yaml`, `docs/design-system.md`, `CLAUDE.md`, `web/design-system/entry.tsx`, `web/design-system/gen-docs.mjs`, `web/design-system/previews/StreamChips.tsx`, `web/design-system/previews/EventRow.tsx`
- Create: `web/design-system/ds/DeskScreen.tsx`, `web/design-system/previews/DeskScreen.tsx`

**Interfaces:**
- Consumes: `Desk`, `LayoutContext`, `RoomScreen`, `BenchSwitcher`, `StreamChips`, `EventRow`, `Sheet`, fixtures

**Docs:**
- `docs/web-frontend.md`: a new section "Desktop", holding the three layouts with a mermaid diagram of `Layout` → `Desk` → slots, the keys with their guards, and this plan's rulings that change behaviour. Fix line 32 and the "Desktop" bullet at lines 2087–2089 so the wording question is answered by R16. Fix line 1927 likewise.
- `docs/PRD.md`: new row after the Web app rows: `Web app — laid out for a laptop: the Room, the Workshop and the detail side by side from 1100 px, the detail as a sheet over the bench from 760 px, solo by dimming, and the keyboard shortcuts`, `Shipped`, `docs/web-frontend.md`. Date stays `2026-11-06` unless it is later on the day of the PR.
- `evals/coverage.yaml`: `{id: 4.1.pwa-desktop, section: "4.1", prd: "Web app — laid out for a laptop", not_llm: "desktop layout and keyboard UI", tests: [web/src/shell/Desk.test.tsx, web/src/shell/Sheet.test.tsx, web/src/workshop/useBenchKeys.test.tsx]}`, in the shape of its neighbours. Its `prd:` must be a prefix of the new row. The test that reads it is `tests/evals/test_prd_coverage.py`: `uv run --python 3.13 python -m pytest tests/evals/test_prd_coverage.py -q`.
- `CLAUDE.md`: the `web/` line under Key Paths gains that the same client lays out for a laptop (`src/shell/Desk.tsx`).

**Design system:**
- `DeskScreen` is `Desk` under `LayoutContext` `columns` with `RoomScreen` as the Room, `BenchSwitcher` + compact `StreamChips` + `EventRow`s as the bench (one soloed, one open) and `Sheet` holding a thread in the column, in a frame 800 px tall.
- It goes in `foundation`, with its own minimum width of 1280 in `gen-docs.mjs` alongside `PHONE`, and a line under "The two canvas-only pieces" in `docs/design-system.md` (which becomes three).
- Stories: `StreamChips` gains `Compact`; `EventRow` gains `Dimmed` and `OpenOnDesk`; `ActRow` (`web/design-system/previews/ActRow.tsx`) gains `ThreadOpen`.
- `RoomScreen` does not change: the phone's layout did not. **Nothing is uploaded.** The owner has the upload on hold.

- [ ] **Step 1:** Write the docs.
- [ ] **Step 2:** Add `DeskScreen` and the stories.
- [ ] **Step 3:** `cd web && npm run design-sync`. Expected: every stage passes, render-check included.
- [ ] **Step 4:** Run the gate and the coverage test. Expected: PASS.
- [ ] **Step 5:** Commit: `docs(web): document the desktop composition and add it to the design system`.

### Task 8: In a real browser (controller)

Not dispatched. Run the built client in Chromium with the API stubbed by `page.route`, and read each screenshot once.

- [ ] 1280×800: three columns against `Alfred Desktop.dc.html`, thread open and closed; Door over all three, shortcuts dead under it.
- [ ] 1100×800: thread open, counts of 400 and 1200 in the chips, no sideways scroll and no clipped chip text (Review Focus 5).
- [ ] 900×800 and 760×800: sheet over the bench only; the Room stays live beside it.
- [ ] 393×852 and 852×393: the phone, identical to master's screenshot at the same size.
- [ ] J, K, Enter, Space, 1–8, `⌘K`, Escape once each at 1280 in Chromium, and the same click-through in light theme.
