# Containerization (Part 1: App Foundation)

> **Stub.** This doc covers the app-side foundation landed in Part 1 (data-dir
> consolidation, secrets backend, trusted-network config, generalized runner). Part 2
> added the fat `Containerfile`, the `alfredctl` launcher, the model cache volume, port
> exposure, and a prod compose-of-one, and retired the Homebrew `scripts/dev-*.sh`
> wrappers — this doc will be expanded into the full guide next.

## Data directory model

All runtime-writable state (SQLite databases, the scratchpad, routines, preferences,
profile, trigger snapshots, the secrets keyring file) is consolidated under a single
root resolved by `shared.config.data_root()` / `data_path()`:

- **`ALFRED_DATA_DIR`** — root dir for all runtime-writable state. Defaults to `./data`.
  Point this at a bind-mounted or named volume in a container to persist state across
  restarts, or leave it as a throwaway path for isolated dev/worktree runs.

Package-shipped preference/profile/routine files under `core/memory/` are read-only
templates; `core.memory.paths.seed_defaults()` copies them into the data dir on first
boot only (never overwrites existing files). See `core/memory/paths.py` for the
per-subsystem path helpers (`scratchpad_path()`, `episodic_cold_path()`,
`preferences_dir()`, `profile_dir()`, `routines_dir()`, `triggers_snapshot_dir()`).

- **`ALFRED_DATA_MODE`** — declares the data lifecycle. Part 1 only exposes the switch
  (`shared.config.data_mode()`); the actual ephemeral/seed *behavior* (wiping state on
  container start, loading dummy fixtures, redis persistence flags) is wired up by the
  container runtime in Part 2.
  - `persistent` (default) — production. State survives restarts.
  - `ephemeral` — dev/worktree. State is expected to be thrown away between runs.
  - `seed` — dev with dummy fixture data pre-loaded, for demos/QA without touching real
    personal data.

## Secrets backend

`shared/secrets.py` wraps the `keyring` library and selects a backend via
`select_backend_name()`:

- **`ALFRED_SECRETS_BACKEND`** — `native` (macOS Keychain, the default on Darwin) or
  `cryptfile` (an encrypted file-based keyring, for containers/Linux where no OS
  keychain is available). If unset, the backend is auto-detected from `sys.platform`.
- **`ALFRED_SECRETS_PASSPHRASE`** — passphrase for the `cryptfile` backend's encrypted
  store. Required (in practice) whenever `ALFRED_SECRETS_BACKEND=cryptfile`; the
  cryptfile itself lives under `ALFRED_DATA_DIR/secrets/keyring.cfg`.

## Trusted networks

`core/channels/web_server.py` gates WebAuthn/admin endpoints to trusted CIDRs
(Tailscale CGNAT plus any extra ranges supplied via):

- **`ALFRED_TRUSTED_NETWORKS`** — comma-separated extra trusted CIDRs, e.g. the
  container's bridge subnet, so WebAuthn/admin access still works when Alfred runs
  inside a container network rather than directly on the Tailscale-connected host.

## Process model

`runner/__main__.py` supervises the core Python services (bus bridge, reflex,
triggers, conscious, channels, memory-ingestor) via `runner.supervisor.Supervisor`.

- **`ALFRED_MANAGE_INFRA`** — when truthy (`1`/`true`/`yes`), the runner additionally
  spawns and supervises `redis-stack-server` and Mosquitto (with readiness checks
  before dependent services start) as child processes. This is the container's job —
  native dev keeps running its own Redis Stack + Mosquitto directly (Homebrew or
  otherwise, no wrapper script), so this stays unset outside a container.

## What's still to come (Part 2)

- ~~Fat `Containerfile` bundling the full runtime~~ — done: single-image build via
  `alfredctl build`.
- ~~`alfredctl` launcher script/CLI~~ — done: `build`/`up`/`down`/`logs`/`shell`/`urls`/`smoke`.
- ~~Model cache volume for Ollama/embedding models~~ — done: `/models` volume,
  `ALFRED_MODELS_DIR`/`HF_TOKEN`.
- ~~Port exposure + prod compose-of-one~~ — done: `docker-compose.yml` is now a
  compose-of-one for the fat image.
- ~~Deletion of `scripts/dev-up.sh` once the container fully owns infra lifecycle~~ — done.
- arm64 image validation
- A full rewrite of this doc covering the end-to-end container workflow
