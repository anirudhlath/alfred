# PWA plan 0b backend follow-ups

**Priority:** low
**Source:** plan 0b implementation + per-task quality reviews (Tasks 7, 8, 10)

Everything below was found while building the plan-0b backend surface (overview
reflex/librarian blocks, probe latency, sessions/passkeys/pairing) and deliberately left
out of its scope. None of it blocks the branch.

## 1. `Overview` in `web/src/lib/types.ts` lacks the new fields
`GET /api/admin/overview` now returns `reflex` (`model`, `last_ms`, `p50_ms`) and
`librarian` (`last_run_at`, `reviewed`, `next_run_at`) — see
[`admin-api.md` → Overview](../../admin-api.md#overview-1) — but the hand-mirrored TS
`Overview` interface has neither, so the frontend cannot consume them without a cast. Its
`cost` member carries the same drift from the earlier overview upgrade that added
`cost.request_count` and `cost.avg_usd` (plan 0b): both are returned, and documented, but
absent from the type. **Acceptance:** add the two blocks (all fields nullable) and the two
optional `cost` fields to `web/src/lib/types.ts` in the client task that renders them.

## 2. No timeout around an adapter's `health_check()`
`GET /api/integrations/{name}/status` (`core/channels/web_server.py`) awaits
`instance.health_check()` with no deadline on the adapter branch, so a hung adapter hangs
the request and the `latency_ms` it reports. The service branch is bounded by the 2.0 s
lifespan `httpx.AsyncClient` and is fine. **Acceptance:** wrap the adapter probe in
`asyncio.wait_for` with a budget close to the service client's, and report the timeout as
`healthy: false` with the elapsed time.

## 3. Adapter first-call latency is not comparable, and `latency_ms` doesn't show why
The clock is placed correctly — `IntegrationRegistry.get()` runs *before* `perf_counter()`,
so constructing the adapter and reading the keyring are already excluded. The first probe
after a boot or a reconfigure is still slower than the steady-state one, because the freshly
built adapter opens its connection inside `health_check()` itself, and nothing in the
response distinguishes that reading from a later one. **Acceptance:** either surface "first
probe since boot" in the client, or warm the registry at startup so the distinction
disappears.

## 4. A non-dict JSON body `AttributeError`s the completion routes
`register/complete` and `login/complete` in `core/identity/auth_routes.py` do
`body = await request.json()` then `body.get(...)`. A syntactically valid non-object body
(`[]`, `"x"`, `12`) reaches `.get` and raises `AttributeError` → 500 instead of 422.
Pre-existing; the ceremony fails either way, so this is tidiness rather than exposure.
**Acceptance:** a Pydantic model, or an `isinstance(body, dict)` guard raising 422, on both
routes.

## 5. `_start_session` writes the hash and its TTL non-atomically
`hset` then `expire` (pre-existing, moved verbatim in plan 0b). A crash between the two
leaves a session hash with no TTL — an 8-hour session that never expires. **Acceptance:**
pipeline the pair (or use `hset` + `hexpire` on Redis 7.4+). Note this churns the
`redis_mock.expire` assertions in `TestSessionLifetime`
(`tests/core/identity/test_auth_routes.py`).

## 6. The challenge fetch/decode/delete block is duplicated
`register/complete` and `login/complete` carry the same block: read
`alfred:webauthn:challenge:{id}`, 400 if missing, decode bytes, delete, `base64url_to_bytes`.
Pre-existing. **Acceptance:** one `_consume_challenge(challenge_id) -> bytes` helper used by
both.

## 7. The pairing failure counter is `INCR` then `EXPIRE`
`_pairing_code_valid` increments `alfred:webauthn:pairing:fails` and then sets its TTL in a
second round trip (documented as deliberate in the code: nothing else in the repo calls
`.pipeline()`, and the key self-heals on the next mint, which deletes it). A crash between
the two leaves the counter without a TTL until then. **Acceptance:** if a pipeline lands
anywhere else in the codebase, fold this in with it; not worth introducing the pattern
alone.
