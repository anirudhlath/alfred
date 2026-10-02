# PWA phase 4 — manual iOS checklist

Spec §7's manual half, for what phase 4 added: the install, a launch with no network or
no answer, the update, and the kill switch. The phase-1 checklist
(`2026-09-07-pwa-phase1-ios-checklist.md`) still applies to the Room and the Door,
`2026-09-10-pwa-phase2-ios-checklist.md` to the Workshop shell and the Activity bench,
and `2026-09-16-pwa-phase3-ios-checklist.md` to the other three benches. This one covers
only phase 4. Gate (spec §8): *installs and behaves as native*.

**Unrun.** It was written on a headless box, and nothing in it has run on a phone. WebKit
is unmeasured for everything the worker does — the deadlines on a slow body, any install
time limit, the first claim, the kill switch — so several steps are observations, not
assertions. Where a step says *record*, write down what you saw, whatever it was.

Run against the deployed build at `https://alfred.example.com`. Record the device, iOS
version and date at the bottom.

**Every step down to the kill switch can be answered by looking at the phone**, as in
phase 3. What the worker holds — its registration and its cache — cannot be seen on the
phone at all, so the steps infer it from what an offline launch does. Two things need a
Mac, and both are optional: the throttled install, because Settings › Developer appears
only on a phone paired with Xcode, and "With Web Inspector", at the end, which settles
what the inference cannot.

The phase-3 checklist's two measured facts (the viewport readings and the safe-area
inset in standalone mode) still hold, and nothing here re-measures them.

## Before you start

- [ ] The build deployed is the one under test (`git log -1` on the deploy host)
- [ ] Two more changes ready to ship during the run, each through its own pull request:
      **A**, an ordinary change you can see on the Room and on the offline gate alike,
      with `web/public/sw.js` untouched: the frame "An ordinary deploy (A)" spells out,
      one rule added at the end of `web/src/index.css`. Not a colour token:
      `contrast.test.ts` holds the tokens to their restatement in
      `web/src/test/contrast.ts`, `icons.test.ts` pins `--bg` and `--accent` to the icon,
      and `manifest.test.ts` pins `--bg` to the manifests, so a token edit fails `ci-ok`
      unless those move in the same pull request. And **B**, one byte of `web/public/sw.js`
      changed — a comment is enough. B installs a new worker on every phone that has the
      app, which is the point of it. Two later steps each need one more byte change of the
      same kind
- [ ] Any earlier home-screen copy of Alfred deleted, so the install below is a first
      install
- [ ] You can stop the house and start it again
- [ ] Airplane mode, and a Wi-Fi network that reaches nothing: a second phone's hotspot
      with that phone's mobile data off. On it, this phone's own mobile data must be off
      too, or iOS sends the traffic over mobile data instead, the house answers, and the
      step fails for the wrong reason
- [ ] Optional, for the throttled install: Settings › Developer › Network Link
      Conditioner. The Developer menu appears only once the phone has been paired with
      Xcode on a Mac
- [ ] Optional: a Mac with Safari's Web Inspector, for the last section

## Install

- [ ] Safari → Share → Add to Home Screen. The tile shows the accent `A` on the dark
      ground, with **no black corners** and no page screenshot. The name under it reads
      `Alfred`
- [ ] Open it from the tile and **watch the first ten seconds**. The page paints once and
      stays. A blank-and-repaint a few seconds in is the page reloading when the first
      worker claims it, which is a defect. Chromium fires `controllerchange` once there and
      the app ignores that one; if Safari fires it twice, the second would reload. Record
      what you saw either way
- [ ] **A throttled install** (optional: it needs the Developer menu). Delete the app and
      set Network Link Conditioner to "3G" (or "Very Bad Network"). Add the app again and
      open it, and leave it on the Room for two minutes. Then Airplane mode, kill the app
      and open it from the tile: it **paints**. Record whether it did, then set the
      conditioner back. This is the only measurement of WebKit under the worker's
      per-read deadline and any install time limit

## The launch screen — an observation, not an assertion

Nothing here can fail. Why the manifests may have no part in what iOS shows is
`docs/web-frontend.md`, "The manifest pair"; this section is the measurement
`docs/backlog/low/pwa-phase4-followups.md` §6 is waiting for.

