# Admin API: auth-surface consistency + perf follow-ups

**Priority:** low
**Source:** PR #21 review (multi-agent review + code-architect)

## Auth-surface consistency (pre-existing, amplified by the new admin surface)

### 1. Session cookie `Secure` flag ignores forwarded proto — RESOLVED
**Resolved on `feat/pwa-phase0-security`.** The cookie still reads
`secure=request.url.scheme == "https"`, but `core/channels/__main__.py` now runs uvicorn
with `proxy_headers=True` and `forwarded_allow_ips=FORWARDED_ALLOW_IPS`, so a trusted
proxy's `X-Forwarded-Proto: https` rewrites the scope's scheme before the route sees it
and the cookie is issued `Secure`. Pinned by
`test_cookie_is_secure_behind_https_proxy` in `tests/core/identity/test_auth_routes.py`,
which drives the login through uvicorn's own `ProxyHeadersMiddleware`.

The operator's half is `FORWARDED_ALLOW_IPS`: unset, it defaults to loopback only and the
process logs a warning at boot saying a proxy on the container network will not match it.
Documented in `.env.example` and `docs/deployment.md` ("Behind a reverse proxy"). The
session TTL also dropped from 24h to 8h in the same branch.

### 2. Integration credential endpoints gated by trusted-network only — RESOLVED
**Resolved on `feat/pwa-phase0-security`.** `PUT/DELETE
/api/integrations/{name}/credentials` and `POST/DELETE /api/devices/register` now carry
both `require_trusted_network` and `require_authenticated`; the network gate runs first
so 401-vs-403 cannot be used as a session-validity oracle. The two reads (`GET
/api/integrations`, `GET .../status`) got `require_authenticated` only — the PWA reads
them from the public host.

The onboarding hazard this note warned about turned out not to need the "allow during
unregistered onboarding" mechanism it proposed. Wizard ordering already resolves it: the
passkey is step 0 and registration sets the session cookie, so by the time the
Connections step (step 4) writes any credential the caller is authenticated. The one
remaining hole — "Skip — already registered" while signed out — now redirects to
`/login` instead of advancing, and the integrations query is gated on
`authStatus.authenticated` so a signed-out wizard never fires a 401 that would bounce
the user mid-flow.

### 3. `/ws/telemetry` (and `/ws`) trusted-network gate — WON'T FIX
Both WS endpoints authenticate by session cookie only, and so does the companion admin
REST surface as of `feat/pwa-phase0-security` (see
[`docs/admin-api.md` → Auth Model](../../admin-api.md#auth-model)) — the inconsistency
this note was written about is gone. Telemetry still
fans out all internal streams, so if defense-in-depth is ever wanted here it has to be
argued on its own merits, not as a consistency fix: network-gating the sockets would break
the admin UI from the public hostname, which is the deployment Phase 0 deliberately
enabled.

### 4. `_get_origin()` trusts `x-forwarded-proto` from any peer
`core/identity/auth_routes.py:_get_origin()` reads the `x-forwarded-proto` header
directly, unlike the rest of the request pipeline, which only honours `X-Forwarded-*`
from peers listed in `FORWARDED_ALLOW_IPS` (uvicorn `proxy_headers`). An untrusted peer
can therefore flip the WebAuthn expected origin between `http://` and `https://`.
**Acceptance:** derive the scheme from `request.url.scheme` (already rewritten by
`ProxyHeadersMiddleware` for trusted peers) instead of reading the raw header.

## Performance

### 5. Episodic browse full-keyspace SCAN
`admin_api.py memory_episodic` browse path does `scan_iter(match="ctx:*")` + `HGETALL`
per key, fetching ~6KB of packed embeddings per entry only to discard them, across a
keyspace shared with semantic + routine entries that grows with every Reflex
observation. **Acceptance:** use `FT.SEARCH idx:context` with `@type:{episodic} SORTBY
timestamp DESC LIMIT 0 <limit>` and an explicit non-embedding `RETURN` list (mind the
"RETURN N must match field count" gotcha), or at minimum `HMGET` the display fields.

### 6. Normalize `significance` server-side
`/api/admin/memory/episodic` returns `significance` in three shapes (numeric string /
JSON string / nested object) depending on store, forcing `MemoryPage.tsx`'s `sigValue`
three-way branch. **Acceptance:** normalize to one shape in the backend response and
simplify the frontend.

### 7. Parallelize + shorten overview inference probes
`admin_api.py` overview probes Ollama and LM Studio sequentially via the 2s httpx
client, so a single overview poll can wait ~4s when inference is down. **Acceptance:**
`asyncio.gather` the two probes and use a shorter (~500ms) health-probe timeout.

## Hardening

### 8. `require_trusted_network` short-circuits on the literal peer `"testclient"`
`core/channels/web_server.py:require_trusted_network` returns early when
`request.client.host == "testclient"` — Starlette's `TestClient` default peer — so the
whole network gate is skipped under test. It is not only a test-time concern: `testclient`
is a *string*, and uvicorn writes whatever `X-Forwarded-For` says into
`request.client.host`, so a proxy configuration that trusts too much (`FORWARDED_ALLOW_IPS=*`
being the worst case) turns the header `X-Forwarded-For: testclient` into a 200 on every
gated route. The process now warns about `*` at boot, but the bypass itself should go.

**Acceptance:** delete the `"testclient"` branch and give each test a real peer via
`TestClient(app, client=("192.168.1.10", 50000))`. Five modules depend on the bypass today
and all five must be migrated in the same change:

- `tests/core/channels/test_settings_api.py`
- `tests/core/channels/test_service_credentials.py`
- `tests/core/channels/test_voice_enroll.py`
- `tests/core/channels/test_device_registration.py`
- `tests/integration/test_webauthn_flow.py`

Note `tests/core/channels/test_trusted_network.py` deliberately uses `"testclient"` as a
*trusted proxy* entry in `FORWARDED_ALLOW_IPS` to exercise the forwarded-header path; that
usage is unrelated and stays.

### 9. No rate limiting on the unauthenticated surface
Everything reachable before a session exists is unmetered: `GET /api/auth/status`,
`POST /api/auth/login/begin`, `POST /api/auth/login/complete`,
`POST /api/auth/register/begin`, `POST /api/auth/register/complete`, and the `/ws` +
`/ws/telemetry` upgrades. The two `register/*` routes carry a per-client-address budget of
their own on the pairing path (`alfred:webauthn:pairing:fails:{bucket}`, 10 guesses per
300 s, bucketed to a single IPv4 address or an IPv6 /64) — but only for a well-formed `X-Pairing-Code`; a call with no header, or a malformed
one, is refused before Redis and is not metered at all. The WebSocket case is the cheapest to abuse — `require_ws_auth()` accepts the
socket *before* authenticating (deliberately, so the browser sees close code 4001 instead
of a bare 403), so every anonymous connect costs a Redis `HGETALL` and a socket. `login/begin`
additionally writes a challenge key per call.

Operationally this is covered today by Cloudflare rate-limiting rules in front of the public
hostname, which is why this is low and not higher — but the origin has no defence of its own,
and the LAN/tailnet path does not pass through Cloudflare at all.

**Acceptance:** a per-IP limiter on the six unauthenticated routes plus the WS upgrade,
applied at the origin rather than only at the edge. Mind that the peer must be read the same
way the trusted-network gate reads it (post-`ProxyHeadersMiddleware` `request.client.host`,
which `_client_address()` in `core/identity/auth_routes.py` already wraps), or a single proxy
address becomes one shared bucket for the whole internet — the exact failure mode the pairing
budget now has to live with when `FORWARDED_ALLOW_IPS` is unset.
