# URGENT Notification Text Delivered When TTS Synthesis Fails

**Feature:** TTS failure resilience in WebSocket adapter (D28)
**Priority:** high
**Type:** functional

## Prerequisites
- Alfred server running (`uv run python -m runner`)
- Browser open at `http://localhost:8081`
- Ability to simulate or trigger a TTS failure (e.g. corrupt the Piper model file, or temporarily restrict write access to the model cache directory to force a load error)

## Test Steps
1. Simulate a TTS failure condition — one approach: rename or delete the Piper model file (`.onnx`) in the HuggingFace cache so synthesis throws an exception on next call
2. Trigger an URGENT notification via the dispatch stream
3. Observe the browser tab and devtools WebSocket messages
4. Observe the server logs for a warning

## Expected Result
- A `notification` message is still received over WebSocket (text delivery is NOT blocked by TTS failure)
- The notification title and body are displayed in the UI
- No `audio` field is present in the payload (graceful omission, not crash)
- Server logs show a `WARNING` line containing "TTS synthesis failed" with the error details
- The Alfred server process does NOT crash or restart

## Notes
- The server should log at WARNING level, not ERROR — a TTS failure is recoverable
- After restoring the model file, the next URGENT notification should resume playing audio without restarting the server
- This tests that `asyncio.to_thread` exceptions are properly caught and that the except block in `websocket.py` does not re-raise
