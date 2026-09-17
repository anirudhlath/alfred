# Handoff: Alfred — Room, Door, Workshop (phone + desktop)

## Overview
Alfred is a self-hosted household assistant (repo `anirudhlath/alfred`, branch `master`). This handoff covers the complete user-facing client: a conversational surface ("the Room"), an approval interrupt for critical actions ("the Door"), and a four-bench diagnostics layer ("the Workshop": Activity, Memory, Triggers, System), plus identity gates (first-run passkey setup, sign-in, 401/403) and the iOS notification-permission path for a PWA. A desktop layout lays the phone's layers side by side. Everything is grounded in the backend as it stands: the 8 catalogued Redis streams, admin endpoints that return `queued` and cannot report outcome, the 300 s pending-action TTL, the 60 s trigger-cache window, routine lifecycle `candidate → active → dormant → archived`, hot/cold episodic stores, and moods `neutral · pleased · concerned · amused · serious`.

## About the design files
The `.dc.html` files in this bundle are **design references built in HTML**. They are working prototypes that show intended look, copy and behaviour; they are not production code and must not be shipped. The task is to **recreate these designs in the target codebase's existing web client environment** using its established framework, routing, state and API layers. The existing web UI in the repo was deliberately *not* used as a reference; this design replaces it. If the client is being rebuilt from scratch, use whatever framework the repo standardises on; the design has no framework dependency (it is a PWA target: installable, standalone display mode, Web Push).

Open any `.dc.html` in a browser (they load `support.js` from the same folder). `Alfred.dc.html` has a scene switcher at the top that puts the prototype into every state listed below; use it to see each state live.

## Fidelity
**High-fidelity.** Colours, type, spacing, radii, copy and motion are final. Recreate pixel-perfectly at 393 pt phone width; the desktop layout is a fixed three-column composition described below. All copy in the prototypes is final microcopy and should be used verbatim (including the fixed status vocabulary in "Status words").

## Product architecture (read first)
Two places and one interruption, all rendering the same event spine.

- **The Room** (default, z-0). One scrolling timeline: Alfred's presence field at the top, conversation in the middle, composer with hold-to-talk at the bottom. Alfred's autonomous acts and notifications appear as quiet two-line rows *in the same thread*. No tab bar. A handle at the bottom edge opens the Workshop.
- **The Door** (interrupt, z-20). Full-height sheet that rises over whatever is showing when a critical action is pending. Ink and paper invert. Confirmed by a slide with travel, never a tap. Expires into a struck-through tombstone in the Room.
- **The Workshop** (z-15). Rises over the Room, same background. Segmented switcher: Activity · Memory · Triggers · System. Back chevron "Room" top-left. Its sheets (Why-thread, Held back) rise with a 45% scrim (z-16/17).
- **Gates** (z-30): setup, install-to-home-screen, standalone/notifications, sign-in, 403, 401, add-passkey. iOS permission dialog z-35. Lock-screen / home-screen context mockups z-40 (these are *context illustrations*, not app screens to build).

Every line carries an **actor**: You, Alfred (conscious), Reflex, Trigger, House, System. Actors map to real fields: `source` on every event, `fired_by` on trigger fires, the `reflex*` source prefix.

## Design tokens

### Colour — dark theme (default after sunset)
| token | value |
|---|---|
| bg | `#25221F` |
| surface / field | `#2F2B27` |
| line | `#35312C` |
| keyboard | `#1C1A17` |
| muted | `#9A9186` |
| fg2 (secondary text) | `#D9D2C8` |
| fg | `#F1ECE4` |
| accent | `oklch(0.78 0.12 45)` |
| ink / paper (Door inversion) | ink `#F1ECE4`, paper `#25221F`, paperMuted `#7D756B`, ring `#D6D0C7` |

### Colour — light theme (daytime)
| token | value |
|---|---|
| bg | `#F6F3EE` |
| surface | `#EFEAE2` |
| field | `#FFFFFF` |
| line / keyboard | `#E6E1D9` |
| muted | `#8A8177` |
| fg2 | `#4D4740` |
| fg | `#221F1B` |
| accent | `oklch(0.72 0.13 45)` (3.1:1 on bg; marks and ≥15 px text only) |
| ink / paper | ink `#221F1B`, paper `#F1ECE4`, paperMuted `#9A9186`, ring `#3D3832` |

Theme toggle: a 44×44 button top-right of the Room header (half-filled 14 px circle glyph). Theme persists.

