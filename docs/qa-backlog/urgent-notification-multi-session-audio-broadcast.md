# URGENT Notification Audio Delivered to All Active Browser Sessions

**Feature:** WebSocket adapter multi-session broadcast with TTS (D28)
**Priority:** medium
**Type:** integration

## Prerequisites
- Alfred server running (`uv run python -m runner`)
- Two or more browser tabs (or separate browsers) open at `http://localhost:8081` and connected
- User has interacted with each tab to unlock AudioContext

## Test Steps
1. Open two browser tabs to `http://localhost:8081` — verify both show "Connected" status
2. Interact with each tab (click) to unlock AudioContext in both
3. Trigger one URGENT notification via the dispatch stream
4. Observe both browser tabs simultaneously

## Expected Result
- Both tabs receive exactly one `notification` WebSocket message (the same payload is broadcast to all sessions)
- Both tabs display the notification title and body
- Both tabs play the audio (the same base64 WAV audio embedded in the single payload)
- TTS is synthesized only once on the server (not once per connected session) — verify in server logs that only one TTS synthesis log entry appears

## Notes
- The WebSocket adapter iterates `sessions` and sends the same pre-built payload dict to each — TTS synthesis happens before the loop, so cost is O(1) regardless of session count
- If TTS were synthesized per session, it would be a performance regression and would show as N synthesis log entries for N sessions
- This test also verifies there is no session-level deduplication that would prevent some sessions from receiving audio
