# PWA phase 5 — manual iOS checklist

Spec §7's manual half, for what phase 5's first slice added: Web Push, the System › Reach
card, and the `install` and `standalone` gates behind it. The phase-1 to phase-4 checklists
(`2026-09-07-pwa-phase1-ios-checklist.md`, `2026-09-10-pwa-phase2-ios-checklist.md`,
`2026-09-16-pwa-phase3-ios-checklist.md`, `2026-09-16-pwa-phase4-ios-checklist.md`) still
apply to everything they cover. This one covers only slice 1; slice 2 (the Room's
home-screen prompt row, the allowed and declined Act rows, and the badge) will add its own
lines. Gate (spec §8): *Alfred reaches the phone when the app is closed*.

**Unrun.** It was written on a headless box, and nothing in it has run on a phone. WebKit
is unmeasured for everything this slice asks of it: whether it lets `subscribe()` run
outside the tap, what it reports after Notifications is turned off in Settings, how a tap
on a notification reaches a closed app, and where the install gate's pointer sits against
Safari's toolbar. Several steps are therefore observations, not assertions. Where a step
says *record*, write down what you saw, whatever it was.

Run against the deployed build at `https://alfred.example.com`. Record the device, iOS
version and date at the bottom.

**Every step can be answered by looking at the phone**, except the rows the house holds:
read those from `GET /api/admin/devices` in a browser signed in to the house (sign in there
again if it answers 401). It shows each row's `platform`, `registered_at`, `push_host` and
the first 12 characters of its field, and never the endpoint. How the pieces fit is
`docs/web-push.md` and `docs/web-frontend.md`, "Web Push".

## Before you start

- [ ] The build deployed is the one under test (`git log -1` on the deploy host)
- [ ] `WEBPUSH_VAPID_SUBJECT=mailto:you@example.com` (your own contact) set in the deploy
      `.env` and the house redeployed (`docs/web-push.md`, "Operations"). Its log shows
      `Generated a VAPID key` on the first start with a subject, then
      `Web Push adapter registered`, and `https://alfred.example.com/api/push/public-key`
      answers with a key
- [ ] The phone is on iOS 16.4 or later, the first iOS that delivers Web Push to a
      home-screen app
- [ ] Any earlier home-screen copy of Alfred deleted, so the install below is a first
      install
- [ ] Another device signed in to Alfred (Safari on a Mac, or Signal), to ask for something
      critical while this phone is locked. A confirmation lives five minutes
      (`PENDING_TTL_SECONDS`); a tap after that opens a Door that has nothing to claim
- [ ] Do Not Disturb off, and no calendar meeting under way. While either holds, the
      dispatcher defers every notification that is not URGENT
      (`core/notifications/dispatcher.py`), and a deferred one reaches neither this phone
      nor the Activity bench
