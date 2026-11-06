# PWA phase 5 slice 2: asking in the Room, and the icon badge

**Status:** design, approved by the owner on 2026-11-04. Nothing is built.
**Follows:** slice 1 (PR #417, plan `docs/superpowers/plans/2026-10-29-pwa-phase5-web-push-slice1.md`).
**Parent spec:** `docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md`, §6.3 and §6.4.
**Design source:** `docs/design/2026-09-04-pwa-client-handoff/` (`README.md` and `Alfred.dc.html`).

## Why

Slice 1 made Web Push work, and the owner's iPhone receives it. Three gaps remain:

1. Someone using Alfred in a Safari tab is never told it could reach them with the tab closed.
2. Someone who installs it is not asked for notifications. The iOS dialog can only be reached
   through Workshop › System › Reach › `Enable`, so a person who follows the install steps lands
   on the home screen and nothing happens. Spec §6.4 names this six-step path as the problem to
   design for.
3. A push that was missed leaves no trace on the icon.

This slice closes those three. On an iPhone that is already set up, only the badge is new. The
rest is for the next device.

## Goals

- A browser tab on an iPhone offers the install path at the moment it matters: a Door is waiting
  and the tab can't knock.
- The first launch from the home screen asks once, through the gate the handoff already draws.
- The answer to the iOS dialog is written into the Room.
- The icon shows how many pushes have arrived since the app was last opened.

## Non-goals

- Tracking seen and unseen per notification: #424. This slice's zero-on-open is a known
  simplification the owner accepted for now.
- Alfred taking back a suggestion whose reason has gone: #425.
- A `pushsubscriptionchange` handler: #427. Dropped by the owner's agreement. Reports say iOS
  doesn't fire it, a rotation can't be forced to test it, and the launch sync already re-registers
  on every open.
- Wording for desktop and Android browsers: phase 6.
- Any server change. There is no new route, no new setting, and `build_payload()` and the payload
  contract stay as they are. Every change is in `web/`, plus docs.

## Anti-goals

These would make the slice a failure even if every test passed.

- **Badge work costs a notification.** Safari requires every push to show a notification and
  removes subscriptions that stay silent. Nothing about the badge may delay or prevent
  `showNotification`.
- **Asking again after an answer.** A decline or `Not yet` is not followed by another automatic ask,
  and `Not now` holds until a new Door arrives. `Enable` in Reach stays available for someone who
  changes their mind.
- **Raising the iOS dialog without a tap.** iOS needs the gesture, and `enable()` must stay the
  first thing in the tap handler (`usePush.ts`).
- **A row that stops being true.** The prompt row says a door is waiting. It can't outlive the Door.
- **A number that claims to be something else.** The badge counts pushes since the last open. Copy
  must not say it counts approvals.
- **A promise the house can't keep.** On a house with no Web Push key, the row's "I can knock" is
  false and the gate would spend iOS's one ask for nothing.

## What gets built

### 1. The prompt row

Appears in the Room's timeline when all of these hold:

- the page is a browser tab (`reachContext() === "tab"`) on an iPhone;
- at least one Door is waiting (`door.pending`) that `Not now` has not already answered on this
  browser;
- the house has a Web Push key (`fetchPublicKey()` returned one).

The key is read only once the other two hold. While that read is pending, or if it fails, there
is no row.

It leaves as soon as no such Door is waiting. Several Doors give one row. It is derived from those
facts, so a reload brings it back while such a Door still waits.

It takes the time of the first such Door for its place in the timeline and for its meta line.

Copy and look are the handoff's, verbatim (`Alfred.dc.html:75-77` and `:751`, README `:143`):

- Text: `That door is waiting on you below. Had this tab been closed, I would have had no way to tell you. Add me to the home screen and I can knock when something cannot wait.`
- Meta: `notifications · unavailable in Safari · <hh:mm>`
- `Show me how` opens the existing install gate. The row stays behind it, and the gate's `Not now`
  only closes the gate.
- `Not now` removes the row until a Door arrives that was not waiting when it was tapped. One tap
  answers every Door waiting at that moment, and that holds across a reload. It writes no line into
  the Room, because the handoff's line (`Alfred.dc.html:835`) says `I shan't raise it again`, which
  would be false (D11).

### 2. The standalone gate opens itself

In the installed app on an iPhone (`reachContext() === "capable"`), with the permission still
`default`, a session and a Web Push key on the house, the `You came in the front way.` gate opens
once on launch. It is the gate slice 1 built; only the opening is new. A failed key read skips this
launch and tries again on the next.

- `Let me reach you` calls `enable()`.
- `Not yet` closes it.
- A tap on either button ends the self-opening on this device, even if iOS's dialog is then
  dismissed without an answer. Reach keeps `Enable`.
- A launch that ends with neither button tapped shows it again next time, because nothing has been
  decided.
- It never opens when the permission is `granted` or `denied`, so an iPhone already set up sees
  nothing new.
- If anything modal is up when it would open, this launch skips it and writes nothing (D12).

The Room hosts it, along with the install gate from section 1, the way `ReachSection` hosts its own
pair. Reach's own opening of these gates is unchanged.

### 3. The Act rows

Written on an iPhone when the iOS dialog answers, from either route to it (the Room's gate or
Reach's), verbatim from `Alfred.dc.html:752`, hue 255 (the notification stream's). Other devices get
no row, because the copy names iOS and this iPhone; Reach still says where they stand.

| iOS answer | Text | Meta |
|---|---|---|
| Granted and registered | `Notifications on for this iPhone` | `<hh:mm> · notifications · iOS allowed · important and urgent only` |
| Declined | `Notifications stay off on this iPhone` | `<hh:mm> · iOS declined · Settings › Alfred › Notifications to change · not asked again` |
| Dismissed with no answer | no row | |
| Granted, but the house did not take the registration | no row | |
| Granted, but the house has no Web Push key | no row | |

The last two are deliberate: `on` would lie (deviation D4 in slice 1's plan), and Reach already says
where things stand (`failed` with `Try again`, or `unavailable`). Only Reach's `Enable` can reach the
last one, since the Room's gate needs a key to open.

### 4. Where this lives on the device

The Act rows are about this device, so they are kept in this browser's `localStorage`, not sent to
the house. Other devices do not see them. `Not now`'s answered Doors and the gate's flag are kept the
same way. Unreadable or corrupt storage loses them and never the app, as `useRoom`'s unsent queue
already handles it.

The Act rows behave like history rows: shown at their time, and gone once the Room's session window
has moved past them.

### 5. The icon badge

The owner's rulings:

- Every push adds one, urgent and important alike. INFORMATIONAL is never pushed, so it never counts.
- Opening the app sets it to zero, for now (#424).
- A notification that lapses while the app is closed stays counted until the app is opened. A closed
  PWA can't lower the number silently.

How it works:

- **The worker keeps the count in IndexedDB.** A worker dies between pushes and `setAppBadge` has no
  getter, so the count needs a store. It is not the Cache API, because rule 1 at the top of `sw.js`
  says the caches hold the app shell and nothing else. The increment runs in one read-write
  transaction, so two pushes at once both count.
- **`push` shows the notification first and unconditionally.** Badge work is a separate branch of
  `waitUntil`. Its failures are swallowed and it has a time limit, so a broken or hung store can
  neither stop the notification nor hold the event open.
- **A push that arrives while the app is on screen ends up not counted, and the page decides that,
  not the worker.** The worker always adds, then posts a message to every open window, the way
  `OPEN_PATH` travels. A page that is running, on screen and inside `AuthGate` answers by clearing.
  A suspended page runs nothing, so its count stands, and it clears on its return anyway. The worker
  never reads `visibilityState`: that reading is unverified from an iOS worker, and if it were wrong,
  pushes to a backgrounded app would never count. This way the worst case is a number that shows for
  a moment, or sits until the next return to the foreground. The notification is still shown, since
  Safari requires it.
- **Opening means the app on screen inside `AuthGate`.** `PushKeeper` already lives there and mounts
  with each sign-in, so the count and the badge clear on its mount and on every return to the
  foreground (`onVisible`), only in the `capable` context. A person at the sign-in gate on launch
  has not opened it yet. A session that lapsed while the app was in the background is the exception:
  `PushKeeper` stays mounted under the Expired gate, so the number clears just before that gate
  rises. That is accepted, because what counts as seen is #424's to define.
- **Sign-out clears them too.** `forgetPush()` ends this device's pushes, and a number left on the
  icon would never move again.
- **Notifications already in Notification Center are left alone.** Opening the app clears the number,
  not the shade.
- Everything is feature-tested. A browser with no `setAppBadge` still counts nothing and loses nothing.

Any change to `sw.js`'s bytes installs a new worker, so every phone sees `A newer Alfred is waiting`
once. Pushes count from the moment the new worker is active.

### 6. Reach's line

The handoff's `on` line, `on · this iPhone · important and urgent only · badge = waiting approvals + unread urgent`,
no longer describes the behaviour. Slice 1's plan said slice 2 would restore it verbatim (D7). It
ships instead as:

`on · this iPhone · important and urgent only · badge = notifications since you last opened`

The clause shows only where `setAppBadge` exists; elsewhere the line stays as slice 1 ships it. This
goes in the Deviations table below.

## Deviations from the handoff

| # | Where | Handoff | This slice ships | Why |
|---|---|---|---|---|
| D8 | Reach, on | `… · badge = waiting approvals + unread urgent` | `… · badge = notifications since you last opened` | The owner's ruling on what counts. The handoff's clause would be false |
| D9 | Prompt row, self-opening gate, Act rows | Drawn on an iPhone only, no rule for other devices | Only on an iPhone. Other devices keep Reach, as they do today | Their copy names Safari, iOS and this iPhone, which would be false elsewhere. Desktop and Android wait for phase 6; iPad is a small follow-up if it's wanted |
| D10 | Prompt row meta time | Sample `07:41` | The time of the first waiting Door `Not now` has not answered | Sample data, not copy |
| D11 | Prompt row, `Not now` | Retires the row for good and writes `Very good, sir. I shan't raise it again; the switch stays under Workshop › System should you change your mind.` into the Room | Hides the row until a new Door arrives, and writes no line | The owner's ruling. The handoff's line would be false |
| D12 | Self-opening gate, with something already up | No rule: drawn on its own over the Room | If anything modal is up when the gate would open (a Door, the Workshop, a sheet, Reach's own gate, the Expired or Denied gate), this launch skips it. The launch's latch is set and nothing is written, so the next launch offers it. The check runs in a passive effect, so it counts what the same commit raises under the Room. A surface above the Room raised in that commit (the Expired or Denied gate) is accepted as not counted, as is a tap in the one task between the check and the gate's commit, which can still raise the gate over a Door | The controller's ruling, beyond the handoff. Over a Door with a live fuse the only quick way back was `Not yet`, which ends the opening for good. Over Reach's own gate there were two identical dialogs, and the lower one could ask iOS a second time and write a second row |

## Constraints

- The repo is public: placeholders only, and the secret-hygiene checks in the runbook run before any
  push. Never quote them in a committed file.
- `sw.js` rule 3: every worker global goes through `self.`, and `eslint.config.js`'s
  `no-restricted-globals` enforces it. `indexedDB` and `navigator` join the list, and the header's
  count and its "caches nothing for either" paragraph change to match. The worker still fetches
  nothing for a push.
- The worker and the page share the IndexedDB database and store names. `sw.js` is hand-written and
  not bundled, so a test pins that both agree, as `src/test/sw-server-routes.test.ts` does for routes.
- Handoff copy is verbatim. Anything invented goes in the Deviations table.
- Every review finding gets fixed, Minors included.

## How it is proven

**In the test suite:**

- The worker: every push shows its notification when the store rejects, when it hangs, and when
  `setAppBadge` is absent; two pushes at once count as two; windows are told only after the count is
  written.
- The page: the badge clears on mount, on return to the foreground, on the worker's message while
  visible, and on sign-out; it does not clear on the message while hidden, or in a tab.
- The prompt row: each condition, including a house with no key and a failed key read; leaving with
  the last Door, one row for several Doors, `Not now` holding across a reload, and the row returning
  with a new Door but not for one that was already waiting.
- The gate: opens once and only on an iPhone, not for `granted` or `denied` or with no key; either
  button holds across a reload; a launch with neither shows it again.
- The Act rows: each outcome in section 3, from both routes, and no row off an iPhone.
- Reach's line: the badge clause only where `setAppBadge` exists.

**On a real phone,** because a test can't settle these:

- With the app closed, two pushes show `2` on the icon, and opening clears it. The owner can run this
  right after the deploy, using the same test push as slice 1.
- With the app in the background, a push adds to the number. With it on screen, a push leaves no
  number behind.
- The prompt row, the gate and the declined Act row need a fresh install, so they join
  `docs/superpowers/qa/2026-10-29-pwa-phase5-ios-checklist.md`, which the owner deferred to the end of
  the PWA.

## Docs that must change with it

- `docs/web-frontend.md`: lines 29-30, "Not in slice 1" (about 1848-1857) and "What phases 1 to 5 do
  not cover" (about 1873-1876). `pushsubscriptionchange` moves to #427.
- `web/README.md:164-166`. It already wrongly says Reach's `on` line names the badge. The code at
  `ReachSection.tsx:37` does not.
- `docs/PRD.md` row 78: extend the existing row and add this PR and plan. No new row, so no new
  `evals/coverage.yaml` entry.
- The checklist's "Nothing lies" lines that say the prompt row and the permission prompt arrive in
  slice 2.

## Who decided what

**The owner:** every push counts; zero on open for now; a lapsed notification stays counted; taking
back a suggestion is out; `pushsubscriptionchange` is dropped to a ticket; the prompt row appears with a
Door and goes when it ends; `Not now` holds only until the next Door, over the handoff; the Act rows
stay on the device; the gate opens by itself once; a push on screen doesn't add; Reach's new wording.

**Calls made while writing this (the owner saw them before approving):**

- `Not now` writes no line into the Room, since the handoff's line would be false.

- IndexedDB for the count, and a time limit on the badge branch.
- The page, not the worker, decides a push arrived on screen, so the number can over-count for a
  moment but never under-counts.
- The prompt row, the self-opening gate and the Act rows are iPhone only (D9).
- The row and the gate both need a Web Push key on the house, read through the existing public-key
  route.
- No Act row when the iOS dialog is granted but registration fails or the house has no key.
- The prompt row's place and meta take the time of the first waiting Door `Not now` has not answered
  (D10).
- A tap on either gate button ends the self-opening, even if iOS's dialog is dismissed. A launch with
  neither tap shows it again.
- Sign-out clears the badge; opening leaves Notification Center alone; a session that lapsed in the
  background clears just before the Expired gate rises.
- Reach's badge clause shows only where `setAppBadge` exists.
- Row 78 is extended instead of adding a PRD row.

## Left to the builder

- How to detect an iPhone.
- Whether the prompt row is a new `TimelineItem` kind or composed in `Room`, and how the device-local
  rows join `useRoom`'s merge so they follow the session window without extending it.
- Whether the Room reads the sessions list for the gate's `signedInAt`, or omits it under slice 1's D5.
- The IndexedDB and `localStorage` names and shapes, and the badge branch's time limit.
- `fake-indexeddb` or a seam in `src/test/sw-harness.ts` for the worker tests.
- The file layout, within the repo's one-responsibility rule.
