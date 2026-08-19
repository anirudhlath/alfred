# Deployment

Self-host Alfred as a production instance. The goal is **clone → run**: one image,
one `.env`, external state in volumes. This guide is the canonical onboarding path; for
the deeper containerization design (build context, runtime matrix, volume seams) see
[`containerization.md`](containerization.md).

## TL;DR

```bash
git clone https://github.com/anirudhlath/alfred && cd alfred
uv venv --python 3.13 && uv pip install -e ".[dev]"

cp .env.example .env          # fill in OPENROUTER_API_KEY (+ HA_TOKEN for home control)
uv run alfredctl doctor       # validate your .env before starting
uv run alfredctl up           # build the image + start everything, prints the URL
```

`alfredctl up` builds the fat image (auto-cloning the `home-service` sibling if needed),
starts the container, and prints the reachable URL (default `http://localhost:8081`).

## What you must configure

Only one value is strictly required to get a reasoning assistant:

| Setting | Required for | How to get it |
|---------|--------------|---------------|
| `OPENROUTER_API_KEY` | System 2 (reasoning, conversation) | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `HA_TOKEN` | Home Assistant control | HA → your profile → Security → **Long-lived access tokens** → Create |
| local inference | System 1 (fast reflex path) | [Ollama](https://ollama.com) (`ollama pull gpt-oss:20b`) or any OpenAI-compatible server (vLLM/LM Studio) |

Everything else in `.env.example` under **OPTIONAL** has a working default. In particular:

- **Memory embeddings work out of the box** — the default model is ungated and needs no
  Hugging Face token or license. (Set `EMBEDDING_MODEL` to a gated model like
  `google/embeddinggemma-300m` only if you also provide `HF_TOKEN` and accept its license.)
- **Secrets are self-managing** — the keyring passphrase is generated and persisted in the
  data volume on first boot. Set `ALFRED_SECRETS_PASSPHRASE` only to pin your own. Either
  way, **back up the data dir** — losing it loses your stored integration credentials.
- **Host services just work with `localhost`** — `OLLAMA_HOST`, `OPENAI_COMPAT_HOST`,
  `HA_HOST`, etc. may point at `localhost`; the container rewrites it to the host gateway
  automatically (both `alfredctl up` and plain `docker compose`).

## `alfredctl doctor` — preflight

Run before starting to catch config gaps early instead of discovering them in the logs:

```bash
uv run alfredctl doctor            # validates .env + live-probes OpenRouter/HA/inference
uv run alfredctl doctor --offline  # shape checks only, no network
```

It reports a pass/warn/fail row per subsystem (System 2, System 1, Home Assistant, memory
embeddings, the home-service sibling). `up` also prints any non-pass rows before building.

## Two ways to run

### A) `alfredctl` (recommended)

```bash
uv run alfredctl up                # persistent (production) — state survives restarts
uv run alfredctl up --mode seed    # demo/QA — dummy fixtures, thrown away on teardown
uv run alfredctl logs -f
uv run alfredctl down
```

### B) Docker Compose (compose-of-one)

```bash
cp .env.example .env               # fill it in
uv run alfredctl build --tag alfred:latest
docker compose up -d
```

No passphrase or host-networking flags to remember — the compose file needs only your
`.env`. (See [`docker-compose.yml`](../docker-compose.yml).)

## Verify it works

```bash
uv run alfredctl smoke             # boots seed mode, health-checks infra + SPA, tears down
uv run alfredctl smoke --deep      # ALSO drives a real request through System 2 (needs a
                                   # valid OPENROUTER_API_KEY) and confirms a reply comes back
```

`--deep` is the check that actually exercises the cloud-LLM path end to end — use it after
setting your key to confirm reasoning is live, not just that the web server is up.

## Host tuning (Linux)

In-container Redis logs a warning unless the host allows memory overcommit. Set it once:

```bash
sudo sysctl -w vm.overcommit_memory=1
echo 'vm.overcommit_memory = 1' | sudo tee /etc/sysctl.d/99-redis-overcommit.conf
```

This lets Redis background-save reliably under memory pressure. It's a host-level setting
(the container can't set it for you) and is otherwise cosmetic.

## Access from your LAN

WebAuthn passkey registration and the admin API are gated to trusted networks. By default
Alfred trusts **loopback, private LAN (RFC1918), and Tailscale** — so localhost, the Docker
bridge, and your own home network all work with no configuration. To reach it from another
device, browse to the host's LAN IP on port 8081 and register a passkey.

- Add extra ranges with `ALFRED_TRUSTED_NETWORKS=10.1.2.0/24,…` (comma-separated).
- Lock it down with `ALFRED_TRUSTED_NETWORKS_STRICT=1` to trust **only** loopback,
  Tailscale, and the CIDRs you list explicitly (passkeys remain the primary auth either way).

A rejected request returns a 403 that names the offending IP and how to allow it.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `alfredctl doctor` shows System 2 ✗ | `OPENROUTER_API_KEY` missing/placeholder — set it in `.env`. |
| Memory recall disabled in logs | You set a gated `EMBEDDING_MODEL` without `HF_TOKEN`. Use the ungated default or provide the token + accept the license. |
| 403 registering a passkey | Your client IP isn't trusted — add its subnet to `ALFRED_TRUSTED_NETWORKS` (the 403 message names the IP). |
| `home-service repo not found` | `alfredctl build` auto-clones it; if you build by hand, `git clone https://github.com/anirudhlath/alfred-home-service ../home-service`. |
| Signal delivery disabled | Optional — install `signal-cli` to enable it. |
| Redis overcommit warning | Set `vm.overcommit_memory=1` on the host (see Host tuning). |

More runtime-specific troubleshooting (Apple `container`, Podman) is in
[`containerization.md` §13](containerization.md).

## Continuous deployment

Every merge to `alfred`'s `master` deploys to lath-server automatically — nobody runs
`docker compose up -d` by hand for it anymore. Every merge to `alfred-satellite`'s
`master` rolls out to the Pi fleet the same way. `alfred-home-service` is the one piece
still pending: its dispatch job is written but not yet merged
(`alfred-home-service#19`, blocked on minting `ALFRED_DISPATCH_TOKEN`). Design:
`docs/superpowers/specs/2026-08-18-cd-local-runner-design.md`.

### The shape

Checks run on GitHub-hosted runners; exactly one job per repo runs on a self-hosted runner
on lath-server, and it cannot start until the `ci-ok` aggregate check has passed **and**
the commit is already on the trunk. A pull-request build can never reach this house.

### What an automated deploy does

On every push to `alfred`'s `master` (and, once `alfred-home-service#19` merges, on a
`repository_dispatch` from `alfred-home-service`), the `deploy to lath-server` job:

1. Checks out `alfred` and `alfred-home-service` as **siblings** — the fat image build
   stages `git ls-files` from both, resolving home-service as a sibling of the alfred
   checkout.
2. `uv sync` on Python 3.13. (`setup-uv` runs with `enable-cache: false` here specifically:
   on a self-hosted runner the default cache dir is the operator's real `~/.cache/uv`, and
   the action's post-step would prune it on every deploy.)
3. **Preflight** — `alfredctl doctor --offline --env-file ~/code/alfred-deploy/.env`,
   before the build, so a misconfigured box fails in seconds rather than after a multi-minute
   image build. `--offline` so a transient outage of an external endpoint can't fail a
   deploy that would otherwise have succeeded.
4. **Records the rollback target** — the image backing the *currently running* container
   (`docker inspect -f '{{.Image}}' alfred`, falling back to `alfred-alfred-1` purely so the
   very first deploy under this workflow still found a target), tagged `alfred:rollback` so
   the prune step below can never destroy it. Deliberately not "whatever `alfred:latest`
   points at" — if a previous deploy left `latest` pointing at something broken, rolling
   back to it would restore the breakage.
5. **Build** — `alfredctl build --tag alfred:latest`, then tags the same image
   `alfred:<40-char-sha>` so history is addressable.
6. **Publishes the compose file** — copies `alfred/docker-compose.yml` over
   `~/code/alfred-deploy/docker-compose.yml`. This happens on *every* deploy — see "The
   deploy workspace" below for what that means for local edits.
7. **Start** — `docker compose up -d` from `~/code/alfred-deploy/`. The job writes its
   `started=1` marker to `$GITHUB_OUTPUT` **before** running this command, not after:
   `docker compose up -d` stops the old container before creating its replacement, so under
   `bash -e` a failed `up` would otherwise abort before a trailing "we started" marker ever
   got written — and the rollback step below only fires when that marker is set. This is the
   one failure mode that matters most: a partial container replacement that leaves alfred
   down with rollback silently skipped.
8. **Verify** — `alfredctl smoke --attach --name alfred`, which polls `/health` (up to 300s)
   then checks redis, the RediSearch modules and mosquitto *inside* the running container.
9. **Rolls back on any failure of 7 or 8** — retags `alfred:rollback` back to
   `alfred:latest`, `docker compose up -d` again, re-runs the smoke check, and fails the job
   **red regardless of whether the rollback itself verified**. A recovered deploy is still a
   failed deploy.
10. **Prunes old deploy images** — keeps the 5 most recent `alfred:<sha>` tags and runs
    `docker builder prune --filter until=336h -f`. Each fat image is ~8.8GB unique and
    consecutive builds share almost nothing; left alone, this fills the root filesystem in
    35–50 deploys. **The builder prune is global** — it also evicts build cache belonging to
    the other projects sharing this box's Docker daemon (usher, usher-web, ha-home-panel,
    comfyui), not just alfred's.

### The deploy workspace

`~/code/alfred-deploy/` — the same directory manual deploys have always used, not a new
CD-only tree — holds the operator-managed state that no workflow writes:

| Path | What |
|---|---|
| `.env` | `OPENROUTER_API_KEY`, `HA_TOKEN`, `OLLAMA_HOST`, … (0600) |
| `docker-compose.yml` | Overwritten from the checkout on every deploy (step 6 above) |
| `satellites.yaml` | Pi inventory: `name`, `host`, `port`, `area` |
| `id_ed25519_satellites` + `.pub` | SSH key trusted by every Pi (0600) |
| `alfred/`, `home-service/` | The pre-existing manual-deploy checkouts (pull-only) |
| `backups/`, `DEPLOY-FRICTION-LOG.md` | Pre-existing, unrelated to CD |

No passphrase file: the secrets passphrase is generated and persisted in the `alfred_data`
volume on first boot.

**Local additions to `docker-compose.yml` do not survive a deploy.** Every run overwrites it
from the checkout, so hand-editing it directly — say, uncommenting `# - "1883:1883"` to
publish the MQTT port for real Home Assistant edge publishing — gets silently reverted on
the next merge. Put local additions in `docker-compose.override.yml` in the same directory
instead: Compose merges it automatically on every `docker compose up`, and the deploy step
never touches it.

**`docker-compose.yml` pins `name: alfred` and `container_name: alfred`.** The running
container really is named `alfred` now (it was `alfred-alfred-1` before the first CD
deploy). The project-name pin is load-bearing: without it, running compose from
`~/code/alfred-deploy/` derives the project name `alfred-deploy` from the directory it runs
in, which creates a fresh set of **empty** volumes — losing the secrets passphrase persisted
in `alfred_data` — while the old container keeps holding `:8081`. Verified after the first
real deploy: the volumes were unchanged and `alfred_alfred_data` still reported its original
2026-07-24 creation time. Do not remove either pin.

### The runners

| Runner | Install dir | Registered to | Label | systemd unit |
|---|---|---|---|---|
| `alfred` | `~/.local/share/github-runner/alfred` | `anirudhlath/alfred` | `alfred-deploy` | `actions.runner.anirudhlath-alfred.lath-server-alfred.service` |
| `alfred-satellite` | `~/.local/share/github-runner/alfred-satellite` | `anirudhlath/alfred-satellite` | `alfred-satellite` | `actions.runner.anirudhlath-alfred-satellite.lath-server-satellite.service` |
| `ha-home-panel` (pre-existing) | `~/.local/share/github-runner/ha-home-panel` | `anirudhlath/ha-home-panel` | `home-panel` | `actions.runner.anirudhlath-ha-home-panel.linux-server.service` |

All three are installed as services (`./svc.sh install anirudhlath && ./svc.sh start`) so
they survive reboots. `alfred` and `alfred-satellite` joined `ha-home-panel`, which predates
this design.

Each runner's `.path` file is pinned to a clean
`/usr/local/sbin:/usr/local/bin:/usr/bin:/home/anirudhlath/.local/bin` — the runner's
default captures whatever happened to be on `$PATH` at registration time, which included
Claude plugin cache directories that get garbage-collected and would otherwise break a
deploy weeks later for an unrelated reason. `docker` and `uv` both resolve from `/usr/bin`.

Check health with `systemctl list-units 'actions.runner*'` — all three should be
`active`/`running`. Restart one with `sudo systemctl restart <unit>`.

To install another, download the current `actions/runner` release into
`~/.local/share/github-runner/<repo>/`, then:

```bash
./config.sh --url https://github.com/anirudhlath/<repo> --token <REGISTRATION_TOKEN> \
            --name lath-server-<repo> --labels <label> --work _work --unattended --replace
sudo ./svc.sh install anirudhlath && sudo ./svc.sh start
```

### Rolling back by hand

Every deploy tags the image it built as `alfred:<sha>` (the 5 most recent are kept) and the
image it replaced as `alfred:rollback`:

```bash
docker images alfred --format '{{.Tag}}\t{{.CreatedSince}}'   # find the sha you want, or use `rollback`
docker tag alfred:<sha-or-rollback> alfred:latest
cd ~/code/alfred-deploy && docker compose up -d
cd ~/code/alfred-deploy/alfred && uv run alfredctl smoke --attach --name alfred
```

### Rotating a secret

`.env` never enters a workflow — it lives on the box and the workflows reference it only by
path. To rotate:

```bash
$EDITOR ~/code/alfred-deploy/.env
uv run --directory ~/code/alfred-deploy/alfred alfredctl doctor --offline --env-file ~/code/alfred-deploy/.env
cd ~/code/alfred-deploy && docker compose up -d   # env_file is read at container start
```

Credentials held in the keyring (not `.env`) rotate through the Settings page instead.

### What an automated satellite rollout does

Every push to `alfred-satellite`'s `master` runs the `deploy to the satellite fleet` job
on the self-hosted `alfred-satellite` runner, gated on `ci-ok` exactly like alfred's —
confirmed live: on the PR that added this job, the `deploy` job itself showed **skipped**,
so a pull-request build has the same "cannot reach this house" property alfred has.
`concurrency: {group: deploy-satellites, cancel-in-progress: false}` (two rollouts racing
over the same `.prev` directory on every Pi would corrupt it) and `timeout-minutes: 30`.
The job is thin glue around the tested tool:

```bash
uv run python -m dev.deploy_satellites \
  --checkout . \
  --inventory ~/code/alfred-deploy/satellites.yaml \
  --key ~/code/alfred-deploy/id_ed25519_satellites \
  --user anirudhlath
```

(`--user` is passed explicitly even though it matches the CLI default, so the workflow
stays self-documenting rather than silently depending on a default that could change.
`setup-uv` runs `enable-cache: false` for the same reason as alfred's job — the runner's
own `~/.cache/uv` shouldn't be churned by a caching action built for ephemeral GitHub
runners.)

Per device, the tool:

1. Refuses to proceed if `/opt/alfred-satellite/config.env` is missing.
2. Rsyncs the checkout to `/opt/alfred-satellite-src` as `anirudhlath` over SSH (excluding
   `.git`, `.venv`, `.venv-dev`, `__pycache__`), using `--rsync-path="sudo rsync"` so the
   remote side runs privileged — `/opt` is root-owned, and every other command in the
   sequence already runs under `sudo` — **never directly onto `/opt/alfred-satellite`**.
   `scripts/setup.sh` installs *into* `/opt/alfred-satellite` from wherever it's run; an
   earlier version of this design rsynced the checkout onto that same directory, which a
   dry run (`rsync -n --delete`) showed would have deleted thousands of files, including
   the device's `config.env` and both virtualenvs the systemd units execute.
3. Copies the device's own `config.env` into the source dir.
4. Moves `/opt/alfred-satellite` to `/opt/alfred-satellite.prev`.
5. Runs `sudo /opt/alfred-satellite-src/scripts/setup.sh`, which rebuilds the install dir
   from scratch (apt packages, a fresh `wyoming-satellite` clone, both virtualenvs).
6. Restarts both units (`wyoming-satellite.service`, `wyoming-openwakeword.service`),
   confirms both report `systemctl is-active`, then probes the Wyoming port — up to 30
   times, 2 seconds apart. The retry is load-bearing, not defensive padding: `is-active`
   returns as soon as the process starts, before `wyoming-satellite` has loaded its models
   and bound the port, and a single immediate probe rolled back a perfectly healthy device
   on the live fleet during rehearsal.
7. Any failure from step 4 onward restores `.prev` and restarts.

Rehearsing against the real Pi (not just the unit tests) is what surfaced both of the
defects above — the `--rsync-path="sudo rsync"` fix and the port-probe retry — neither of
which a dry run or a mock-transport test could have caught. The port-probe rollback in
particular is good evidence the rollback path itself works: the device came back with the
install dir restored, both units active, and its identity (`config.env`) intact.

### Adding a satellite

The rollout tool lives in `alfred-satellite` (`dev/deploy_satellites.py`) and defaults to
`~/code/alfred-deploy/satellites.yaml` and `~/code/alfred-deploy/id_ed25519_satellites`.

1. Flash and network the Pi. **The fleet's SSH user is `anirudhlath`, not `pi`** — the `pi`
   account does not exist on these images — then trust the deploy key:
   `ssh-copy-id -i ~/code/alfred-deploy/id_ed25519_satellites.pub anirudhlath@<host>`
2. **Provision it once, by hand** — this step is not automated and never will be: copy the
   `alfred-satellite` checkout to the device, fill in its `config.env` (device name, area,
   wake word, mic device), and run `sudo scripts/setup.sh`. The automated rollout refuses
   to touch a device that has never been provisioned this way — a missing
   `/opt/alfred-satellite/config.env` is reported, never silently provisioned, by design.
3. Add it to `~/code/alfred-deploy/satellites.yaml` with `name`, `host`, `port` and an
   `area` that matches a Home Assistant area name exactly.
4. Rehearse from an `alfred-satellite` checkout before trusting the change:
   `uv run python -m dev.deploy_satellites --dry-run`
5. Merge anything to `alfred-satellite`'s `master` (or run the same command without
   `--dry-run` to roll out immediately, off-cycle). The merge path is the normal one now —
   see "What an automated satellite rollout does" above.