### Colour rules
- Amber accent means exactly two things: Alfred's presence, and a decision waiting on you. Never decorative, never success, never data.
- One semantic green `oklch(0.70 0.13 150)` for "alive" and "applied" only.
- **No red anywhere.** Errors are amber (needs you) or muted (it stopped). The only red in the prototypes is the iOS home-screen badge `#FF3B30`, which is OS chrome.
- Stream ring, identical in both themes: `oklch(0.62 0.11 h)`, h = 30 + 45·i.

| mono | stream | hue |
|---|---|---|
| UR | user_requests | 30 |
| AL | user_responses | 75 |
| EV | events | 120 |
| AC | actions | 165 |
| RX | reflex_observations | 210 |
| NT | notifications | 255 |
| HS | home_state | 300 |
| HR | home_action_results | 345 |

Room actor marks use the same hues: reflex acts RX (210), trigger acts EV (120), notification rows NT (255).

### Type
Google Fonts: `DM Sans` (opsz 9..40, weights 400/500/600) and `Geist Mono` (400/500). Fallback stack `-apple-system, Helvetica, sans-serif`. `-webkit-font-smoothing: antialiased`.

Rule: DM Sans for anything a person says or reads; Geist Mono for anything the machine says about itself (times, ids, tool names, status). Alfred's voice is one size larger than yours.

| role | spec |
|---|---|
| gate | 30 px / 1.15 · 500 · −0.02em |
| headline | 26 / 1.15 · 500 · −0.02em |
| title (sheet, detail) | 20 / 1.2 · 500 · −0.02em |
| alfred | 19 / 1.4 · 400 · −0.01em |
| you (bubble) | 15 / 1.4 · 400 |
| body | 15 / 1.5 · 400 |
| row | 14.5 / 1.35 · 400 |
| event row (Activity) | 14 / 1.35 · 400 |
| button | 14–16 · 500 (DM Sans) |
| label (section caps) | 11 / 1 · 500 · +0.08em · uppercase · muted |
| meta | mono 11 / 1.5 · 400 · muted |
| payload | mono 11 / 1.55 · pre-wrap · fg2 |
| monogram | mono 9 / 22 · 500 (22 px tile), mono 10 (44 px chip) |
| stat | 20–22 / 1 · 500 · −0.02em |
| fuse | mono 40 / 1 · 500 · −0.02em |
| status line | mono 12 / 1.5 · muted |

### Spacing & shape
4 pt grid; ladder 4 · 8 · 12 · 16 · 24 · 40. Page gutter 24 (Room) and 16 (Workshop). Every tappable element ≥ 44 pt tall (visually smaller controls extend the hit area with negative margins).

| radius | use |
|---|---|
| 2 | actor mark (8×8) |
| 6 | stream monogram tile (22×22) |
| 8 | payload blocks, stream chips, status pills, raw-call box |
| 10 | notes/banners, DND expiry buttons |
| 12 | cards, System rows, bench switcher |
| 16 | Door banner, your bubble (`16 16 4 16`) |
| 22 (pill) | 44 px buttons/chips |
| 25 (pill) | 50 px composer, sheet buttons |
| 28 (pill) | 56 px mic / send / gate primary; sheet top corners |
| 32 (pill) | 64 px slide track |

### Elevation
No drop shadows inside the app. Depth is one step of surface tone plus a 1 px line.
0 bg (Room, benches) · 1 surface (banners, payloads, cards) · 2 sheet (rises, 28 radius, scrim `rgba(20,17,14,.45)`) · 3 workshop (full layer, same bg) · 4 door (full layer, ink/paper swapped) · 5 gate (full layer, presence field shown).

### Motion
Two curves only.
- `rise` = `cubic-bezier(.2,.8,.2,1)`, 380–420 ms, anything entering from the bottom (`translateY(100%) → 0`). Sheets 380 ms, Workshop 400 ms, Door 420 ms.
- `settle` = `cubic-bezier(.2,1.4,.3,1)`, 600 ms, used once: when the Door handle lands.
- Nothing fades in. Reduce-motion: presence field static, `rise` becomes a 200 ms opacity step.
- Keyframes used: `breathe` (thinking dots, 1.2 s opacity .55↔1, staggered 0/.2/.4 s), `wave` (mic level bars, .7 s scaleY .4↔1, stagger 0/.15/.3/.1 s), `nudge` (Share pointer, 1.4 s translateY 0↔6 px), `drift` (static presence fallback, 6 s background-position 0→2px,1px→0).

## Screens / Views

### 1. The Room (phone, 393 × 852)
Column flex, `overflow:hidden`, bg `t.bg`. Dynamic Island mock: 126×37 black pill at top 11, centred (omit in production; it is device chrome).

