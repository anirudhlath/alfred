# Routine Suggestion Push Notification — iOS

**Feature:** D3 Pattern Detection + Proactive Notifications
**Priority:** high
**Type:** e2e

## Prerequisites
- Alfred server running with Conscious Engine
- APNs credentials configured in Secrets Manager
- Real iOS device with Alfred app installed
- At least one candidate routine detected by Librarian

## Test Steps
1. Ensure a candidate routine exists (e.g., `evening_dim` with trigger_pattern `20:00 daily`)
2. Wait for the proactive suggestion check to fire (every 15 minutes) during the routine's time window
3. Observe the iOS device for a push notification

## Expected Result
- Push notification appears with title "Routine Suggestion"
- Body contains the routine description and asks if the user wants to automate it
- Notification respects DND settings

## Notes
- APNs must be configured before this test can run
- If no candidate routines exist, manually create one via the RoutineStore
