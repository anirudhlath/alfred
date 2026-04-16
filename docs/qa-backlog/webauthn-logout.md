# WebAuthn Logout

**Feature:** WebAuthn Authentication (D1)
**Priority:** high
**Type:** functional

## Prerequisites
- Alfred server running
- Logged in with valid passkey session

## Test Steps
1. Navigate to Settings page (`/settings.html`)
2. Scroll to "Session" section at the bottom
3. Click "Sign Out" button (red)
4. Observe redirect behavior

## Expected Result
- Page reloads after logout
- Login screen appears (not chat interface)
- Auth cookie `alfred_auth` is cleared
- Redis session is deleted
- Re-login with passkey works correctly

## Notes
- Verify the cookie is actually removed (check browser dev tools)
- After logout, WebSocket connections should also be rejected
