# Critical Action Confirmation Flow (end-to-end)

**Feature:** Tiered autonomy — confirmation flow
**Priority:** critical
**Type:** e2e

## Prerequisites
- Full stack running (`python -m runner`), home-service registered with a `risk: critical` tool (e.g. a lock; Plan 2 risk map)
- Logged into the web SPA with a passkey

## Test Steps
1. In web chat, ask Alfred to actuate a critical entity (e.g. "unlock the front door")
2. Observe the URGENT notification toast with a Confirm button
3. Click Confirm
4. Repeat step 1, wait >5 minutes, then click Confirm on the stale toast
5. Repeat step 1, then confirm over chat instead: "yes, go ahead"

## Expected Result
- Step 1: no actuation; toast appears with Confirm; conscious reply mentions confirmation is required
- Step 3: action executes within ~1s; "Action confirmed" success toast
- Step 4: error toast "Pending action not found or expired" (404); no actuation
- Step 5: Conscious calls `confirm_pending_action`; action executes

## Notes
- v1 rule: confirmation is required even for direct user commands
