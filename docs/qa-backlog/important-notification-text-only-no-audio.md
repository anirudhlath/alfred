# IMPORTANT Notification Delivers Text Only — No Audio

**Feature:** WebSocket adapter urgency-based TTS gating (D28)
**Priority:** high
**Type:** functional

## Prerequisites
- Alfred server running (`uv run python -m runner`)
- Browser open at `http://localhost:8081`

## Test Steps
1. Open browser devtools → Network tab (filter to WS) and Console tab
2. Trigger an IMPORTANT-urgency notification (not URGENT) via the dispatch stream or conscious engine
3. Observe the WebSocket message payload in devtools
4. Watch and listen in the browser tab

## Expected Result
- A `notification` message is received over WebSocket
- The payload does NOT contain an `audio` field
- The notification title and body are displayed in the UI
- No audio plays at all — silence is the expected behavior for IMPORTANT urgency
- The TTS engine is not invoked (no model warmup delay for the first IMPORTANT notification after startup)

## Notes
- IMPORTANT notifications are lower-urgency attention items (e.g. reminders, non-critical alerts)
- URGENT notifications are for time-sensitive, safety-relevant situations requiring immediate audio attention
- If audio does play for an IMPORTANT notification, the urgency gating in `websocket.py` has regressed
