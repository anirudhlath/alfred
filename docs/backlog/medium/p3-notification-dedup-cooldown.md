# P3: Notification Dedup/Cooldown

## Summary
Hash-based dedup with Redis TTL key to prevent notification storms.

## Context
Repeated sensor triggers or multiple sources detecting the same situation can cause notification floods. Need `notification:{source}:{title_hash}` with configurable cooldown (default 5min, urgent = no cooldown).

## Acceptance Criteria
- Hash-based dedup key in Redis with TTL
- Default 5min cooldown, configurable per urgency
- Urgent notifications bypass cooldown
