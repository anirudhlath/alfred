# Service Credential Card — Real Browser Settings Flow

**Feature:** Settings page `IntegrationCard` for `kind=service` entries (`web/src/pages/IntegrationCard.tsx`, `SettingsPage.tsx`)
**Priority:** high
**Type:** e2e

## Prerequisites
- Alfred running with `web/dist` built (`npm run build`) and served by `core.channels`
- Logged into the web PWA (WebAuthn passkey session) on the trusted network
- A stub or Plan-2 service registered with a `credentials_schema` + reachable `credentials_endpoint` (home-service doesn't declare one on this branch — use a stub `AlfredClient` service or Plan 2's home-service once available), plus a second scenario where the service's `credentials_endpoint` is unreachable (stopped process / wrong port)

## Test Steps
1. Navigate to `/settings` in a real browser and locate the stub service's card.
2. Confirm the card header shows the service name, category, and an "external service" badge (`kind === "service"` render path) distinct from adapter cards (weather/calendar/health/robinhood), which show no such badge.
3. Confirm the "not configured" badge shows before any save, fill in the declared fields, and click SAVE.
4. With the service's `credentials_endpoint` reachable, confirm a success toast (`"{name} credentials saved"`) appears and the badge flips to "configured".
5. Click TEST CONNECTION and confirm a toast reflects the `/status` health-proxy result (healthy/unhealthy).
6. Stop the stub service (or point its `credentials_endpoint` at a dead port), then SAVE again with new values — confirm an error toast surfaces the 502 from the failed push (not a silent failure or generic network error).
7. Click CLEAR and confirm a success toast and the badge reverts to "not configured".
8. Verify password-type fields mask input by default and the SHOW/HIDE toggle works, and that transient fields (if the stub declares one) show the "not stored" badge and are never pre-filled.

## Expected Result
- Card renders correctly for `kind=service` with visible "external service" badge, distinguishing it from in-process adapter cards.
- Save/clear/test-connection all produce the correct toast (success or error) tied to real backend responses, including the 502-on-unreachable-service path.
- No console errors, layout breakage, or stuck loading states across the flow.

## Notes
- Frontend unit/component tests (`IntegrationCard.test.tsx`, `SettingsPage.test.tsx`) use fixture data and mocked API calls — they verify the badge and rendering logic but not real network round-trips, real toast timing/stacking, or real browser rendering (fonts, layout, responsive behavior).
- Backend e2e-through-TestClient tests cover the API contract but never render the actual page.
