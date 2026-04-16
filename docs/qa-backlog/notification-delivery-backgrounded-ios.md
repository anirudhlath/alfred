# Notification Delivery — Backgrounded iOS App

**Feature:** iOS Notifications
**Priority:** medium
**Type:** functional

## Prerequisites
- APNs credentials configured
- Real iOS device with Alfred app installed and backgrounded/killed

## Test Steps
1. Background or kill the Alfred iOS app
2. Trigger a notification from Alfred server (any urgency)
3. Check the iOS notification center

## Expected Result
- Push notification appears in notification center even when app is backgrounded/killed
- Tapping notification opens the app

## Notes
- APNs delivery is independent of WebSocket connection
- Device token must be registered via POST /api/devices/register