- [ ] Install during daylight with the light theme active, kill the app, open it from the
      tile, and **record what you see** before the app paints: the colour of the screen,
      whether an icon appears on it, whether it flashes white
- [ ] Switch to dark, delete and reinstall, and record it again. Two identical answers mean
      the manifest swap is inert on iOS, which is the expected result and **not a defect to
      fix on the spot**

## Standalone

- [ ] No Safari chrome and no address bar. The status bar is translucent over the app's
      own background, and the composer sits above the home indicator

## A cold launch with no network

- [ ] Airplane mode, kill the app, open it from the tile. The app **paints** — real type,
      real palette, the presence field — and shows `offline · nothing read yet` and
      `No way to the house.`, with no button
- [ ] That step depends on iOS reporting `navigator.onLine` false at a cold launch in
      standalone mode. If you saw the bare presence field for a while and then
      `The house is not answering.`, iOS reported online. Record which you saw — either way
      it is the finding
- [ ] Turn Airplane mode off with the app still open. The gate clears itself with no tap:
      the field for a moment, then the Room — or the sign-in gate, if the session has
      lapsed. On the Room, the `No connection to the house …` note goes within a second or
      so, because both sockets reconnect on the same `online` event; in Chromium it went in
      well under a second, where it used to wait out up to 8 s of backoff. Record how long
      it stayed
- [ ] **A deep link, offline.** iOS opens no link into a home-screen app, and a Safari tab
      has a worker of its own, so this runs in Safari. Open `https://alfred.example.com` in
      a Safari tab once, online, then wait a minute, so the tab's worker finishes copying
      the app. Then Airplane mode, and open `https://alfred.example.com/actions/<any id>`
      in a new tab. The same shell loads with the offline gate, and the Door does not
      open, because nothing has been read

## Online, and the house does not answer

Every answer the status read can get, and the gate each one shows, is mapped in
`docs/web-frontend.md` ("The gates"). These steps cover the ones a phone can produce by
itself; a proxy's 4xx or 5xx to the read is mapped there and pinned in
`web/src/gates/AuthGate.test.tsx`, not here.

- [ ] Turn this phone's mobile data off and join the dead hotspot, then kill the app and
      open it. You see the presence field and then `The house is not answering.`, with
      `Try again`. It may take a second or so on a network that refuses at once, and about
      twenty seconds on one that hangs. **Never** the sign-in gate's `signed out`, and
      never `No way to the house.`
- [ ] Tap `Try again` with the hotspot still dead. The button dims while it asks and the
      gate stays up; it never drops back to the bare field
- [ ] Switch away and back: it asks again by itself, and the button dims for a moment
- [ ] Rejoin a network that reaches the house, turn mobile data back on, and tap
      `Try again`. The gate gives way to the Room, or to the sign-in gate if the session
      has lapsed
- [ ] **The house stopped, behind the reverse proxy.** Stop the house, kill the app, and
      open it. Record what appears. The predicted result is the proxy's own error page,
      because the network answered and the app never boots, so the unanswered gate cannot
      rise (`docs/backlog/low/pwa-phase4-followups.md` §8). Leave the house stopped for
      the next section

## The API is never served from cache

- [ ] Start the house, open the app online, and let the Room and the System bench paint.
      Then stop the house with the app open. The Room says
      `No connection to the house since HH:MM.` and the System bench says
      `unknown since HH:MM` — never a stale `alive`, never a stale spend figure, never a
      stale trigger state
- [ ] With the house still stopped, switch away and back. The update check fails, which
      shows nothing on screen: an online phone that cannot reach the house is expected to
      do that, and it is not a fault. Then start the house

## An ordinary deploy (A)

A is one rule, added at the end of `web/src/index.css`. It draws a 4px frame in the
accent colour along the four edges of whatever `#root` holds, over everything in it, and
lets every tap through:

```css
#root::after {
  content: "";
  position: absolute;
  inset: 0;
  border: 4px solid var(--accent);
  pointer-events: none;
  z-index: 2147483647;
}
```

