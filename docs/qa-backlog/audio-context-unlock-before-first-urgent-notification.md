# URGENT Notification Audio Before the First Gesture

**Feature:** Autoplay-policy handling for spoken replies and URGENT notification audio
(`web/src/lib/audio.ts`, unlocked from `web/src/main.tsx`)
**Priority:** high
**Type:** functional

## Prerequisites
- Alfred server running (`uv run python -m runner`)
- Fresh browser tab (no prior user interaction) opened at `http://localhost:8081`, signed in

## Test Steps
1. Open `http://localhost:8081` in a new tab — do NOT click, tap or press any key
2. Immediately (before any user interaction) trigger an URGENT notification via the dispatch stream
3. Observe whether audio plays
4. Now tap anywhere on the page — the `pointerdown` listener `installAudioUnlock()` registered
   resumes the shared context and starts a zero-length buffer from inside the gesture
5. Trigger a second URGENT notification
6. Observe whether audio plays this time
7. Reload, tap once anywhere, then send a text message and wait for Alfred's spoken reply

## Expected Result
- Step 3: audio does NOT play — the context has never been resumed by a gesture, so
  `playWavBase64()` decodes into a suspended context and nothing is heard. The notification
  title/body still appear in the Room; text delivery is unaffected
- Step 3: no unhandled rejection reaches the console — `decodeAudioData` and `resume()`
  failures are caught deliberately
- Step 6: audio plays for the second URGENT notification
- Step 7: **the first spoken reply after a launch is audible**, which is the whole point of
  unlocking in `main.tsx` rather than at first playback — this is the regression the outgoing
  client had (`new Audio()` per reply, dropped in silence until something else had played)
- In no case does the page crash or lose further functionality

## Notes
- `lib/audio.ts` keeps ONE `AudioContext` for the app's life (`getAudioContext()`); every
  reply and notification plays through it. There is no `new Audio()` anywhere in the client
- `installAudioUnlock()` listens for `pointerdown` and `keydown`, once, and uninstalls itself
  after the first one. Resuming alone is not enough on iOS: a source has to actually start
  from inside the gesture, which is what the zero-length buffer is for
- `playWavBase64()` re-checks `ctx.state !== "running"` on every play. iOS suspends the
  context when the app is backgrounded, and WebKit also has an `"interrupted"` state (a phone
  call or Siri took the audio session) that the `AudioContextState` union does not name
- On iOS Safari the restriction is stricter than on desktop; test on the phone separately —
  the manual pass is `docs/superpowers/qa/2026-09-07-pwa-phase1-ios-checklist.md` §4.7
