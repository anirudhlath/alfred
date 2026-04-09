# P2: Notification Dispatcher as Sub-Agent

## Summary
Replace hardcoded routing rules with LLM-powered sub-agent for notification dispatch.

## Context
Would allow natural-language routing policies and learning from user feedback. Adds latency + inference cost.

## Acceptance Criteria
- LLM reasons about context, urgency, channel selection, DND
- User feedback loop for routing quality
- Configurable fallback to static routing
