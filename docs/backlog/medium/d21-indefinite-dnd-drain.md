# D21: Indefinite DND Drain via Keyspace Notification

## Summary
When DND has no `until`, deferred queue strands until next expiry-based drain or restart.

## Context
Use Redis keyspace notifications on DND_STATE_KEY deletion to trigger immediate drain when DND is manually turned off.

## Acceptance Criteria
- Subscribe to keyspace notifications for DND_STATE_KEY
- Immediate drain triggered on DND key deletion
- Works for both timed and indefinite DND
