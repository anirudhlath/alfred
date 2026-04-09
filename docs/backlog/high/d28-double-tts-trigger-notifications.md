# D28: Investigate Double TTS on Trigger Notifications

## Summary
TTS plays twice on trigger fire despite D26 fix confirming single delivery.

## Context
May be WebSocket adapter + Voice adapter both producing audible output, or frontend replaying. Needs debugging with browser dev tools to identify which messages arrive.

## Acceptance Criteria
- Root cause identified
- TTS plays exactly once per notification
- Regression test added
