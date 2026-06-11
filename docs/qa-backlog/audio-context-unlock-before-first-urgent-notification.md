# URGENT Notification Audio Blocked Before First User Interaction

**Feature:** Browser autoplay policy handling for URGENT notification audio (web/src/chat/use-chat.ts)
**Priority:** high
**Type:** functional

## Prerequisites
- Alfred server running (`uv run python -m runner`)
- Fresh browser tab (no prior user interaction) opened at `http://localhost:8081`

## Test Steps
1. Open `http://localhost:8081` in a new browser tab — do NOT click or press any key
2. Immediately (before any user interaction) trigger an URGENT notification via the dispatch stream
3. Observe whether audio plays
4. Now click anywhere on the page (this satisfies the browser's autoplay policy requirement)
5. Trigger a second URGENT notification
6. Observe whether audio plays this time

## Expected Result
- Step 3: Audio does NOT play (browser autoplay policy blocks `new Audio().play()` before any user gesture); the notification title/body still appears in the UI — text delivery is unaffected
- Step 3: Browser console may show an `AbortError` or `NotAllowedError` from the rejected `.play()` promise (silently caught by `.catch(() => {})`)
- Step 5 (after user gesture): Audio plays correctly for the second URGENT notification
- In neither case does the page crash or show a JS error that breaks further functionality

## Notes
- `use-chat.ts` calls `new Audio(...).play().catch(() => {})` per notification — no shared AudioContext; each URGENT notification creates a fresh `Audio` object
- The `.catch(() => {})` handler intentionally swallows the `NotAllowedError` — audio failure is silent (text still renders)
- This is a browser security constraint (autoplay policy) — cannot be bypassed without a prior user gesture
- On iOS Safari, the restriction is stricter — audio may be blocked even after some gestures unless the tap directly triggered playback; test on iOS PWA separately
- If the user never interacts with the page before receiving a notification, audio silently fails but text is still shown; this is acceptable behavior
