# WebSocket Rejection When Unauthenticated

**Feature:** WebAuthn Authentication (D1)
**Priority:** critical
**Type:** functional

## Prerequisites
- Alfred server running
- No auth cookie set (incognito window or cleared cookies)

## Test Steps
1. Open browser developer tools (Network tab)
2. Attempt to connect to `ws://localhost:8081/ws` directly (e.g., via JS console)
3. Observe the WebSocket connection result

## Expected Result
- WebSocket connection is accepted then immediately closed with code 4001
- Reason: "Authentication required"
- No session message is sent
- Chat interface is not accessible

## Notes
- This is a hard gate — all unauthenticated WS connections must be rejected
- The login screen should prevent users from reaching this state normally
