# Self-Hoster OCI Runtime Verification (podman, CPU-only, rootless specifics)

## Summary

**Superseded/narrowed by Part 2 containerization (2026-07-23).** This ticket originally
scoped a full runtime-agnostic compose profile. Most of that scope shipped a different
way — a single `alfredctl` launcher (no compose at all for dev/worktree use) plus a
minimal `docker-compose.yml` "compose-of-one" for production — and is documented end to
end in `docs/containerization.md`. What's left is verification work the implementation
didn't cover: podman-compose parity and rootless-podman specifics were never actually
run, and the CPU-only/low-VRAM stance is still undecided.

## What shipped (no longer open)

- ✅ Apple `container` path: decided and documented — `alfredctl` issues plain `run`
  commands per-runtime (no compose anywhere), auto-detects Apple `container` first on
  macOS, and resolves reachable URLs via `container inspect` since `-p` isn't supported.
  See `docs/containerization.md` §2, §10.
- ✅ Ollama strategy per runtime: documented and automated — `alfredctl` rewrites
  `OLLAMA_HOST` to the correct host-gateway per runtime (`host.docker.internal`,
  `host.containers.internal`, the resolved Apple vmnet gateway). See §7, §10.
- ✅ Durable-state volumes + container secrets backend: decided — `/data` bind-mount
  (`alfredctl`) or named volume (compose), `cryptfile` keyring backend with a
  passphrase, both documented in §4/§6.
- ✅ Docs refer to "an OCI runtime" generically (`docker | container | podman`)
  throughout `docs/containerization.md`, the README, and `alfredctl --runtime`'s own
  help text — not Docker-specifically.
- ✅ `docker-compose.yml` is a minimal compose-of-one (one service, no Docker-only
  extensions beyond `extra_hosts: host-gateway`, which podman-compose also supports) —
  addresses the original "no Docker-only extensions" criterion by construction, since
  there's almost nothing left in the file to be Docker-specific about.

## What's still open

- [ ] **Podman verification, not just support-in-principle:** `alfredctl --runtime
      podman up`/`down`/`smoke` has never actually been run end-to-end. Verify on a real
      Linux box with rootless Podman.
      → tracked for manual verification in
      [`docs/qa-backlog/container-podman-smoke.md`](../../qa-backlog/container-podman-smoke.md).
- [ ] **Rootless-podman specifics** (privileged-port binding for `:8081`, volume
      ownership/uid mapping for the bind-mounted `/data` and `/models` directories) —
      genuinely unverified, not assumed. `alfredctl` uses unprivileged ports (8081) and
      user-owned host directories by default, which should sidestep most of this, but it
      hasn't been confirmed on a real rootless setup.
- [ ] **CPU-only / low-VRAM mode: still no explicit decision.** The container never
      bundles GPU inference (SLM inference is always external via Ollama/OpenRouter), so
      this is really about whether the *host's* Ollama has GPU access — orthogonal to
      containerization itself. Either document "a GPU-capable host Ollama (or
      OpenRouter, which needs no GPU at all) is the supported first-run path" explicitly
      in the README prerequisites, or scope a degraded local-SLM profile. Related:
      [`docs/backlog/medium/cpu-only-torch-index.md`](../medium/cpu-only-torch-index.md)
      addresses the *image's own* CPU-only torch dependency (embedding/STT/TTS), which is
      a different axis from "can the reflex SLM run without a GPU."

## Acceptance Criteria (narrowed)

- [ ] `alfredctl --runtime podman up/down/smoke` verified green on a real rootless-podman
      Linux host; findings folded back into `docs/containerization.md` §10 (Runtime
      Matrix) if anything differs from the Docker-derived assumptions there.
- [ ] Rootless-podman privileged-port and volume-ownership behavior confirmed or a
      documented workaround added.
- [ ] Explicit CPU-only/GPU-required stance written into the README prerequisites (not
      left implicit).
