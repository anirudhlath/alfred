# PWA phase 1 — manual iOS checklist

Spec §7: "Every one of the twelve iOS constraints in §4 gets a test or a
documented manual QA step." The automated half is in `web/src`; this is the half
that needs a real iPhone, Safari, and a house that is actually running.

Run against the deployed build at `https://alfred.example.com` on a phone that has
never signed in. Record the device, iOS version and date at the bottom.

## Before you start

- [ ] `npm run build` output is what is deployed (`git log -1` on the deploy host)
- [ ] The phone is on the house network for registration (§3.1 gates it)
- [ ] A pending critical action can be produced on demand (ask Alfred to unlock
      something, or publish one by hand)

## §4.1 — secure context

- [ ] The address bar shows a padlock; no mixed-content warning in the console
- [ ] Face ID registration is offered at all (WebAuthn is refused outside HTTPS)

## §4.2 — home-screen icon

- [ ] **Deferred to phase 4.** `manifest.json` still ships SVG only, and no
      `apple-touch-icon` PNG exists yet. Expect a screenshot-style icon if you
      add it to the home screen; that is not a phase 1 defect.

## §4.3 — notch, island and home indicator

- [ ] The headline sits below the Dynamic Island, never under it
- [ ] The composer sits above the home indicator with a clear gap
- [ ] Rotate to landscape: nothing is under the rounded corners

## §4.4 — 100vh is wrong

- [ ] Scroll the timeline down and back: the composer never leaves the screen
- [ ] Scroll a long thread: the headline and status line stay put at the top and the
      composer at the bottom; only the timeline moves
- [ ] Send a message with the thread scrolled to the bottom: the reply scrolls into view
      on its own
- [ ] With Safari's toolbar collapsed, the Room still fills the viewport exactly
- [ ] Rotate twice quickly: no strip of background appears at the bottom

## §4.5 — the software keyboard

- [ ] Tap the field: the composer rises with the keyboard, it is not covered
- [ ] The timeline is still scrollable with the keyboard up
- [ ] Dismiss the keyboard: the composer returns without a jump
- [ ] The home-indicator gap does **not** double up while the keyboard is open
- [ ] Tap the composer field, then a setup-gate input, in a Safari **tab** and again from the installed icon: the page must not zoom. Both use the handoff's 15px type, and Safari zooms into a focused field under 16px. If it does, put the inputs at 16px — not a `maximum-scale` viewport, which takes pinch zoom from everyone

## §4.6 — rubber-band scroll

- [ ] Drag down on the header: the page does not bounce as a whole
- [ ] Drag past the top of the timeline: only the timeline bounces

## §4.7 — audio needs a gesture

- [ ] Cold-launch the app, tap once anywhere, then send a message: the spoken
      reply is **audible on the first reply**, not only the second
- [ ] Background the app for a minute, return, trigger an urgent notification:
      the audio still plays

## §4.8 — Safari's recorder

- [ ] Hold the microphone, speak, release: the dashed bubble appears and is
      replaced by the transcript
- [ ] The console shows no `NotSupportedError` from `MediaRecorder`
- [ ] Release inside a second: nothing is sent and the caption clears

## §4.9 / §4.9b — Web Push and install

- [ ] **Deferred to phase 5.** There is no Reach gate, no permission prompt and
      no service worker in phase 1. Confirm only that nothing *offers* them.

## §4.10 — iOS kills suspended PWAs

- [ ] Send a message, background the app for five minutes (inside the session idle
      timeout, 30 min by default), return: the thread is intact and the status line's
      clock has moved — past the timeout the thread is *meant* to be gone, see "The
      Room's window"
- [ ] While backgrounded, have the house produce an act (a reflex action): it is
      present after returning, without a manual refresh — an act from **today** only;
      the Room keeps the house's rows for the day, not for ever
- [ ] When a routine suggestion arrives, the row reads as the suggestion itself, not the
      bare label "Routine Suggestion"
- [ ] Turn off Wi-Fi and mobile data: the headline reads `Reconnecting…` first and
      `Unreachable.` within ~4 s (three failed tries), or at once when iOS reports
      the device offline; the offline note carries a real `last true HH:MM`, and a
      sent message shows `not sent · will retry when connected`
- [ ] Turn the network back on: the queued message sends itself, in order

## §4.11 — no haptics

- [ ] The slide-to-confirm gives no vibration and does not need one: the knob
      resists at the start and only confirms past ~85% of the track

## §4.12 — no browser chrome

- [ ] The Held-back sheet closes on both `Done` and the scrim
- [ ] The Door closes on `‹ Leave it`, and on `Back to the room` once answered
- [ ] The Expired gate can be dismissed by signing in; the Denied gate by
      `Back to the room`
- [ ] No screen is reachable that has no way out

## The Door, end to end

- [ ] Produce a pending action: the banner appears above the composer with a
      counting fuse
- [ ] Open it: the ring, the reason and the raw call all match what was asked
- [ ] Slide half-way and release: the knob snaps home and nothing is confirmed
- [ ] Slide fully: the pill reads `Confirmed · queued`, then `Applied` once the
      house reports — **not** immediately
- [ ] Let a second one lapse: the pill reads `Expired`, and the Room carries a
      struck-through row reading `expired HH:MM · not done · asked HH:MM`
- [ ] Under 30 s the ring's arc changes colour; nothing pulses

## Reduce motion

- [ ] Settings › Accessibility › Motion › Reduce Motion on: the presence field is
      static, gates and the Door cross-fade in about 200 ms instead of rising

## Themes

- [ ] The toggle switches instantly and survives a relaunch
- [ ] Safari's chrome colour matches the theme in both

## The Room's window

- [ ] Open the app after more than the session idle timeout away (30 min unless the
      house's `session.idle_minutes` says otherwise): the conversation is gone, and the
      first message starts a new session — Alfred does not refer back to it
- [ ] Today's notifications and reflex acts are still there, under `earlier today`
- [ ] On a house with no notification and no reflex act today, after a break: the
      Timeline is **completely blank, and that is correct** — no conversation left, no
      house rows, and no first-day greeting on an established house. Not a failed
      history read
- [ ] Reopen within the timeout: the conversation is still there and continues
- [ ] Leave the app open and idle past the timeout, then send: a
      `new conversation · HH:MM` divider separates the two and the older turns stay on
      screen — the window moves on a background-and-return, not at minute thirty

---

Device: ______________  iOS: ______  Build: ______________  Date: ____________
Tester: ______________