**Presence field** — `<canvas>` 393×190 absolutely positioned at the top, masked `linear-gradient(to bottom, rgba(0,0,0,.95) 30%, transparent 100%)`, `pointer-events:none`. A 12 pt dot grid (radius 1 px, alpha .55 dark / .70 light, colour `rgb(232,178,132)` dark / `rgb(205,132,80)` light; offline `rgb(154,145,134)` / `rgb(138,129,119)` at alpha .42). Dots are displaced by a height field `z`: at rest z = 0 and the field is perfectly still. While holding to talk, z is driven by an 8-band audio envelope (mic via `getUserMedia` + `AnalyserNode` fftSize 256, smoothing .6; synthesized envelope if the mic is unavailable). While thinking, a Gaussian pulse band sweeps down the field every ~2.4 s with a fainter counter-sweep, eased in over ~.6 s and out over ~1.2 s. Displacement: `px = x + z·sin(x·.022 − phase)·2.5`, `py = y − z·12`, dot radius `max(.6, 1 + .7z)`, alpha `base·(.65 + .45z)`, plus a soft shadow ellipse under dots with z > .3. Runs on `requestAnimationFrame`, dpr capped at 2. Tweak `dotResponse` (0.3–2.5, default 1) multiplies z; `useMicrophone` boolean; `reduceMotion` freezes it. Full algorithm is in `Alfred.dc.html` → `presence()` / `signal()`.

**Header** (`padding: 72 24 0`, gap 4, z-1):
- Headline 26/500: `Listening, sir.` (idle) · `Go on, sir.` (holding) · `One moment, sir.` (thinking) · `Quiet until 08:30.` (DND) · `Unreachable.` (offline) · `Reconnecting…` · `Good evening, sir.` (first day).
- Status line mono 12 muted: `21:14 · cloud 1.42 / 5.00 · reflex 380 ms · 2.1 ev/s`; offline `last true 21:14 · cloud 1.42 / 5.00 · reflex ok`; first run `first run · cloud 0.00 / 5.00 · reflex ok · 0 ev/s`.
- Offline note (when offline/reconnecting): 8 px muted dot + 13 px text on `surface`, radius 10, padding 8 12, margin-top 8. Copy: `No connection to the house since 21:14. Everything below is last-known. Sending is paused.` / reconnecting: `Trying again. Everything below was last true at 21:14.`
- DND row (when DND on): 44 px min, 1 px `line` border, radius 10, `Do-not-disturb until 08:30` left, mono `2 held ›` right. Opens the Held-back sheet.

**Timeline** (flex 1, scroll, `padding: 22 24 12`, gap 16). Row types:
- *Divider*: 1 px lines either side of mono 11 muted text (`earlier today`, `new conversation · 20:52`). A new divider appears after a 30-minute gap.
- *You*: right-aligned bubble, max 280, `padding 10 14`, 1 px `line` border, radius `16 16 4 16`, 15/1.4. Unsent (offline): opacity .6 with mono 10.5 `not sent · will retry when connected` beneath.
- *Alfred*: no bubble; 19/1.4 −0.01em text, max 330, then mono 11 meta `pleased · calendar.today, weather.forecast · 20:52` (mood · actions_taken · time). Gap 7.
- *Act* (autonomous line): `padding 10 0`, 1 px top+bottom `line`; 8×8 radius-2 actor mark in stream hue at margin-top 6; text 14.5 in `fg2`; meta mono 11 (`17:58 · reflex · 380 ms`). Optional `why?` button right (accent, 13/500, 44 px hit area).
- *Tombstone*: `surface` bg, radius 12, `padding 10 12`, opacity .8; 8 px hollow circle (1.5 px muted border); text 14.5 struck through; meta `expired 07:46 · not done · asked 07:41`.
- *Prompt* (Reach): Alfred text + meta + two buttons: `Show me how` (filled ink/paper, 44 px, radius 22, padding 0 18) and `Not now` (outlined, padding 0 14).
- *Transcribing*: right-aligned dashed-border bubble, italic muted `Transcribing…`, mono 10.5 `audio sent · 2.4 s · waiting on server`.
- *Thinking*: three 6 px accent dots with `breathe`, mono 11 `conscious mind · calendar.today running`.
- *First day (empty)*: centred 19 px `Good evening, sir. Nothing has happened yet; I'm watching the house and listening for you.` + mono `first run · no memories · 0 routines · hold the button to speak`.
- Timeline autoscrolls to the bottom when items change or thinking toggles.

