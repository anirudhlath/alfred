# Voice Round-Trip Responsiveness with Parallel Requests

**Feature:** WebSocket channels — STT/TTS off event loop
**Priority:** high
**Type:** regression

## Prerequisites
- Alfred channels service running (web server on localhost:8081)
- Whisper STT + Piper TTS models loaded (or waiting for warmup to complete)
- WebSocket client capable of sending multiple concurrent audio frames (e.g., `wscat` or custom test script)
- WAV or WebM audio samples (can be short speech, 1-2 seconds)

## Test Steps
1. Open two concurrent WebSocket connections to `ws://localhost:8081/ws`
2. Send audio (content_type: "audio") on first connection; do NOT wait for response
3. Immediately send audio on second connection while first is transcribing
4. Wait for both responses
5. Measure response times and verify both complete without timeout or error
6. Repeat with a longer transcription (10+ seconds of speech) on first connection, audio on second during middle of transcription

## Expected Result
- Both WebSocket connections handle transcription in parallel without blocking each other
- Response times are similar (~3-8s per request depending on audio length and model)
- No "timeout" or "Event loop is blocked" errors in logs
- Both transcriptions produce accurate text results
- TTS synthesis (if enabled) runs off-loop and doesn't block other requests

## Notes
- Before this fix: blocking `stt.transcribe()` and `tts.synthesize()` calls ran on the event loop, causing one slow request to block the entire WebSocket handler for all clients
- The async versions (`_transcribe_async`, `_synthesize_async`) use `asyncio.to_thread()` to offload to worker threads
- A lock (`_voice_load_lock`) prevents two concurrent requests from triggering model construction twice if models haven't loaded yet
- If first request takes 30s to transcribe and second request arrives at 5s, second should not wait for first to finish before starting
