# D11: Streaming TTS to WebSocket

## Summary
TTS sends full audio blob only. No chunk streaming for lower latency.

## Context
Streaming TTS chunks to the frontend would reduce time-to-first-audio.

## Acceptance Criteria
- TTS chunks streamed as they're generated
- Frontend plays chunks incrementally
- Fallback to full-blob mode if streaming fails