**Door banner** (when a critical action is pending and the Door is closed): `margin 0 16 10`, `padding 14 16`, radius 16, bg `ink`, colour `paper`. 34 px fuse ring (conic accent over `ring`, masked to a 1 px stroke… inner radius 14/15), title 16/500 `Unlock the front door`, mono 11 paperMuted `expires in 4:12 · asked by Alfred, for you`, `Open` in accent 13/500 on the right.

**Composer** (`padding 0 20 8`, gap 10): input 50 px, radius 25, 1 px `line`, bg `field`, `padding 0 18`, 15 px; placeholder `Ask or tell Alfred` / offline `Offline · will send when connected` (disabled). Right: if draft non-empty, 56 px send button (ink bg, paper chevron); else 56 px hold-to-talk button: 1.5 px accent border, transparent, 14×22 radius-7 accent pill glyph (muted when offline). Enter sends.

**Workshop handle** (below composer, hidden when keyboard is up): 44 px min button, mono 11 muted `workshop` under a small up-chevron, then a 139×5 radius-3 bar in `fg`. Keyboard-up state: composer stays above a 290 px keyboard block (`keyboard` bg, 1 px `line` top).

**Hold to talk**: on pointer-down the button scales 1.12, fills accent, shows four 3 px `ink` bars with `wave`; headline becomes `Go on, sir.`; a centred mono 11 caption at bottom 84: `recording 0:04 · release to send`. On release with ≥1 s recorded: dashed `Transcribing…` bubble → replaced in place by the server's text as a You bubble → thinking → Alfred reply.

### 2. Sheets (over Room or Workshop)
Scrim `rgba(20,17,14,.45)` (tap closes). Sheet: bottom-anchored, max-height 78%, bg `t.bg`, radius `28 28 0 0`, `rise` 380 ms. 40×5 grab bar (`line`) at top; header row `padding 8 20 12`: title 20/500 left, `Done` accent 15/500 right (44 px). Body `padding 0 20 40`, gap 12.

- **Held back** (deferred notifications): intro 13.5 fg2 `Non-urgent notifications wait here while do-not-disturb is on. Urgent ones still speak. With no expiry set this queue never drains on its own.` Rows: NT mark + 14.5 text + mono meta (`important · 07:02 · deferred by DND`), 1 px top line, `padding 12 0`. Button 50 px outlined `Drain queue now` → label `Queued`; note beneath: `Queued only; the server does not report delivery. Items stay listed until a fresh read confirms.` → `Accepted at HH:MM. Queue will empty on the next refresh if delivery succeeded.`
- **Why Alfred did that** (causal thread): intro `Every link drawn solid is joined by an id the server holds. Dashed means adjacent in time only.` Grid `22px 1fr`, gap 12; left column: 22 px monogram tile then a 1.5 px vertical connector (`solid` when joined by id, `dashed` when adjacent in time only), `margin 4 0`, min-height 22. Right: 14.5 text + mono 11 meta, `padding-bottom 14`. Chain for the door example: HS `binary_sensor.front_door → on` → RX `Reflex watched it for 120 s, then chose to speak` (joined by trigger_event) → AC `speak("The front door has been open two minutes, sir.")` (joined by request_id) ⇢ NT `Spoken aloud, priority important` (adjacent only) → HS `binary_sensor.front_door → off` (`not caused by the above`).

### 3. The Door (critical approval)
Full layer, bg `ink`, colour `paper`, `rise` 420 ms. Header `padding 64 24 0`: `‹ Leave it` (paperMuted 15/400, 44 px) left; mono 11 paperMuted `CRITICAL · a91f` right.
Centre (gap 28): **fuse ring** 168 px, conic `fuseColor` over `ring`, masked to a 1 px stroke (transparent inside 79 px, solid from 80 px), `transition: background .9s linear` so each 1 s step animates linearly. Inside: mono 40/500 `4:12` and mono 11 paperMuted sub (`until it lapses` / `lapsed` / `confirmed HH:MM`). Below: title 30/500 `Unlock the front door`; reason 15/1.45 paperMuted max 300 `You asked me to let the cleaner in when she rings. She rang at 07:41.`; raw call in mono 11.5 inside a 1 px `ring` box radius 8 `padding 8 12`: `home.lock_unlock { entity_id: "lock.front_door", action: "unlock" }`.
Footer `padding 0 20 40`, gap 12:
- *Pending*: slide track 64 px, radius 32, bg `ring`, `touch-action:none`; hint 15 px paperMuted `Slide to unlock the door` centred (padding-left 56), fades with knob travel; knob 56 px accent circle at (4,4) with a 10 px ink chevron. Foot mono 11 `Approval only; the lock itself reports back separately. Releasing before the end snaps back.`
- *Confirmed · queued / Applied / Expired*: 64 px outlined pill (1.5 px `ring`) with 8 px dot + 16/500 word; foot text; `Back to the room` 50 px text button.
- Foot copy: queued `Sent to Home Assistant. Waiting for the lock to report (request a91f).` · applied `lock.front_door reported unlocked at HH:MM.` · expired `The five minutes ran out at 07:46. Nothing was done. Ask again to get a fresh one.`
- Dot colour: accent (queued), green (applied), paperMuted (expired).
- Under 30 s the arc colour changes from accent to `paper`. Nothing pulses.
- Offline: track disabled/greyed; foot `Cannot confirm while offline; the fuse is still running on the server.` Ring keeps ticking from last-known TTL, labelled as such.
- Already consumed (404 on confirm): same tombstone as expiry with `already answered`.