- [ ] The other device can ask Alfred for reminders. A fired reminder with no action is
      announced at the urgency it was created with, informational unless asked otherwise
      (`core/triggers/models.py`; `create_trigger`'s `urgency`, `core/triggers/feature.py`),
      so one reminder makes the IMPORTANT notification below and another the informational
      one
- [ ] Reflex running, not off: its log shows `Reflex Runner started`, not `Reflex is off`.
      Reflex sends a fired trigger's notification, and with no `REFLEX_MODEL` it stays off
      and reads no stream (`stay_off()`, `core/reflex/__main__.py:122-132`), so no trigger
      notifies
- [ ] Optional, for the Reflex ask line: all of these at once. Reflex in `ask` mode, with an
      `ask_tools` pattern that matches the tool (the default `[]` holds every proposal as
      `tool_off`); a tool whose declared risk is benign or elevated; outside Reflex's quiet
      hours (23:00 to 07:00 unless changed); Do Not Disturb off; no ask already open; not the
      same ask within `repeat_hours` (2 h); fewer than the day's cap of 6 asks
      (`daily_ask_cap`; the three are `core/reflex/asks.py:111-118`); and the model proposing
      something (`docs/reflex-anticipation.md`, "Settings" and "Asks"). Switching the live
      house's mode is the owner's call, and in `shadow` mode no ask is ever sent, so the
      line is normally skipped
- [ ] You can stop the house and start it again
- [ ] A second phone on iOS 16.4 or later, for the last section
- [ ] Optional: a Mac with Safari's Web Inspector, for "With Web Inspector"

## In a Safari tab

- [ ] Open `https://alfred.example.com` in Safari, sign in, and open the Workshop's System
      bench. Reach's `Push notifications` row reads
      `unavailable in Safari · add to home screen first`, with `Show me how` on its right
- [ ] `Show me how` opens the install gate: `reaching you · 3 of 6 · in Safari`,
      `Add me to the home screen.`, three steps with `Tap Share, below` current, and a
      pointer at the bottom edge nudging down
- [ ] **Record where the Share pointer sits against Safari's toolbar** (R9 in the plan):
      over the Share button, beside it, or somewhere else, and whether Safari's toolbar was
      expanded or collapsed. The pointer assumes the page's own bottom edge is where
      Safari's chrome begins
- [ ] **Watch the pointer's chevron at the bottom of its nudge.** If the tip is clipped at
      the screen's bottom edge, the follow-up is to raise its `bottom` in
      `web/src/gates/Gate.tsx` to `calc(10px + env(safe-area-inset-bottom, 0px))` and add a
      row to the plan's "Deviations from the handoff" table. Record which case you saw:
      clipped, or whole
- [ ] `Not now` only closes the gate: the Workshop is back, Reach reads as before, and
      nothing else changed

## Install and enable

- [ ] Share → Add to Home Screen, open Alfred from the tile and sign in. System › Reach
      reads `off · not yet asked · iOS asks once`, with `Enable`
- [ ] `Enable` opens the standalone gate: `reaching you · 5 of 6 · opened from the home
      screen`, `You came in the front way.` Step 1 (`Open in Safari`) names this host,
      `alfred.example.com`; step 2 (`Sign in with Face ID`) shows the time you signed in,
      or no time at all if the sessions list had not loaded; step 4
      (`Open from the home screen`) says `now`; step 5 is current
- [ ] `Not yet` only closes the gate: no iOS dialog, and Reach still reads
      `off · not yet asked · iOS asks once`
- [ ] `Enable` again, then `Let me reach you`: the iOS dialog asking whether Alfred may send
      notifications
- [ ] Allow. `Let me reach you` holds while the phone subscribes and registers, the gate
      closes, and Reach reads `on · this iPhone · important and urgent only` with `on` in
      green. `checking · allowed in iOS · registering with the house` shows under the gate
      on this path, so record whether you saw it at all; the row ending on `on` is the
      assertion
- [ ] **This step measures whether iOS lets `subscribe()` run outside the tap.** Enable asks
      the permission inside the tap, then fetches the house's key and subscribes after it.
      If iOS refused that, the gate closes on `checking` while the sync tries again, and
      ends on `on` (the sync's own subscribe was accepted) or on
      `off · allowed in iOS · the house did not take the registration` with `Try again`
      and the error under it. Record which, and the error's words
- [ ] `GET /api/admin/devices` shows exactly one row with `platform` `web_push`, its
      `push_host` `web.push.apple.com`, and no endpoint, `p256dh` or `auth` anywhere in the
      answer. Note its `registered_at`

## Delivery, with the app closed

Close the app from the app switcher and lock the phone for each of these.

- [ ] Ask for something critical on the other device (a confirmation, URGENT): a
      notification titled `Confirmation required` arrives on the lock screen
- [ ] Tap it with the app closed (cold): Alfred opens at `/actions/<id>`, the Room with
      that action's Door raised over it. Deny it there unless you meant it. Record what you
      saw before the Door: the Room alone first, a blank screen, or the Door straight away
- [ ] Ask again, open Alfred, then send it to the background (home screen, not closed) and
      lock the phone. Tap the new notification: the open app raises that action's Door.
      Record both cases
- [ ] On the other device, ask Alfred: "In one minute, remind me to check the oven, and make
      it important". A notification titled `Trigger: ` and the name Alfred gave the
      reminder arrives on the lock screen. The Activity bench's notifications stream shows
      it with `important`; if it shows `informational`, Alfred did not pass the urgency, so
      record it and ask again. This is the IMPORTANT delivery (`Urgency: normal`, kept a day
      by the push service). It is sent before Reflex decides anything about the trigger, so
      in any Reflex mode
- [ ] Tap it: it carries no approval (no `pending_action_id`), so Alfred opens on the Room
      (`/`), with no Door. A tap reaching a Door is the confirmation's to prove, above
- [ ] Optional: a Reflex ask (IMPORTANT) arrives on the lock screen, and a tap opens its
      Door. It needs every precondition in "Before you start"; record what made Reflex
      ask, or record the line skipped
- [ ] Ask for a plain reminder a minute from now, naming no urgency. It does **not**
      arrive. The Activity bench's notifications stream shows it with `informational`,
      which proves it was sent

## Relaunch

- [ ] Close the app and open it from the tile. No Reach gate, no iOS dialog and no tap in
      the app: the launch re-syncs on its own. System › Reach reads `on`, and the row's
      `registered_at` has moved past the one you noted. Still exactly one `web_push` row
      for this phone

## Permission taken away

- [ ] Settings › Alfred › Notifications off. Back to Alfred, close it, open it from the
      tile: Reach reads `off · declined in iOS · this app cannot flip it` with `off` in the
      accent, and the note under it reads `Settings › Alfred › Notifications › Allow. iOS
      holds this switch now; nothing in here can flip it, and Alfred will not raise it
      again.` Record it if iOS reported the permission as not asked
      (`off · not yet asked · iOS asks once`) instead
- [ ] **A failure stays a failure.** Stop the house. Settings › Alfred › Notifications back
      on, then switch to Alfred (no relaunch): the permission's return starts a sync, which
      fails, after some seconds. Reach reads
      `off · allowed in iOS · the house did not take the registration` with `Try again`,
      and the error under it
- [ ] Start the house. Leave System for the Room, then open System › Reach again: it still
      reads `failed`, with `Try again`, no flash of `checking`, and no row's
      `registered_at` has moved. Opening the bench never retries behind the reader's back.
      If the phone lost and regained its network in between, react-query's reconnect
      refetch re-runs the sync, as it should (`docs/web-push.md`, "From the PWA"): redo the
      step
- [ ] `Try again`: `checking`, then `on`, and a row's `registered_at` moves. Record how many
      `web_push` rows this phone has now: a second one means iOS issued a new endpoint, and
      the old row stays until a push to it draws a 404 or 410 and prunes it

## Sign out

- [ ] System › `Sign out on this device`. The device read no longer lists this phone's
      `web_push` row
- [ ] A confirmation asked for on the other device does **not** arrive on this phone. If one
      does, that is the double-failure gap (`docs/web-frontend.md`, "Web Push": both the
      `DELETE` and the browser's unsubscribe failed); record it
- [ ] Sign in again on this phone. Sign-out unsubscribed the browser, so the sign-in's
      sync subscribes again outside any Reach tap, this time after a passkey ceremony: the
      same unmeasured question as "Install and enable". **Record** whether the row came
      back with no Reach tap and no iOS dialog, and what Reach reads: `on`, or `failed`
      with its error's words

## A second phone

- [ ] Install and enable on the second phone, as in "Install and enable". The device read
      shows two `web_push` rows, each `web.push.apple.com`
- [ ] A confirmation arrives on both phones, and a tap on either opens the same Door. The
      first decision taken wins; the other phone's Door then has nothing to claim

## With Web Inspector (optional)

On a Mac with Safari's Develop menu, inspect the home-screen app.

- [ ] `Notification.permission` reads `granted` while Reach reads `on`, and `denied` after
      Notifications was turned off (or record what it read)
- [ ] Whether a subscription exists, as a yes or no:
      `!!(await (await navigator.serviceWorker.getRegistration()).pushManager.getSubscription())`
      is `true` while Reach reads `on`, and `false` after sign-out. Its host only, while one
      exists:
      `new URL((await (await navigator.serviceWorker.getRegistration()).pushManager.getSubscription()).endpoint).host`
      reads `web.push.apple.com`. **Never print, paste or copy the endpoint itself**: it is
      a credential, and anyone holding it can push to this phone
- [ ] Nothing from the worker on the console when a push arrives or a notification is
      tapped

## Nothing lies

- [ ] The Room shows no home-screen prompt row, no allowed or declined Act row, and the tile
      carries no badge: those are slice 2's. Reach's `on` line names a badge that slice 2
      makes true; it is the handoff's copy, and known
- [ ] Reach never read `on` before the house held the row: every `on` above had its
      `web_push` row in the device read
- [ ] Nothing arrived for an informational notification, and nothing arrived on this phone
      while it was signed out

---

Device: ______________  iOS: ______  Build: ______________  Date: ____________
Tester: ______________
