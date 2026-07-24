# Production Compose-of-One on the CachyOS Box (x86_64 build + boot)

**Feature:** Containerization — production deployment (`docker-compose.yml`)
**Priority:** critical
**Type:** e2e

## Prerequisites

- The CachyOS deployment box (RTX 4090, 64GB RAM, 5800X3D) with Docker + Docker Compose
  installed.
- `alfred` and `alfred-home-service` both cloned as sibling directories on that box.
- Ollama running on the CachyOS host with a model pulled (this is the box the 4090 lives
  on — the reflex sub-500ms target assumes local Ollama, not a cloud endpoint).
- A filled-in `.env` (copied from `.env.example`): at minimum
  `OPENROUTER_API_KEY`/`CLAUDE_API_KEY` for System 2, `HA_TOKEN`/`HA_HOST` if a real
  Home Assistant is in play, and `OLLAMA_HOST` pointed at the host gateway (see Test
  Steps — compose does not rewrite this automatically).
- `container-build.yml` CI already builds arm64 + amd64 on GitHub-hosted runners — this
  ticket is specifically about building **on the actual target box** and running the
  **production compose path**, neither of which CI exercises.

## Test Steps

1. On the CachyOS box: `uv run alfredctl build --tag alfred:latest` — confirm the
   x86_64 image builds successfully on real deployment hardware (not just GitHub's
   `ubuntu-latest` amd64 runner — different kernel/Docker version, real disk I/O).
2. `cp .env.example .env` and fill in real values, including setting
   `OLLAMA_HOST=http://host.docker.internal:11434` explicitly (compose does **not**
   rewrite `localhost`/`127.0.0.1` the way `alfredctl up` does — see
   `docs/containerization.md` §9).
3. `ALFRED_SECRETS_PASSPHRASE=$(openssl rand -base64 32) docker compose up -d` — confirm
   the container starts and `docker compose logs -f` shows all supervised services
   (bridge, reflex, triggers, conscious, channels, memory-ingestor, home-service, redis,
   mosquitto) starting without crash-looping.
4. `curl http://localhost:8081/health` — confirm 200.
5. Confirm the reflex path reaches the host's real Ollama (not a container-internal
   one) — trigger a state-change event (e.g. via a real HA integration if configured, or
   a manual `redis-cli XADD` into `alfred:home:state_changed` if not) and check
   `docker compose logs` for a successful round-trip to Ollama on the host.
6. Confirm `ALFRED_TRUSTED_NETWORKS` needs to be set manually for WebAuthn/admin access
   through this path (compose does not auto-inject the Docker bridge subnet the way
   `alfredctl up` does) — either set it in `.env` before this test or confirm the
   documented gap is accurate.
7. `docker compose down` (without `-v`) then `docker compose up -d` again — confirm state
   persists (see the dedicated persistence ticket,
   [`container-persistent-mode-retention`](container-persistent-mode-retention.md), for
   the detailed check list; this step is a lighter smoke-level confirmation in the
   production context specifically).
8. Confirm `restart: unless-stopped` actually survives a host reboot if feasible to test
   (`sudo reboot` the box, or simulate via `systemctl restart docker`).

## Expected Result

- Image builds cleanly on the real x86_64 production box.
- All supervised services start and stay up under `docker compose`.
- `/health` returns 200; the reflex path reaches the host's real GPU-backed Ollama.
- Named-volume state (`alfred_data`, `alfred_models`) survives `down`/`up` without `-v`.
- Service survives a Docker/host restart via `restart: unless-stopped`.

## Notes

- This is the one deployment path with genuinely no automated coverage at all (CI builds
  the image but never runs `docker compose up`) and the one that actually matters for
  the live household — treat failures here as release-blocking, not backlog.
- If Ollama reachability fails, double-check `host.docker.internal:host-gateway` in
  `extra_hosts` actually resolves on this specific Docker version/CachyOS kernel — this
  is the "needs `--add-host` on Linux Docker Engine" case noted in
  `docs/containerization.md` §9/§10, unlike Docker Desktop which provides it natively.
- Delete this file once verified.