### 4. Workshop shell
Full layer over the Room, same bg, `rise` 400 ms. Header `padding 62 16 0`, gap 10: `‹ Room` (accent 15/500) left, mono 11 status right (`live · 2.1 ev/s` · `paused · 3 new` · `last true 21:14 · not live`). Bench switcher: 44 px, radius 12, bg `surface`, padding 3, gap 3, four equal buttons radius 9 (active: bg `field`, fg; inactive: transparent, muted) 13/500.

### 5. Activity bench
Stale banner when offline (`surface`, radius 10, 8 px muted dot): `Feed stopped at 21:14. Nothing below is live.`
List (scroll, `padding 8 16 0`): 44 px mono muted button at top `↑ older · before cursor <last id>` (after paging: `fetched before cursor …`). Rows: 1 px top `line`, grid `22px 1fr` gap 10, `padding 9 0`, min-height 44; 22 px monogram tile (radius 6, hue bg, white mono 9/500); line 1 14 px ellipsised; line 2 mono 11 muted ellipsised (`21:02:11 · 380 ms · decision "movie started, evening" · watch-listed`). Tap expands in place: payload `<pre>` (`surface`, radius 8, `padding 10 12`, mono 11/1.55 fg2, `pre-wrap`, `margin 0 0 12 32`) + 36 px outlined pill buttons `Why · causal thread` (accent, RX rows only) and `Only RX`.
Empty solo: centred `Nothing on this stream yet.` + mono `UR · 0 in the last 24 h · stream exists, no producers have written`.
Footer (1 px top line, `padding 10 16 0`): eight stream chips flex 1, 44 px, radius 8, 1.5 px border in hue, mono 10/500 mono + count (solo: filled hue, white; non-solo when a solo is active: opacity .35). Then `Pause feed` 50 px filled ink (paused: accent bg, ink text, `Resume · 3 new`) and `All streams` outlined (opacity .4 when no filter). Bottom padding 30.
Behaviour: live feed inserts at top (prototype every 3.5 s); paused counts on the button, Resume inserts held rows in one step with no per-row animation. Solo dims nothing on phone (filters); on desktop other streams are dimmed to opacity .35 rather than removed. Desktop keyboard: J/K move, Enter opens payload, Space pauses, 1–8 solo a stream.

### 6. Memory bench
Sub-tabs (44 px pills, radius 22, active filled ink/paper): Episodic · Semantic · Routines · Scratchpad. Body `padding 12 16 40`, gap 12.
- **Episodic**: mono note `Browsing here does not count as recall. Nothing you open is kept warmer or colder for it.` Rows 1 px top, `padding 11 0`: 8 px circle with 1.5 px accent border (filled accent = hot, transparent = cold); text 14.5/1.4 (cold rows in `muted`); meta `20:52 today · significance 0.62 · recalled 1× · hot`. Footer: search input 50 px `Search by meaning` + mono status pill `model: ok` / `model: 503`. 503 state: `surface` card `Embedding model is still loading.` / `503 · search by meaning unavailable · the list below is by recency`. No results: `Nothing close enough to "…".` / `best score 0.31 · threshold 0.55 · 128 hot, 1 204 cold searched`.
- **Semantic**: note `Human-readable documents the conscious mind reads before every reply. Rewritten by the nightly consolidation.` Cards (`surface`, radius 12, padding 14): title 15/500 + mono meta (`rewritten 03:00`), body 14/1.55 fg2 `pre-line`.
- **Routines**: note `Patterns Alfred noticed on its own. Ignored suggestions lose confidence and slide right until archived.` Rows `padding 12 0`: text 14.5 (dormant/archived in muted) + mono confidence/trend right (`0.86 +0.04`, accent when rising). Lifecycle rail: 4 columns, 3 px bar (accent = current, muted = passed, line = ahead) with mono 10 labels `candidate · active · dormant · archived`. Tap expands: mono detail block, 8-bar confidence sparkline (28 px tall, accent .7), caption `confidence, last 8 consolidations`.
- **Scratchpad**: note, then a `surface` mono 12/1.65 pre-wrap block of working notes; two stat cards (1 px line, radius 12): `14` / `episodes queued, unscored` and `03:00` / `next consolidation · last 03:00 today`.

