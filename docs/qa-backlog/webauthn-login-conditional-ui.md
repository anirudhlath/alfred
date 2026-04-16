# WebAuthn Login with Conditional UI

**Feature:** WebAuthn Authentication (D1)
**Priority:** high
**Type:** functional

## Prerequisites
- Alfred server running
- At least one passkey already registered
- Session cookie expired or cleared

## Test Steps
1. Clear cookies or wait for session expiry (24hr)
2. Open Alfred PWA at `http://localhost:8081`
3. Observe login screen appears with "Sign in with Passkey" button
4. Check if browser shows passkey autofill suggestion in the input field (Conditional UI)
5. Either tap the autofill suggestion or click "Sign in with Passkey"
6. Complete biometric prompt

## Expected Result
- Login screen renders with Alfred branding
- On Safari/Chrome, passkey autofill appears in the input field
- After successful login, redirects to chat interface
- WebSocket connection established successfully

## Notes
- Conditional UI (autofill) only works in Safari 16+, Chrome 108+
- Fallback button should work in all browsers that support WebAuthn
- If browser doesn't support passkeys, shows unsupported message
