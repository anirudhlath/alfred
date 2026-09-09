# Setup Gate E2E: Fresh State → Passkey, Credentials, Attention Set

**Feature:** First-run setup gate (`web/src/gates/SetupGate.tsx` — three steps)
**Priority:** high
**Type:** e2e

## Prerequisites

- Alfred runner started with a clean data directory (`data/credentials.db` absent or cleared)
- Browser with no `alfred_auth` cookie and cleared `localStorage`, on `localhost`
- Redis reachable, so `GET /api/admin/attention` has domains to offer
- Keyring access on the server, to verify the credential write

## Test Steps

### Part A — The three steps, with real data

1. Clear all Alfred auth state: delete `data/credentials.db`, flush `alfred:auth:*` in Redis,
   clear browser cookies and localStorage.
2. Navigate to `http://localhost:8081`. Observe the **Setup gate** — no redirect, the URL
   stays `/`. Kicker `first run · localhost`, title "Good evening. I am Alfred.", and a
   three-row progress list.
3. **Step 1 — Register this device:** click **Create passkey with Face ID**, complete the
   biometric prompt. There is no device-name field; the name comes from the user agent
   (`defaultDeviceName()`) and is stored under `alfred.device`.
4. Observe: the gate advances — kicker `first run · 1 of 3 done`, title "Registered."
5. **Step 2 — The house:** paste a long-lived Home Assistant token into the declared field
   (the fields come from home-service's `credentials_schema`, so their labels and types are
   whatever the adapter declares). Click **Continue**.
6. Observe: the gate advances — kicker `first run · 2 of 3 done`, title
   "What may the reflex touch?", one toggle row per attention domain, each labelled
   `{Domain} · {n} found`.
7. Toggle one domain on and one off, then click **Finish**.
8. Observe: the gate falls away and the Room is behind it — headline, status line, composer.

### Part B — Verify what the gate actually wrote

9. `GET /api/integrations` (or reload the gate's step 2) — home-service reads as configured;
   the token is in the keyring, not in Redis or on disk in the clear.
10. `GET /api/admin/attention` — the domain toggled **on** has its seen entities added
    (`allow`), and the domain toggled **off** has its current members removed (`ask`). A row
    left untouched must produce **no write at all**; watch the Network panel to confirm.
11. Confirm `lock`, `alarm_control_panel` and `cover` were never offered as rows: the client
    keeps them out of automatic attention (`NEVER_AUTOMATIC`), whatever the server reports.

### Part C — The skip paths

12. Repeat Part A with fresh state, and at step 2 click **Do this later** instead of Continue.
    Confirm the gate still advances to the attention step and no credential write is sent.
13. Repeat with fresh state and click **Finish** at step 3 without touching any toggle.
    Confirm zero `PUT /api/admin/attention/*` requests are sent, and the Room opens anyway.
14. With Redis returning no attention domains, confirm step 3 is skipped entirely rather than
    showing an empty list.

### Part D — Off-network first run

15. Reach the same fresh instance from an untrusted origin and click
    **Create passkey with Face ID**.
16. Observe: registration is refused with 403 and the **Denied gate** ("Not from here.")
    rises over the setup gate. The setup gate's own foot line does not repeat the message.

## Expected Result

- Part A: passkey registered; all three steps complete; the Room opens without a reload.
- Part B: the credential lands in the keyring and the attention writes match the toggles
  exactly — nothing is written for a row that ended where it started.
- Part C: both skip paths reach the Room and neither invents a write.
- Part D: the 403 is said once, by the gate that owns that message.

## Notes

- **This is not the old six-step wizard.** `POST /api/onboarding` still exists server-side
  (`core/channels/web_server.py`), but no phase 1 client calls it: the personal / proactivity
  / guest-access steps that wrote `core/memory/preferences/` and `core/memory/profile/` have
  no surface until the Workshop lands (phase 2). Do not expect preference files to change.
- Registration authenticates the session, so `AuthGate` seeds `["auth-status"]` itself after
  the ceremony rather than waiting for a refetch.
- Credential writes need a trusted network as well as a session — see
  `docs/backlog/low/pwa-phase1-followups.md` §1 for why step 5 can fail on the public host
  with no warning beforehand.