### 7. Triggers bench
Kind chips: All · Time · Schedule · Sensor · Composite. Rows 1 px top, `padding 11 0`: mono 10 accent kind (`time`) + 14.5 name; meta (`one-shot · fires 08:40 tomorrow · created from conversation 20:52`); 52×32 switch on the right (track accent on / `line` off, 26 px knob in `t.bg`, left 3 → 23, 200 ms). Done one-shots at opacity .55. Toggling: switch does **not** move; an accent mono note appears `queued 21:15 · enabling · takes effect within 60 s`; the row flips only after a fresh read (prototype: 6 s). Tap expands: payload `<pre>`, `Fire now` 44 px outlined (→ `Fire again`) with note `queued only; look for trigger.fired on the events stream to know it ran`. Corrupt record: `surface` card `This record can't be read.` / `500 · condition JSON fails to parse at byte 118 · the scheduler skips it · fix in the store or delete`; switch and fire disabled. Footer note: `Switches are fire-and-forget: the server queues the change and the scheduler picks it up within 60 s. A row keeps its old state, with a note, until a fresh read confirms.`

### 8. System bench
Sections (caps label 11/500 +.08em muted; gap 22 between sections; cards 1 px `line`, radius 12):
- **Health**: stamp right (`live · 21:14:07` muted / offline `unknown since 21:14` accent). 2×2 stat cards, `padding 12 14`: 8 px dot (green alive / muted unknown) + 20/500 value + mono label: `alive · bus · redis 6 services, 8 streams`, `380 ms · reflex · reflex-3b · gpu 41%`, `2.1/s · event rate · 5-min mean`, `ok · home assistant · 210 ms`. Offline: values `?`/`—`, opacity .55. Spend card: `Cloud spend today` / mono `£1.42 of £5.00`, 4 px bar 28.4% accent on `line`, note `38 requests · avg £0.037 · resets 00:00 · at the cap, the conscious mind declines and says so`.
- **Quiet**: `Do-not-disturb` row (52 px min) with switch; sub `off · urgent still speaks regardless` / `on · until 08:30 · queue drains then` / `on · no expiry · queue will not drain on its own`. When on: expiry chips `1 h · until noon · until 22:00 · no expiry` (44 px, radius 10) and `Held back` row with `2 held ›` (accent `2 · growing` when no expiry). DND set/clear is a direct write → `Applied`.
- **Reach** (push notifications): sub varies by context: `unavailable in Safari · add to home screen first` + `Show me how` · `off · not yet asked · iOS asks once` + `Enable` · `on · this iPhone · important and urgent only · badge = waiting approvals + unread urgent` (green dot) · `off · declined in iOS · this app cannot flip it` (accent dot) with note `Settings › Alfred › Notifications › Allow. iOS holds this switch now; nothing in here can flip it, and Alfred will not raise it again.`
- **Sessions**: rows 56 px: name + mono meta (`passkey · pwa · signed in 07:02 · 192.168.1.24`); right `current` (this device, disabled) / `End` (accent) → `ended HH:MM · applied`, opacity .5.
- **Connected services**: Home Assistant · Weather · Calendar · Health · Finance. State word + dot right: `ok` green, `failed` accent, `unset` muted. Tap expands: password input 48 px radius 10 mono 14, `Save & test` filled 44 px (→ `Testing…`), note per state (`stored encrypted at rest · last check ok`, `401 from the service on the last check · stored value kept until you replace it`, `nothing stored · Alfred answers without this source`, `round-trip in progress · up to 10 s`).
- **Devices & identity**: passkey rows (`iPhone 15 Pro · passkey · registered 12 Aug · Face ID · this device`), `Add a passkey on another device` (accent) with `2 registered`, `Sign out on this device`.
- **Maintenance**: `Nightly consolidation` / `last 03:00 · 42 reviewed`; `Run consolidation now` outlined (→ `Run again`) with note `queued only; the run reports on the events stream, not here` → `queued HH:MM · progress shows on the events stream as consolidation.*`.

