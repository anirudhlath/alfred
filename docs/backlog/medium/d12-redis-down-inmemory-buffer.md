# D12: Redis-Down In-Memory Buffer

## Summary
No fallback when Redis is unavailable. Events are lost.

## Context
Need an in-memory ring buffer that queues events during Redis outages and flushes when connectivity returns.

## Acceptance Criteria
- In-memory buffer catches events when Redis is down
- Automatic flush on reconnection
- Buffer size configurable with overflow policy
