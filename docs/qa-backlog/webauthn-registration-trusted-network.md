# WebAuthn Registration on Trusted Network

**Feature:** WebAuthn Authentication (D1)
**Priority:** critical
**Type:** functional

## Prerequisites
- Alfred server running (`python -m runner`)
- Browser on Tailscale network or localhost
- No existing passkey registered (fresh `data/credentials.db`)

## Test Steps
1. Open Alfred PWA at `http://localhost:8081`
2. Observe onboarding step 0 shows "Register Your Device" with passkey form
3. Enter a device name (e.g., "MacBook Pro")
4. Click "Register Passkey"
5. Complete the browser's passkey prompt (Touch ID / Face ID / Windows Hello)
6. Observe onboarding advances to step 1 (preferences)

## Expected Result
- Step 0 shows passkey registration form (not the old welcome text)
- Browser prompts for biometric/passkey creation
- After successful registration, cookie `alfred_auth` is set (HttpOnly)
- Onboarding proceeds to step 1

## Notes
- Registration MUST be on a trusted network (localhost or Tailscale CGNAT) **or** carry a
  valid `X-Pairing-Code` header on both `register/begin` and `register/complete` — a
  6-digit code minted by an already-signed-in device via `POST /api/auth/pairing`
  (5-minute TTL, single-use; 10 wrong guesses refuse the guessing address, not the
  code). This case covers the network path; the pairing path is
  [`webauthn-registration-pairing-code.md`](webauthn-registration-pairing-code.md),
  which needs a second device already registered.
- If attempted from an untrusted network with no code, should get 403 error