`#root` is already `position: fixed` in that file, and that makes it the frame's
containing block: the frame sits on `#root`'s edges, not the viewport's, and nothing needs
`position: relative`, which on `#root` would undo the `fixed`. The Workshop, the Door and
the gates that rise over a running app are drawn into `<body>` over `#root` and hide the
frame, and a sheet's scrim only dims it, so the steps look for it only on the screen a
launch comes up on: the Room, or the gate shown in its place. In Chromium it shows on all
four edges of the Room, the offline gate and the unanswered gate, it frames the sign-in
gate too, and taps still reach `Try again` and the Room's buttons; nobody has looked at
it on a phone yet.

- [ ] With the app open on the Room, ship A. Switch away and back. **No** note appears:
      `sw.js` did not change, so no worker is waiting. Expected, not a defect
- [ ] Kill the app and open it online. **Pass:** the frame runs round the edges of the
      Room, or of the sign-in gate if the session has lapsed. **Fail:** no frame
- [ ] Airplane mode, kill the app, open it. **Pass:** the gate "A cold launch with no
      network" showed — the offline gate, or the unanswered gate if iOS reported online —
      inside the frame, because the online launch above copied A for offline use; or, if
      that launch could not copy all of A, that gate as it was before A, whole, with no
      frame. Record which you saw. **Fail:** anything else — iOS's own offline page, a
      blank screen, or a gate without its own type and palette
- [ ] Revert A, through a pull request of its own, and let it deploy. Kill the app and
      open it online. **Pass:** the frame is gone. **Fail:** it is still there

## A changed `sw.js` (B)

- [ ] With the app open on the Room, ship B. Switch away and back. Within a few seconds the
      Room shows `A newer Alfred is waiting. Nothing is lost; I pick up where we left off.`
      and `Reload`
- [ ] Open the Workshop: the note is not there, because it lives in the Room's header
      (`docs/backlog/low/pwa-phase4-followups.md` §3). Back in the Room it is still there
- [ ] Type a few words in the composer and do not send them. Tap `Reload`. The page
      reloads **once** and does not loop, the note is gone, and the words are still in the
      field
- [ ] **With the app killed.** Kill the app, then ship another byte change to `sw.js` —
      a word added to a comment, so the last section can find it. With a Mac, read that
      section's item for this launch before you open the app. Wait until any browser shows
      the word in `https://alfred.example.com/sw.js`: until then the house may still serve
      the old one, and no verdict in this step means anything. Then open the app from the
      tile. It must come up on the Room, signed in, with no `No connection` note (sign in
      first if a gate shows), because the Room is the only place the note appears; then
      give it a minute. The note now is a **pass**: the new worker is waiting. With no
      note, switch away and back and give it a minute more. A note on that switch is a
      **fail**: the launch ran on the old worker and never noticed the new one, which is
      the iOS failure this step exists to catch. No note on that switch either is a
      **pass** on the phone's evidence: the new worker took over at launch. A new worker
      left waiting with no note looks the same here, and only the last section's reading
      tells the two apart

## The kill switch

The procedure is `docs/web-frontend.md`, "The kill switch", step for step.

- [ ] Fire it: `cp web/public/sw.kill.js web/public/sw.js`, committed alone, through a
      pull request. `ci-ok` passes, and the run carries a warning from each of the two
      stand-down tests. Merge it, which deploys it
- [ ] With the app open, switch away and back. The page reloads **once** and then stays
      quiet: no second reload and no loop, on this switch or any later one
- [ ] Airplane mode, kill the app, open it from the tile. The app **does not paint** —
      iOS's own offline page instead — because no worker and no cache are left. Record
      what you saw. A shell that still paints means the switch did not take. The predicted
      worst case is the switch still installed with no fetch handler and no caches, which
      looks the same as success here; the last section tells them apart
- [ ] Online again, open the app: it works as a plain web page — the Room, signed in, or
      the sign-in gate if the session has lapsed
- [ ] **Revert the firing commit**, through a pull request of its own, and let it deploy.
      Kill the app, open it online, and leave it on the Room for a minute. Then Airplane
      mode, kill it, and open it: it **paints again**. That proves the restored worker
      registered. Do not skip this step: it is the one that proves the switch is
      reversible

## The keyboard, again

- [ ] Open the composer on the installed app. The shell must not move and the composer
      must not clip — the phase-2 bug, re-checked because the page now boots from the
      worker's copy of the shell

## VoiceOver

