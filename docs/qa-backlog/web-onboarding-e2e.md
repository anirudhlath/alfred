# Onboarding E2E: Fresh State → Wizard → Memory Files Written, Skips Respected

**Feature:** Onboarding wizard (OnboardingPage — 6 steps)  
**Priority:** high  
**Type:** e2e

## Prerequisites

- Alfred runner started with a clean data directory (`data/credentials.db` absent or cleared)
- Browser with no `alfred_auth` cookie, on `localhost`
- Access to `core/memory/preferences/` and `core/memory/profile/` on the server

## Test Steps

### Part A — Full wizard flow with real data

1. Clear all Alfred auth state: delete `data/credentials.db`, flush `alfred:auth:*` keys in Redis, clear browser cookies.
2. Navigate to `http://localhost:8081` — observe redirect to `/onboarding`.
3. **Step 1/6 — Register device:** Enter device name "QA Test Device". Click **Register passkey**. Complete biometric prompt.
4. Observe: wizard advances to Step 2/6.
5. **Step 2/6 — Personal:** Set wake time to `08:30`, work address "456 Test Ave", dietary restrictions "vegan". Click **Continue**.
6. **Step 3/6 — Proactivity:** Select **Conservative**. Click **Continue**.
7. **Step 4/6 — Guest access:** Uncheck "Lighting" (default on), leave "Media" checked. Click **Continue**.
8. **Step 5/6 — Connections:** Expand any integration card and enter a (test) credential. Click **SAVE** on that card. Click **Continue** without saving others.
9. **Step 6/6 — Done:** Read the "Very good, sir." message. Click **Begin**.
10. Observe: redirected to Chat page (`/`).

### Part B — Verify memory files written

11. On the server, inspect `core/memory/preferences/` — confirm a preferences file was written or updated with wake_time `08:30`, dietary restrictions `vegan`, proactivity_level `conservative`.
12. Confirm `core/memory/profile/` or the appropriate user profile file reflects guest_controls: `["Media playback"]` (Lighting was unchecked).

### Part C — Skip defaults respected

13. Repeat Part A with a fresh state but at Step 4 (Guest), leave all defaults (Lighting + Media on), do NOT enter any integration credentials at Step 5, and click **Continue** → **Begin**.
14. Confirm that the preferences file shows `guest_controls: ["Lighting control", "Media playback"]`.
15. Confirm no integration credentials are stored for unconfigured integrations.

### Part D — Already-registered skip behavior

16. While authenticated, navigate directly to `/onboarding`.
17. Observe Step 1 auto-skipped (wizard starts at Step 2/6 without the passkey prompt) because `authStatus.registered && authStatus.authenticated` is true.
18. Confirm the "Back" button is hidden on Step 2 when reached via this path (no valid step to return to).

## Expected Result

- Part A+B: Memory preference files reflect the exact values entered in the wizard.
- Part C: Default values (Lighting + Media) are preserved when no explicit change is made; empty optional fields (`work_address`, `dietary_restrictions`) are omitted from the payload.
- Part D: Already-registered users skip the passkey step cleanly; no `InvalidStateError` is thrown.

## Notes

- `OnboardingPage` sends `POST /api/onboarding` with `OnboardingPayload` fields; empty optional strings are sent as `undefined` (omitted from JSON).
- The wizard has 6 steps (TOTAL_STEPS = 6). The progress bar reflects `(activeStep + 1) / 6 * 100`.
- Step 5 (Connections) renders `IntegrationCard` components with `showActions={false}` — credentials can be saved per-card but there is no bulk save.
- On `finish.mutate()` success, `auth-status` query is invalidated before navigation to ensure the `Guarded` component re-validates.
