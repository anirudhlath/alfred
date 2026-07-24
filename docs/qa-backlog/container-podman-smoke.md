# Podman Runtime: Build + Up + Smoke + Down

**Feature:** Containerization (`alfredctl`) — Podman runtime
**Priority:** high
**Type:** e2e

## Prerequisites

- A Linux (or macOS with `podman machine`) host with Podman installed and working
  (`podman info` succeeds). Rootless Podman preferred, since that's the self-hoster-
  relevant configuration.
- `alfred-home-service` cloned as a sibling directory (`../home-service`).
- Podman is never exercised in CI (`container-build.yml` only builds on Docker/`ubuntu-
  latest` + `ubuntu-24.04-arm` runners) — this is Podman's only verification path today.

## Test Steps

1. `uv run alfredctl build --runtime podman` — confirm the image builds successfully
   using `podman build` under the hood.
2. `uv run alfredctl up --mode seed --runtime podman` — confirm the container starts and
   a `http://localhost:8081`-style URL is printed (Podman supports `-p` like Docker, so
   URL resolution should take the simple path, not the Apple-`container`
   `inspect`-and-resolve-IP path).
3. Confirm the printed port is actually bound and reachable: `curl -s
   http://localhost:8081/health` returns 200.
4. `uv run alfredctl smoke --runtime podman --attach` — confirm all 6 checks (health,
   redis, redisearch, mqtt, spa, data-dir) PASS against the already-running container
   from step 2 (exercises `podman exec` for the in-container checks).
5. If running rootless: confirm no privileged-port or volume-ownership errors occurred
   in any of the steps above (rootless Podman maps some things differently than
   Docker/rootful Podman — this hasn't been verified on a real rootless host before).
6. `uv run alfredctl down --runtime podman` — confirm the container is removed
   (`podman ps -a` no longer lists it).

## Expected Result

- Build, up, smoke, and down all succeed with `--runtime podman` with no Docker-specific
  assumption leaking through (e.g. no `host.docker.internal`-only behavior — Podman
  should get `host.containers.internal` per `alfredctl/runtime.py:host_gateway()`).
- `alfredctl smoke` PASSes all 6 checks running its internal commands via `podman exec`.
- No privileged-port binding or bind-mount ownership failures under rootless Podman.

## Notes

- If host-gateway resolution needs verifying too (Ollama reachable from inside the
  Podman container via `host.containers.internal`), fold that check in here rather than
  filing a separate ticket — it's the same session's setup either way.
- This ticket also serves as the concrete verification step referenced by
  `docs/backlog/low/self-hoster-oci-compose-profile.md`'s "still open" items (Podman
  parity, rootless specifics) — fold any findings back into that ticket and
  `docs/containerization.md` §10 if Podman behaves differently than documented.
- Delete this file once verified.
