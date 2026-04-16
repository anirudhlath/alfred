# AudioContext Unlock Required Before First Notification Audio Plays

**Feature:** Browser AudioContext autoplay policy handling (D28 / web/app.js)
**Priority:** high
**Type:** functional

## Prerequisites
- Alfred server running (`uv run python -m runner`)
- Fresh browser tab (no prior user interaction) opened at `http://localhost:8081`

## Test Steps
1. Open `http://localhost:8081` in a new browser tab — do NOT click or press any key
2. Immediately (before any user interaction) trigger an URGENT notification via the dispatch stream
3. Observe whether audio plays
4. Now click anywhere on the page (this unlocks AudioContext)
5. Trigger a second URGENT notification
6. Observe whether audio plays this time

## Expected Result
- Step 3: Audio does NOT play (browser autoplay policy blocks it); the notification title/body still appears in the UI — text delivery is unaffected
- Step 3: Browser console may show a warning about AudioContext suspended state or failed decoding
- Step 5 (after unlock): Audio plays correctly for the second URGENT notification
- In neither case does the page crash or show a JS error that breaks further functionality

## Notes
- `app.js` uses `{ once: true }` event listeners on `click` and `keydown` to unlock AudioContext on first user interaction
- This is a browser security constraint (autoplay policy) — cannot be bypassed without a user gesture
- On iOS (Safari), the AudioContext unlock behavior may differ — test on the iOS PWA separately if applicable
- If the user never interacts with the page before receiving a notification, audio silently fails but text is still shown; this is acceptable behavior
