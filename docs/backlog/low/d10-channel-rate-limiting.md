# D10: Channel Rate Limiting

## Summary
No rate limiting middleware for notification channels. No per-user limits.

## Context
Channels (Signal, WebSocket, Voice) have no protection against burst traffic.

## Acceptance Criteria
- Per-channel rate limits (configurable)
- Per-user rate limits
- Graceful degradation when limits hit (queue or drop with warning)
