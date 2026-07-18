# Self-Hoster Docker Compose Profile (one-command bring-up)

## Summary

A `docker compose up`-grade path for self-hosters: compose profile bundling
redis-stack, mosquitto, and all Alfred services, with documented GPU/Ollama
expectations and a degraded CPU-only mode decision.

## Context / Motivation

Launch-readiness assessment (2026-07-18): the existing `docker-compose.yml`
targets the owner's prod box — it references an external `alfred-net` network,
assumes a containerized `homeassistant` host, and doesn't include
infrastructure services. A self-hoster currently needs Homebrew services +
manual steps. One-command bring-up is table stakes for r/selfhosted adoption.

## Acceptance Criteria

- [ ] A compose profile (or overlay file) that starts redis-stack, mosquitto,
      and all Alfred processes on a fresh machine with only Docker + an
      `.env` file; no external network prerequisites.
- [ ] Ollama strategy documented: host-installed Ollama vs containerized with
      GPU passthrough (Linux/NVIDIA), with `OLLAMA_HOST` wiring for each.
- [ ] Explicit decision on CPU-only / low-VRAM mode: either a supported
      degraded profile (smaller SLM, slower reflex) or a documented "GPU
      required" stance in the prerequisites.
- [ ] Volumes for durable state (SQLite cold store, keyring alternative for
      containerized secrets — note: OS keyring is unavailable in containers;
      decide and document the container-mode secrets backend).
- [ ] Verified once on a clean Linux host or VM.

## Notes

- Gate final polish on HA Plan 2 (home-service rewrite) so the compose file
  ships the real home-service, not the legacy one.
