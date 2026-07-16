# Admin API: auth-surface consistency + perf follow-ups

**Priority:** low
**Source:** PR #21 review (multi-agent review + code-architect)

## Auth-surface consistency (pre-existing, amplified by the new admin surface)

### 1. Session cookie `Secure` flag ignores forwarded proto
`core/identity/auth_routes.py` (register/login complete) sets the `alfred_auth` cookie
with `secure=request.url.scheme == "https"`, while `_get_origin` honors
`x-forwarded-proto`. Behind a TLS-terminating proxy the backend sees `http`, so the
24h cookie is issued without `Secure`. Mitigated today by HttpOnly + SameSite=Strict +
Tailscale-encrypted transport and uvicorn `proxy_headers`. **Acceptance:** derive
`secure` from the same forwarded-proto logic as `_get_origin`, or a config flag
defaulting to `True` in non-dev. (Touches security-critical, working passkey code —
verify the full register→login cycle after.)

### 2. Integration credential endpoints gated by trusted-network only
`PUT/DELETE /api/integrations/{name}/credentials` (`web_server.py`) require only
`require_trusted_network`, not `require_authenticated` (the admin surface requires
both). **Note:** onboarding may set integration credentials before a passkey session
exists (trusted-network is the intended pre-auth gate there), so adding
`require_authenticated` naively would break onboarding — resolve that first (e.g. allow
during unregistered onboarding, require auth once registered).

### 3. `/ws/telemetry` (and `/ws`) trusted-network gate
Both WS endpoints authenticate by session cookie only; the companion admin REST also
requires trusted-network. Telemetry fans out all internal streams. **Acceptance:**
decide whether WS endpoints should also enforce trusted-network (defense-in-depth) and
apply consistently to `/ws` + `/ws/telemetry`.

## Performance

### 4. Episodic browse full-keyspace SCAN
`admin_api.py memory_episodic` browse path does `scan_iter(match="ctx:*")` + `HGETALL`
per key, fetching ~6KB of packed embeddings per entry only to discard them, across a
keyspace shared with semantic + routine entries that grows with every Reflex
observation. **Acceptance:** use `FT.SEARCH idx:context` with `@type:{episodic} SORTBY
timestamp DESC LIMIT 0 <limit>` and an explicit non-embedding `RETURN` list (mind the
"RETURN N must match field count" gotcha), or at minimum `HMGET` the display fields.

### 5. Normalize `significance` server-side
`/api/admin/memory/episodic` returns `significance` in three shapes (numeric string /
JSON string / nested object) depending on store, forcing `MemoryPage.tsx`'s `sigValue`
three-way branch. **Acceptance:** normalize to one shape in the backend response and
simplify the frontend.

### 6. Parallelize + shorten overview inference probes
`admin_api.py` overview probes Ollama and LM Studio sequentially via the 2s httpx
client, so a single overview poll can wait ~4s when inference is down. **Acceptance:**
`asyncio.gather` the two probes and use a shorter (~500ms) health-probe timeout.