A Pi that answers mDNS but is missing from `satellites.yaml` is still deployed to, with a
warning. So is one in the file that does not answer mDNS. Both sources exist because the
file is authoritative today while discovery earns trust; retiring the file is
`docs/backlog/medium/satellite-mdns-only-inventory.md`.

### When a deploy fails

| Symptom | Cause |
|---|---|
| `deploy` job **skipped** on a push | An upstream job skipped and the condition lost its `always() &&` lead — check `ci.yml`'s `deploy:` job condition. |
| `Preflight` fails | `~/code/alfred-deploy/.env` is missing a required value — the doctor table names the row. |
| `Verify`'s `health` check fails, everything else unrun | The container did not come up within 300s; `docker logs alfred`. |
| Job is red but `Roll back` ran and verified | The new build was broken; the box is back on the previous image. Fix the code and merge again. |
| Job is red and the rollback did NOT verify | Alfred may be down. SSH in, `docker ps -a` and `docker logs alfred` directly — don't wait for another deploy to notice. |
| A new `alfred-deploy_*` volume appeared | The `name: alfred` pin was removed from `docker-compose.yml`. Restore it before anything else — the running stack is on empty volumes. |
| Local `docker-compose.yml` edits keep disappearing | Every deploy overwrites it from the checkout. Use `docker-compose.override.yml` instead. |
| Satellite rollout fails with one device red | Read the per-device table; the failed device was rolled back to `/opt/alfred-satellite.prev`. The rest of the fleet did deploy — a partial rollout is a failure, never a pass. |
| Satellite `deploy` job **skipped** on a PR | Expected and correct — the job only runs on `push` to `master`. This is the same "a PR can never reach this house" property alfred has; confirmed live on `alfred-satellite#3`'s own PR. |
| A satellite device's own rollback failed too | `/opt/alfred-satellite.prev` may still hold the last good tree. SSH in as `anirudhlath` and restore by hand: `sudo rm -rf /opt/alfred-satellite && sudo mv /opt/alfred-satellite.prev /opt/alfred-satellite && sudo systemctl restart wyoming-satellite wyoming-openwakeword`. |

A failed alfred deploy's outage floor is roughly 10–12 minutes even in the best case: up to
300s of `/health` polling before the rollback starts, then up to another 300s verifying the
rollback. Tracked as `docs/backlog/medium/deploy-outage-floor-timeout.md`.
