# Devcontainer / Environment Bootstrap for Cloud Agents

**Epic:** [GitHub Chores](../epics/github-chores.md)
**Priority:** medium
**Source:** GitHub agent-enablement round-up 2026-07-18; updated 2026-07-23 post
containerization Part 2 (`.devcontainer/` now exists — most of the shape below already
shipped; this ticket narrows to what's left).

## Summary

Alfred's dev environment needs redis-stack, mosquitto, Python 3.13 (system may be 3.14),
and Node — `.devcontainer/` now exists and provisions exactly this (see "What shipped"
below). What's left is *verification from a real cloud agent session* and a couple of
doc/link cleanups; the provisioning shape itself is done.

## What shipped (no longer open)

- ✅ `.devcontainer/devcontainer.json` + `.devcontainer/docker-compose.yml`: a
  `mcr.microsoft.com/devcontainers/python:3.13` dev service, a `redis/redis-stack-server`
  service, and an `eclipse-mosquitto:2` service, with the Node 22 devcontainer feature.
  No `ports:` published on any service — safe by construction inside the devcontainer's
  private network.
- ✅ `.devcontainer/post-create.sh`: installs `uv`, runs `uv venv --python 3.13 && uv
  sync --all-extras`, then `(cd web && npm ci)` — matches the target
  `postCreateCommand` shape exactly, and prints the `HF_HUB_OFFLINE=1` reminder for test
  runs (gated embedding default — see
  [`embedding-model-gated-first-run`](../high/embedding-model-gated-first-run.md)).
- ✅ **Stale assumption fixed:** the original text suggested reusing "the existing prod
  `docker-compose.yml` service definitions" for the devcontainer's redis/mosquitto
  services. That's no longer possible or desirable — Part 2 containerization replaced
  the old multi-service prod compose with a **compose-of-one** running the single fat
  image (`docker-compose.yml` now has exactly one `alfred` service, no bare
  redis/mosquitto service definitions to borrow). The devcontainer's own standalone
  redis-stack/mosquitto services (as shipped) are the correct, independent shape — see
  `docs/containerization.md` §11 for the explicit deviation-from-spec rationale (building
  the fat multi-stage image on every Codespace boot would be prohibitively slow for an
  edit/test loop).
- ✅ **Dead link removed:** the original text referenced a
  `lock-down-compose-redis-mqtt.md` backlog ticket that does not exist in this repo (no
  such file was ever filed) — dropped rather than perpetuated. The underlying concern
  (no published ports, no plaintext secrets) is satisfied by the shipped
  `.devcontainer/docker-compose.yml` regardless.

## What's still open

- [ ] **Verified from at least one real cloud agent session** (Claude Code web or a
      GitHub Codespace) — inspection of the config is not the same as a live run.
      Confirm: `pytest` passes, `npm run build` works, and `python -m runner` starts with
      Redis/MQTT reachable (voice/embedding warmup failures acceptable/documented per the
      gated-model ticket).
- [ ] CLAUDE.md (repo root) documents all three environment paths explicitly — native
      Homebrew-managed Redis/Mosquitto (macOS), `alfredctl up` (containerized,
      macOS/Linux), and `.devcontainer/` (Linux/cloud edit-test loop) — so an agent
      picks the right one for its environment without guessing. (Root `CLAUDE.md`'s
      *Dev Environment Notes* section names `.devcontainer/` and `alfredctl`; confirm it
      stays current as those paths evolve.)

## Acceptance Criteria (narrowed)

- [ ] Verified from at least one real cloud agent session (Claude Code web or
      Codespace) — the sole functional gap left in this ticket.
- [ ] CLAUDE.md's environment-path guidance re-read for currency at the same time
      (cheap to check while already in a live session).
