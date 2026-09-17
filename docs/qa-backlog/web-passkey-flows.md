# Passkey Auth Flows: Register, Sign In, Expired Gate, WS 4001

**Feature:** WebAuthn passkey authentication through the PWA client's identity gates
(`web/src/gates/`, `web/src/lib/webauthn.ts`)
**Priority:** critical
**Type:** functional

## Prerequisites

- Alfred runner started, channels process on port 8081
- Fresh browser profile OR cleared `alfred_auth` cookie and cleared `localStorage`
  (`alfred.session`, `alfred.device`, `alfred.theme`, `alfred.unsent`)
- Accessing via `localhost` (trusted network — the path this case registers on; the
  pairing-code alternative has its own case,
  [`webauthn-registration-pairing-code.md`](webauthn-registration-pairing-code.md))
- Browser supports WebAuthn / passkeys (Chrome 108+, Safari 16+, Firefox 119+)

## Test Steps

### Part A — First run (Setup gate, three steps)

1. Clear the `alfred_auth` cookie and navigate to `http://localhost:8081`.
2. Observe the **Setup gate**, not a redirect: the URL stays `/`. Kicker reads
   `first run · localhost`, title "Good evening. I am Alfred."
3. Click **Create passkey with Face ID** — observe the browser biometric prompt (Touch ID /
   Windows Hello). There is no device-name field: the name is derived from the user agent
   (`defaultDeviceName()`) and stored under `alfred.device`.
4. Complete biometric authentication.
5. Observe: the gate advances to step 2 — kicker `first run · 1 of 3 done`, title
   "Registered.", asking for a long-lived Home Assistant token.
6. Enter a token and click **Continue** (or **Do this later** to skip; both reach step 3).
7. Observe step 3 — kicker `first run · 2 of 3 done`, title "What may the reflex touch?",
   one toggle row per attention domain. Click **Finish**.
8. Observe: the gate falls away and the Room is behind it — headline, status line, composer.

### Part B — Sign-in gate (subsequent visit, no session)

1. Delete the `alfred_auth` cookie in DevTools → Application → Cookies.
2. Reload `http://localhost:8081`.
3. Observe the **Sign-in gate**: kicker `localhost · signed out`, title "Welcome back, sir.",
   a **Sign in with Face ID** button, and a foot line naming the remembered device.
4. Click it and complete biometric authentication.
5. Observe: the gate falls away and the Room is behind it.

### Part C — Expired gate rises over the Room (401)

1. From the Room, delete the `alfred_auth` cookie in DevTools without reloading.
2. Wait for the next `["overview"]` poll (≤30 s) or force one by backgrounding and returning.
3. Observe: the **Expired gate** rises *over* the Room — kicker `401 · session lapsed`, title
   "Your session lapsed.", body naming the eight-hour cap. The Room stays rendered behind it;
   the URL does not change and nothing blanks.
4. Sign in with Face ID; the gate falls away and the same Room is still there.

### Part D — WS 4001 while the page is open

1. From the Room, delete the `alfred_auth` cookie and force the chat socket to reconnect
   (kill the channels process, or wait for the next reconnect).
2. Observe: `ReconnectingSocket` receives close code 4001 and stops — status `"unauthorized"`,
   no retry loop in the Network panel.
3. Observe the Room tells the truth about it: the headline reads `Unreachable.` and the
   offline note carries a real `last true HH:MM`.
4. Sign in again through the gate; `ConnectionProvider` reopens both sockets (a 4001 is never
   retried by backoff, so this is the only path back).

## Expected Result

- Part A: passkey registered without error; the three setup steps complete; credentials land
  in the keyring (`GET /api/integrations` shows home-service configured) and the attention
  choices in `GET /api/admin/attention`.
- Part B: the stored credential authenticates; `alfred_auth` cookie set.
- Part C: **401 never redirects.** The gate is an overlay; the last-known Room is visible
  behind it throughout.
- Part D: unauthorized WS closes with 4001 (not a connection error); no infinite reconnect
  loop; both sockets recover after sign-in without a page reload.

## Notes

- Registration is gated to trusted network (localhost / Tailscale CGNAT) **or** a valid
  `X-Pairing-Code` header on both `register/begin` and `register/complete`. Attempting from an
  untrusted IP with no code — or with a wrong/expired one — returns HTTP 403, which raises the
  **Denied gate** ("Not from here."); `SetupGate` deliberately does not repeat that message in
  its own foot line.
- A rejected passkey assertion is a 401 too. `api()` excludes `/api/auth/login/*` and
  `/api/auth/register/*` from the expired signal, so a failed attempt shows in the gate that
  asked instead of raising a second one over it.
- **Phase 1 has no sign-out affordance.** `logout()` exists in `lib/webauthn.ts` but nothing
  in the client calls it; the settings screen that carried SIGN OUT is phase 2's Workshop. To
  end a session for testing, clear the cookie or flush `alfred:auth:*` in Redis.
- The chat WebSocket (`/ws`) and the telemetry socket enforce auth with the same 4001
  mechanism.
