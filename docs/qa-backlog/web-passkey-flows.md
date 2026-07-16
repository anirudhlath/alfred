# Passkey Auth Flows: Register, Conditional UI Login, Logout, WS 4001

**Feature:** WebAuthn passkey authentication  
**Priority:** critical  
**Type:** functional

## Prerequisites

- Alfred runner started, channels process on port 8081
- Fresh browser profile OR cleared `alfred_auth` cookie and `alfred_session_id` localStorage
- Accessing via `localhost` (trusted network — required for registration)
- Browser supports WebAuthn / passkeys (Chrome 108+, Safari 16+, Firefox 119+)

## Test Steps

### Part A — Initial registration (Onboarding)

1. Clear the `alfred_auth` cookie and navigate to `http://localhost:8081`.
2. Observe redirect to `/onboarding` (not registered).
3. On Step 1/6 ("Register your device"), enter a device name (e.g. "Test Mac").
4. Click **Register passkey** — observe the browser biometric prompt (Touch ID / Windows Hello).
5. Complete biometric authentication.
6. Observe: the wizard advances to Step 2/6 ("A few particulars") automatically.
7. Complete the remaining steps (can use defaults) and click **Begin** on the final step.
8. Observe: redirected to Chat page (`/`).

### Part B — Conditional UI login (subsequent visit)

1. Close the browser tab and re-open `http://localhost:8081` (cookie should be set from Part A, so this may go directly to `/`).
2. To test login: delete the `alfred_auth` cookie in DevTools → Network → Cookies, or use a fresh browser context.
3. Navigate to `http://localhost:8081` — should redirect to `/login`.
4. On the Login page, observe that the passkey prompt appears automatically (Conditional UI — no button press required on supported browsers) OR click the sign-in button.
5. Complete biometric authentication.
6. Observe: redirected to Chat page (`/`).

### Part C — Sign-out

1. From any authenticated page, navigate to Settings (`/settings`).
2. Locate the **SESSION** card at the top.
3. Click **SIGN OUT**.
4. Observe: redirected to `/login`.
5. Confirm the `alfred_auth` cookie is deleted (DevTools → Application → Cookies).

### Part D — WS 4001 unauthorized redirect

1. Delete the `alfred_auth` cookie in DevTools while the Chat page is open.
2. Wait for the WebSocket to reconnect (or refresh the page partially).
3. Observe: `ReconnectingSocket` receives close code 4001 from the server.
4. Observe: socket status transitions to `"unauthorized"` — no retry loop.
5. The `api()` fetch helper, on the next REST call, receives HTTP 401 and redirects to `/login`.

## Expected Result

- Part A: Passkey registered without error; wizard advances; memory files written by `POST /api/onboarding`.
- Part B: Conditional UI presents the stored credential; no password or username required; successful authentication sets `alfred_auth` cookie.
- Part C: Cookie cleared; redirect to login; Settings page no longer accessible without re-auth.
- Part D: Unauthorized WS closes with 4001 (not a connection error); no infinite reconnect loop; user lands on login page.

## Notes

- Registration is gated to trusted network (localhost / Tailscale CGNAT). Attempting from an untrusted IP returns HTTP 403.
- If the user is already registered when reaching `/onboarding`, the passkey step shows a "Skip — already registered" button to avoid `InvalidStateError`.
- The "already set up" path (`alreadySetUp` flag in `OnboardingPage`) auto-advances step 0 → step 1 if the user is both registered and authenticated.
- The chat WebSocket (`/ws`) also enforces auth with the same 4001 mechanism.
