# Trusted-Network Gating for Service Credential Endpoints via Real Tailscale Client

**Feature:** `require_trusted_network` gate on `PUT`/`DELETE /api/integrations/{name}/credentials` (service + adapter paths)
**Priority:** high
**Type:** integration

## Prerequisites
- Alfred core reachable over Tailscale (CachyOS deployment or a dev machine joined to the Tailscale tailnet), with `core.channels` bound to a non-localhost interface
- A second real device on the same Tailscale tailnet (e.g. iPhone or another laptop) with a Tailscale CGNAT address (`100.64.0.0/10`)
- A third device/network path that is NOT on the tailnet and not localhost (e.g. public internet or a different LAN) to confirm rejection
- A stub or Plan-2 service with a declared `credentials_schema` to exercise the service credential path specifically (not just the adapter path)

## Test Steps
1. From the real Tailscale-connected second device, hit `PUT /api/integrations/{service_name}/credentials` with valid values over the tailnet address of the Alfred host.
2. Confirm the request succeeds (200) and the push to the service's `credentials_endpoint` happens as expected.
3. From a non-trusted network path (e.g. disable Tailscale on the second device and hit the same endpoint over public IP/LAN, or use a device never added to the tailnet), repeat the same PUT.
4. Confirm the request is rejected (403) by `require_trusted_network` and no keyring write or push occurs.
5. Repeat steps 1-4 for `DELETE /api/integrations/{service_name}/credentials`.
6. Confirm `GET /api/integrations` and `GET /api/integrations/{name}/status` remain accessible from the non-trusted path (per `docs/secrets.md`, these are unauthenticated/no trusted-network requirement) — verifying the gate is scoped correctly to only the mutating endpoints.

## Expected Result
- Only genuinely trusted-network clients (localhost or real Tailscale CGNAT peers) can write or delete service credentials; all other network paths are rejected with 403.
- Read endpoints remain reachable as designed, confirming the gate isn't over- or under-scoped.

## Notes
- `require_trusted_network`'s CIDR logic is presumably unit-tested against synthetic IPs, but real Tailscale traffic (MagicDNS, NAT traversal, subnet routers, exit nodes) can present addresses or routing behavior that differs from a synthetic `100.64.0.0/10` test — this is the only way to catch a real-world mismatch (e.g. a Tailscale relay altering the apparent source IP as seen by FastAPI/uvicorn).
- Also good to confirm behavior behind a reverse proxy if one is used in prod (X-Forwarded-For handling), since that would change what IP `require_trusted_network` actually sees.
