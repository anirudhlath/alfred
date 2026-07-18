# Self-Hoster OCI Compose Profile (runtime-agnostic one-command bring-up)

## Summary

A compose-spec profile bundling redis-stack, mosquitto, and all Alfred
services for one-command bring-up — **OCI-compliant and runtime-agnostic**:
must work with Docker Compose AND podman-compose, with a documented path for
runtimes that lack compose support (Apple `container`). Docker is a supported
runtime, never a requirement.

## Context / Motivation

Launch-readiness assessment (2026-07-18), revised per owner direction: the
project already commits to OCI at the image layer (`Containerfile`, not
`Dockerfile`; Apple `container` CLI in dev) — the orchestration layer must
honor the same principle so self-hosters can use podman, Docker, or other OCI
runtimes. The existing `docker-compose.yml` targets the owner's prod box
(external `alfred-net` network, containerized `homeassistant` assumption) and
includes no infrastructure services.

## Acceptance Criteria

- [ ] Compose file conforms to the open compose-spec: no Docker-only
      extensions; images referenced/built from the existing `Containerfile`s.
- [ ] Verified green on BOTH `docker compose up` and `podman compose up`
      (rootless podman preferred) on a clean machine with only the runtime
      and an `.env` file; no external network prerequisites.
- [ ] Apple `container` path decided and documented: compose is unsupported
      there, so either ship a small bring-up script (per-container
      `container run` equivalents) or take an explicit "use the
      Homebrew-services dev path on macOS" stance. Note the known `-p` port
      forwarding limitation wherever relevant.
- [ ] Ollama strategy documented per runtime: host-installed vs containerized
      with GPU passthrough (Linux/NVIDIA), with `OLLAMA_HOST` wiring for each.
- [ ] Explicit decision on CPU-only / low-VRAM mode: either a supported
      degraded profile (smaller SLM, slower reflex) or a documented "GPU
      required" stance in the prerequisites.
- [ ] Volumes for durable state (SQLite cold store); container-mode secrets
      backend decided and documented (OS keyring is unavailable in
      containers).
- [ ] Rootless-podman specifics verified, not assumed: privileged-port
      binding, volume ownership/uid mapping for redis-stack and mosquitto.
- [ ] All docs refer to "an OCI runtime (Docker, Podman, …)" rather than
      Docker specifically; README prerequisites updated to match.