- [ ] With VoiceOver on and the app open on the Room, ship one more byte change to
      `sw.js` and switch away and back (or run B's first step this way). The note is
      announced once, and the Room's offline note is not announced with it
- [ ] On the offline gate and the unanswered gate: the title and the body are read in order,
      and on the unanswered gate `Try again` is announced as a button, and as dimmed while
      it asks

## With Web Inspector (optional)

Phase 3's checklist keeps to what the phone shows. This section breaks that rule on
purpose, because a worker's registration and its cache are invisible on the phone.
Turn on Web Inspector on the phone, under Settings › Apps › Safari › Advanced (Settings ›
Safari › Advanced before iOS 18), and on the Mac, Safari › Settings › Advanced › Show
features for web developers. Connect the phone, then Safari › Develop › the phone › the
Alfred home-screen app.

Safari shows neither a worker's state nor Cache Storage, so read both in that app's
Console. Paste each read whole. The first logs the registration's scope and the states of
its active, waiting and installing workers, `undefined` for each one it lacks, or a lone
`undefined` if there is no registration; the second logs each cache with the paths it
holds:

```js
navigator.serviceWorker.getRegistration()
  .then((r) => r && [r.scope, r.active?.state, r.waiting?.state, r.installing?.state])
  .then(console.log)
```

```js
caches.keys()
  .then((ks) => Promise.all(ks.map(async (k) => [k,
    (await (await caches.open(k)).keys()).map((q) => new URL(q.url).pathname)])))
  .then(console.log)
```

A worker is listed under Develop › the phone, beside the app, only while it runs, as it
does right after a launch; its inspector has a Console, Sources and Network. Everything
this section says about Safari is documented, not measured (webkit.org, "Enabling Web
Inspector"; Apple's iPhone User Guide, "Customize your Safari settings on iPhone", for the
Settings › Apps › Safari path; web.dev's PWA course, "Tools and debug"): none of it has
been tried on an iPhone, the two reads included. Where a step below cannot be read as
written, record what Safari offered instead.

- [ ] After the install: the registration read gives `https://alfred.example.com/`,
      `activated`, `undefined`, `undefined`. The cache read gives one cache,
      `alfred-shell-<VERSION>` (`VERSION` at the top of `sw.js`), holding `/index.html`,
      the entry chunk and the stylesheet under `/assets/`, eight `.woff2` files and
      `/__shell-manifest`
- [ ] After B, before `Reload`: `activated`, then `installed` — the old worker active and
      the new one waiting
- [ ] The killed-app launch in B. As the app opens, open each worker listed under
      Develop › the phone (two if the new one is already installing) and leave the
      inspectors open. A minute later, before switching away, read the registration. A
      worker waiting (`installed`, third) is a **pass** with the note showing and a
      **fail** without it. A worker still installing (`installing`, last): wait, then read
      again. Nothing waiting or installing: the active worker is the one still listed, and
      its source settles it; if none is listed, record that. The word that step added is
      a **pass**: the new worker took over at launch. No word is a **fail**: the old worker
      is active with nothing waiting
- [ ] After the kill switch, a few seconds after opening the app online and before the
      revert: the registration read gives `undefined` and the cache read `[]` — no
      registration and no cache
- [ ] After the revert and one online launch: `activated` again with nothing waiting or
      installing, and the cache read as it was after the install
- [ ] Console noise that is expected, not a fault: with the phone online and the house
      unreachable, one `service worker update check failed` warning per return to the
      foreground; and after the kill switch, the `InvalidStateError` warnings under the
      same message that `docs/web-frontend.md` describes ("The kill switch", What it
      leaves behind)
- [ ] Nothing else from the worker on the console

## Nothing lies

- [ ] There is no Reach card, no `install` gate, no `standalone` gate, no home-screen
      prompt row and no notification permission prompt anywhere. That is phase 5, and an
      inert one would be the defect the phase-2 placeholder tabs were
- [ ] No launch above showed `signed out` before the house had answered: with no network
      or no answer, the sign-in gate never appeared
- [ ] The update note appeared only when a changed `sw.js` was waiting, never after an
      ordinary deploy
- [ ] A cold launch with no network showed no row, no vital and no stamp at all — only the
      offline gate (or the unanswered gate, where iOS reported online). Nothing the house
      said is ever kept by the worker

---

Device: ______________  iOS: ______  Build: ______________  Date: ____________
Tester: ______________
