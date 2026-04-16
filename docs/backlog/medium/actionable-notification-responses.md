# Actionable Notification Responses

## Summary
Add accept/reject inline actions to routine suggestion notifications so users can respond directly from push notifications without opening the app.

## Context
Currently, routine suggestions via notifications are text-only. Users must open the app and respond in chat to accept/reject. Actionable notifications (Signal reply, WebSocket action buttons, APNs interactive notifications) would reduce friction.

## Acceptance Criteria
- Signal: reply with "yes"/"no" to accept/reject a routine suggestion
- WebSocket: action buttons in the notification card
- APNs: UNNotificationAction categories with "Accept" and "Reject" buttons
- Backend: new endpoint or stream handler to process accept/reject responses
- Routine state updated accordingly (active/archived)
