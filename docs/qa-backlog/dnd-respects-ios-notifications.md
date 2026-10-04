# DND Respects iOS Notifications

**Feature:** Notifications + DND
**Priority:** medium
**Type:** functional

## Prerequisites
- Alfred server running with DND enabled (manual or calendar-based)
- APNs configured, iOS device registered

## Test Steps
1. Enable DND in Alfred (via Redis key or during a calendar meeting)
2. Trigger a NORMAL or INFORMATIONAL notification
3. Check that no push notification is delivered
4. Disable DND: for manual DND, from the web app (System › Quiet switch, or
   `POST /api/admin/dnd` with `active: false`) — a raw `redis-cli DEL` of the key skips the
   drain; for calendar DND, let the meeting end
5. Verify deferred notifications are drained and delivered

## Expected Result
- No notification during DND
- Deferred notifications delivered after DND ends
- URGENT notifications bypass DND (if applicable)

## Notes
- DND is Alfred-level, not iOS-level — the test validates server-side deferral
