# Apple `container` Runtime: Full Lifecycle + WebAuthn + Host Ollama Chat

**Feature:** Containerization (`alfredctl`) — Apple `container` runtime
**Priority:** critical
**Type:** e2e

## Prerequisites

- macOS with the Apple `container` CLI installed and working (`container system start`
  succeeds, `container network ls` shows a `default` network — see
  `docs/containerization.md` §13 if not, for the version-skew fix).
- `alfred-home-service` cloned as a sibling directory (`../home-service` relative to this
  repo) — required by `alfredctl build`'s staged context.
- A local Ollama install with a model pulled (e.g. `ollama pull gpt-oss:20b`), OR an
  `OPENROUTER_API_KEY`/`CLAUDE_API_KEY` in `.env` for System 2.
- A browser that supports WebAuthn/passkeys (Chrome 108+, Safari 16+, Firefox 119+) and a
  platform authenticator (Touch ID) on this Mac.
- `alfredctl` is Docker-only by default in CI — this ticket exists because Apple
  `container` has no macOS CI runner and must be exercised manually.

## Test Steps

1. `uv run alfredctl up --mode seed --runtime container` — observe the build completes,
   the container starts, and a URL is printed (`http://<vmnet-ip>:8081`, resolved via
   `container inspect`, not `localhost` — Apple `container` doesn't support `-p`).
2. `uv run alfredctl urls --runtime container` — confirm it independently resolves and
   prints the same reachable URL without restarting anything.
3. Open the printed URL in a browser. Confirm the SPA loads (onboarding or chat,
   depending on prior state).
4. Complete WebAuthn passkey registration through the onboarding wizard (Register your
   device → Touch ID prompt → completes). This specifically exercises the
   `ALFRED_TRUSTED_NETWORKS` auto-injection of the Apple vmnet subnet
   (`192.168.64.0/24`) — registration is gated to trusted networks, and requests arrive
   from the vmnet gateway, not localhost, when hitting the container's own IP.
5. Send a chat message that requires the local reflex/System 1 path (e.g. a request that
   would trigger a smart-home style tool call) if a home-service is registered, OR simply
   confirm a conversational round-trip via System 2 completes.
6. If testing the reflex path specifically: confirm reflex reaches host Ollama through
   the injected gateway (`alfredctl` rewrites `OLLAMA_HOST` in `.env` to the resolved
   vmnet gateway address) — check `alfredctl logs --runtime container` for a successful
   `POST /api/chat` to Ollama, not a connection-refused/timeout.
7. `uv run alfredctl smoke --runtime container --hf-cache ~/.cache/huggingface` — confirm
   all 6 checks (health, redis, redisearch, mqtt, spa, data-dir) PASS.
8. `uv run alfredctl down --runtime container` — confirm the container is removed
   (`container ls` no longer lists it).

## Expected Result

- `up`/`urls`/`down` all resolve the container's own IP correctly (never `localhost`) and
  the printed URL is actually reachable in a browser.
- WebAuthn registration succeeds through the vmnet subnet without a manual
  `ALFRED_TRUSTED_NETWORKS` edit — proves the auto-injection in `alfredctl/launch.py`
  works for real, not just in unit tests.
- Chat works end-to-end; if Ollama-backed, the gateway rewrite actually reaches host
  Ollama (not a hardcoded `localhost` that would fail inside the container network
  namespace).
- `alfredctl smoke` reports PASS on all checks.
- `down` fully tears down — no orphaned container/image left running.

## Notes

- Docker-runtime end-to-end is **not** needed as a separate QA item — the branch's final
  containerized smoke gate (`uv run alfredctl build` → `alfredctl smoke` → `alfredctl up
  --mode seed` → verify → `alfredctl down`, run as part of shipping this branch) already
  covers Docker. This ticket exists specifically because Apple `container` has no CI
  coverage and needs a human on real hardware.
- If `container network create`/`container run` fails with `Error: builtin network is
  not present` or a `DecodingError`, see the version-skew troubleshooting entry in
  `docs/containerization.md` §13 before treating this as a product bug.
- Delete this file once verified.
