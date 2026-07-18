# Harden Trusted-Network Gate & Enforce Identity Clearance

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** medium
**Severity (audit):** medium
**Source:** Public-release readiness audit 2026-07-18 (findings #13, #14)

## Summary
Two authorization primitives are weaker than they appear. The trusted-network gate
authorizes on the raw socket peer IP and ignores forwarding headers, so any deployment
behind the documented TLS-terminating reverse proxy (the standard way to add HTTPS)
sees every request as `127.0.0.1` and treats the entire internet as trusted. Separately,
identity is effectively binary `sir`/`guest`: `sir` is granted on a client-supplied
`identity_claim` even when unauthenticated, and the computed `confidence`/`risk_clearance`
fields are never read, so the tiered-clearance design is dead code. Because `alfred`
shipped v0.1.0 as its first public release on 2026-07-16, this is post-exposure hardening
of already-published code, not a pre-release fix — anyone deploying the current tree
behind a reverse proxy inherits the bypass.

## Context / Motivation

### Trusted-network gate trusts the socket peer IP (finding #13)
`require_trusted_network` (`core/channels/web_server.py:215-226`) authorizes a request
if `request.client.host` is `127.0.0.1`/`::1` or inside the Tailscale CGNAT range
`100.64.0.0/10`. It reads the raw socket peer and never consults `X-Forwarded-For`. The
standard way to add HTTPS to this app is a TLS-terminating reverse proxy
(nginx/caddy/traefik) — the docs even reference uvicorn `proxy_headers` and
`x-forwarded-proto`. Behind such a proxy every client's `request.client.host` is
`127.0.0.1`, so the gate passes for the entire internet.

Scope/mitigation: the CGNAT/loopback ranges are not internet-routable on their own; the
bypass materializes specifically when the service is fronted by a reverse proxy that
rewrites the peer address. A bare uvicorn bound to a loopback/Tailscale interface is not
directly reachable. Related endpoints: `core/identity/auth_routes.py`.

### Identity clearance is computed but never enforced (finding #14)
`IdentityGate.resolve` returns full `IDENTITY_SIR` (confidence `0.7`, risk_clearance
`low`) for any `web_pwa`/`voice`/`ios` `UserRequest` whose client-supplied
`identity_claim == 'sir'`, even when unauthenticated
(`core/conscious/identity.py:74-81`). The Conscious Engine gates integration tools,
memory tools, and home-domain tools purely on `identity.identity == 'sir'`
(`core/conscious/engine.py:650-658`; the domain tools at `:648` aren't gated at all) —
`confidence` and `risk_clearance` are computed but read nowhere, so the tiered-clearance
design is dead code. Relevant locations also include `core/channels/web_server.py` (the
WS auth result that should feed `UserRequest`).

## Acceptance Criteria
- [ ] Bind uvicorn to the loopback/Tailscale interface only (not `0.0.0.0`).
- [ ] When a reverse proxy is used, derive the client IP from a trusted-proxy-validated `X-Forwarded-For` (or run uvicorn `--proxy-headers` with `forwarded_allow_ips` restricted) rather than trusting the raw socket peer in `require_trusted_network`.
- [ ] Add `require_authenticated` to the credential-write and device endpoints once the onboarding flow allows it.
- [ ] Propagate the WS auth result into `UserRequest.authenticated` and require `authenticated` (not a client-supplied string) to resolve to `sir` in `IdentityGate.resolve`.
- [ ] Either enforce `risk_clearance` on high-impact tools (calendar CRUD, credential/integration actions) — including the currently-ungated domain tools at `engine.py:648` — or remove the unused `confidence`/`risk_clearance` fields to avoid a false sense of tiering.

## Related
- [Admin API: auth-surface consistency + perf follow-ups](../low/admin-auth-and-perf-followups.md)
- [Admin API: respect owning-process boundaries](../medium/admin-api-owner-boundaries.md)