### 9. Gates (identity and reach)
Full layer, bg `t.bg`, presence dot-field at top (220 px, static `drift`), content bottom-aligned `padding 0 28 12`, gap 12: mono kicker · 30/500 title · 15/1.5 fg2 body · optional step list (48 px rows, 1 px top line, 22 px ring: accent filled = done, fg ring = current, line = ahead; 14.5 label; mono meta right). Footer `padding 8 20 40`: primary 56 px filled ink/paper 16/500, optional secondary 50 px text button, mono foot centred.
- **setup** (3 steps): `first run · alfred.local` / `Good evening. I am Alfred.` / body about the passkey being the only key; `Create passkey with Face ID`; foot `The passkey never leaves the phone. Nothing here phones home.` → `Registered.` (Home Assistant token; `Continue` / `Do this later`) → `What may the reflex touch?` (Lights · 6 found allowed, Media players · 2 found allowed, Fans & plugs · 4 found ask me; `Finish`; foot `Change this any time under Workshop › System.`). Finishing lands on the First-day Room.
- **install** (Safari, sheet stops 115 px above the bottom to leave Safari's toolbar visible): `reaching you · 3 of 6 · in Safari` / `Add me to the home screen.` / steps Tap Share · Scroll to "Add to Home Screen" · Tap Add; no primary; `Not now`; animated `Share ˅` pointer in accent at bottom (`nudge`).
- **standalone** (opened from home screen, 6-step rail with step 5 current): `You came in the front way.` / `Let me reach you` → triggers the iOS permission dialog / `Not yet`; foot `iOS asks once. Decline, and the switch moves to Settings; I shall not raise it again.`
- **signin**: `alfred.local · signed out` / `Welcome back, sir.` / `Sign in with Face ID`; foot `Passkey · iPhone 15 Pro · registered 12 Aug`.
- **denied** (403 off-network): `Not from here.`; only `Back to the room`; foot `Last true 21:14 · everything shown behind this is last-known`.
- **expired** (401): `Your session lapsed.` / `Sign in with Face ID`; foot `Conversations and settings are on the server; nothing is lost.`
- **passkey** (from System): `Add a passkey.` 2 steps; `I see the code · confirm` / `Cancel`; foot `Pairing window closes 21:16`.
- Outcome of the iOS dialog is written into the Room as an Act row: `Notifications on for this iPhone` / `Notifications stay off on this iPhone · iOS declined · Settings › Alfred › Notifications to change · not asked again`.

### 10. Context mockups (do not build)
Safari toolbar, lock-screen notification, home-screen badge and the iOS permission dialog in the prototype illustrate OS behaviour: a time-sensitive push whose tap deep-links to the specific approval (even after it lapsed, landing on the tombstone), and a badge count = waiting approvals + unread urgent, cleared on open.

### 11. Desktop (`Alfred Desktop.dc.html`)
Grid `420px 1px minmax(380px,1fr) 1px minmax(360px,520px)`, 1 px `line` dividers, 100 vh columns, dark theme shown. Left: the Room, identical to phone (header `padding 40 28 0`, gutter 28, composer with `⌘K` hint). Middle: Workshop bench (label `WORKSHOP` caps + `live · 2.1 ev/s`; switcher max 520; stream chips 36 px with `All` and `Pause` inline; list max 568). Right (bg `#2A2723`): detail column, e.g. the causal thread with `esc closes`. Below 1100 px the detail column becomes a sheet over the bench; below 760 px the layout is the phone. The Door still covers all three columns.

## Interactions & behaviour

- **Slide to confirm** (Door): travel `max = trackWidth − 64` (≈ 289 at 393). Finger ratio `r = dx / max` clamped 0–1; eased position `r < .25 ? r·0.6 : 0.15 + (r − .25)·1.1333` (resists at the start). Hint opacity `1 − knob/(max·.5)`. Release at ≥ 85 % → knob snaps to end with `settle` (600 ms, overshoot ≈ 4 pt), state → `Confirmed · queued`, POST confirm. Release below → snaps home (transform 260 ms). Applied only when `home_action_results` carries the same `request_id` (prototype: 2.6 s). While dragging, `transition: none`.
- **Fuse**: 300 s TTL from the server's `expires_at`; ring is a conic arc stepped once per second with a 900 ms linear transition. Reaching 0 → `expired` and a tombstone row is appended to the Room. No count-in animation on arrival.
- **Hold to talk**: pointer down/up/leave; 1 s counter; mic stream started on press and stopped on release; <1 s releases do nothing.
- **Send**: Enter or send button; offline appends an unsent bubble that retries on reconnect.
- **Why?**: opens the causal-thread sheet from any autonomous Room row or RX Activity row.
- **Workshop**: handle opens (rise 400 ms); `‹ Room` closes; swipe-back on detail views.
- **Activity**: tap row toggles payload; chip tap solos / un-solos; Pause holds inserts and counts; page-back appends older rows and shows the cursor id fetched before.
- **Trigger toggle / Fire / Drain / Consolidate / Save & test**: all fire-and-forget → button label becomes the status word; row state unchanged until re-read.
- **Theme**: manual toggle; default dark after sunset, light in daytime.
- **Persistence**: theme, scene, timeline, door state, fuse and DND persist locally (prototype uses `localStorage` key `alfred.proto`); the real client should read these from the server.
- **Reduce motion**: presence static, rise → 200 ms opacity step.

## State management
Client state (as in the prototype): `theme`, `ctx` (standalone|safari|lock|home), `notifPerm` (unknown|granted|denied), `draft`, `focused`, `holding`, `holdT`, `thinking`, `transcribing`, `items[]` (Room rows), `sheet` (null|deferred|why), `doorOpen`, `doorState` (pending|queued|applied|expired), `fuse`, `fuseTotal`, `knob`, `dragging`, `deferred[]`, `dndOn`, `dndExpiry`, `drainState`, `workshop`, `bench`, `events[]`, `solo`, `paused`, `heldCount`, `openEvent`, `pagedBack`, `gate`, `gateStep`, `sessionsEnded`, `openService`, `svcDraft`, `svcState`, `consolidateAt`, `passkeyAt`, `memTab`, `memQuery`, `memLoading`, `openRoutine`, `trigKind`, `openTrigger`, `trigPending`, `trigFired`, `trigState`.

Data sources (backend as documented in `docs/`, `shared/streams.py`, `bus/schemas/events.py`, `core/memory/schemas.py`, `core/triggers/models.py`, `core/routing/pending.py`):
- Room rows = filtered `user_requests`, `user_responses` (mood, actions_taken), `actions`/`reflex_observations` with a `reflex*` source, `notifications`. Activity = all 8 streams unfiltered with payloads; paging by stream cursor id.
- Door = a pending critical action (`request_id`, tool + params, reason, `expires_at`, 300 s TTL); confirm endpoint returns applied; 404 = already consumed.
- Causal thread joins: `session_id` (UR→AL), `actions_taken`, `request_id` (AC→HR), `trigger_event` (HS→RX); anything else is adjacency in time and must be drawn dashed.
- Memory: episodic search (hot/cold, significance, recall count; 503 while embedding model loads), semantic documents, routines with lifecycle + confidence history, scratchpad and consolidation queue.
- Triggers: kinds time/schedule/sensor/composite, one-shot flag, `last_fired`, enable/disable and fire are queued with a 60 s cache window.
- System: health, spend vs cap, DND (set/clear applied; deferred queue drain queued), sessions (end applied), credentials (set/test), passkeys, consolidation (queued).
- Status vocabulary is closed: `queued`, `applied`, `last true HH:MM`, `unknown since HH:MM`, `hot / cold`, `candidate · active · dormant · archived`, `expired · not done`, `takes effect within 60 s`. Always mono, lower case. Nothing else may describe system state.

## Assets
- Fonts: DM Sans and Geist Mono from Google Fonts (`https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600&family=Geist+Mono:wght@400;500`). Self-host for the PWA.
- No raster images. All glyphs (chevrons, mic pill, half-circle theme toggle, share/tabs icons in the Safari mock) are CSS shapes; replace with the codebase's icon set at the same sizes (9–14 px strokes 1.5–2 px).
- The presence field is procedural canvas; port the algorithm from `Alfred.dc.html` (`presence()`, `signal()`).

## Files
- `Alfred.dc.html` — phone prototype, all states via the scene switcher; full logic (presence field, slide math, fuse, feed, benches, gates).
- `Alfred Desktop.dc.html` — three-column desktop composition.
- `Alfred Design System.dc.html` — tokens, type scale, shape, elevation, component families, motion spec with the Door handle demos.
- `00 Architecture and Reasoning.dc.html` — product architecture, actor model, status vocabulary, surface inventory.
- `support.js` — runtime the prototypes need to open in a browser; not part of the design.
