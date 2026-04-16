# APNs Credential Setup and E2E Testing

## Summary
Configure Apple Push Notification service credentials in the Secrets Manager and validate the full notification delivery path from Alfred to iOS device.

## Context
APNs adapter code is implemented (PR #16) but actual Apple Push certificates/keys have not been configured. The adapter needs real credentials to deliver push notifications. Sandbox vs production environment must be validated.

## Acceptance Criteria
- APNs key (p8 format) stored in Secrets Manager under service "apns"
- team_id, key_id, bundle_id configured
- Sandbox environment tested with TestFlight build
- Production environment validated
- Device token registration verified end-to-end
- Push notification delivered to real iOS device
