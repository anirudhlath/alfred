# D23: Frontend Audio Queue

## Summary
Response TTS and notification TTS play simultaneously. Need a sequential audio queue.

## Context
Notifications should wait for current playback to finish before playing their own TTS.

## Acceptance Criteria
- Sequential audio queue in frontend
- Notifications queued behind active playback
- Queue drains in FIFO order
