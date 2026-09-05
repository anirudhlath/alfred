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
uv run alfredctl smoke --port 8082 # publish somewhere else, so the run can coexist with an
                                   # Alfred already holding 8081 (the case on a deploy host)
```

`--port` applies only to the container smoke starts itself. With `--attach` the port is read
off the running container (`docker port <name> 8081`) rather than assumed, and smoke refuses
to run if it cannot be determined — assuming 8081 meant that on a host already serving Alfred
there, the HTTP checks probed *that* container while the `docker exec` checks ran against the
named one, producing a single report describing two different containers.

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

**The common case needs no configuration** — browse to the host's LAN IP on port 8081
from another device and register a passkey. To widen or narrow the trusted set:

- `ALFRED_TRUSTED_NETWORKS=203.0.113.0/24,…` — extra ranges, comma-separated.
- `ALFRED_TRUSTED_NETWORKS_STRICT=1` — trust **only** loopback, Tailscale and the CIDRs
  you list. Required for anything internet-facing; see "Behind a reverse proxy" below.

It works out of the box because Alfred trusts **loopback, private LAN (RFC1918), and
Tailscale** by default, so localhost, the Docker bridge and your home network all pass.
The gate covers only the endpoints that can mint or widen credentials — WebAuthn passkey
**registration**, credential writes (`PUT`/`DELETE /api/integrations/{name}/credentials`),
push device tokens (`POST`/`DELETE /api/devices/register`) and voice enrolment
(`POST /api/voice/enroll`). Everything else, including the admin API's reads and controls,
needs only a signed-in passkey session.

A rejected request returns
`403 Access restricted to trusted networks: <ip> is not trusted.` — always the peer the
*process* saw, which is exactly the number you need when a proxy is in the path. The hint
on how to allow it is appended only for a caller who already holds a session; see
[`secrets.md` → Security](secrets.md#security) for why.

## Behind a reverse proxy

When a proxy (nginx-proxy-manager, Caddy, Cloudflare in front of either) terminates TLS,
the process sees the *proxy* as the peer. Until `FORWARDED_ALLOW_IPS` names that peer,
uvicorn ignores `X-Forwarded-For` / `X-Forwarded-Proto` and the trusted-network gate,
`Secure` cookies and the 403 detail all judge the proxy instead of the browser. Run these
steps in order — 2 before 3 is load-bearing, and 4 before 6.

### 1. Pick the public hostname — once

It becomes the WebAuthn RP ID: `_get_rp_id()` derives it from the request's `Host`, and
passkeys are cryptographically bound to it. Renaming the host later orphans every
registered passkey, and there is no migration.

### 2. Lock the trusted set *before* exposing anything

Set `ALFRED_TRUSTED_NETWORKS_STRICT=1` and put your LAN CIDR(s) **and the proxy's own
address** in `ALFRED_TRUSTED_NETWORKS`. Three reasons this comes first:

- The permissive default trusts all of RFC1918 — i.e. any network a caller happens to be
  on, the proxy's own included.
- Strict mode is also what stops `alfredctl up` from auto-appending the container subnet
  (`172.16.0.0/12` on Docker, see [`containerization.md` §7](containerization.md)). Under
  strict mode the proxy's address is therefore *not* trusted unless you list it.
- It is the only hard stop for a mis-set `FORWARDED_ALLOW_IPS`, which otherwise fails
  **open**: with the headers unrewritten the gate judges the proxy's own RFC1918 address
  and every internet caller sails through.

### 3. Discover the proxy's peer address

Make one request through the proxy to a gated endpoint and read the IP out of the 403:

```bash
curl -sS -X POST https://alfred.example.com/api/auth/register/begin \
  -H 'content-type: application/json' -d '{"device_name":"probe"}'
# {"detail":"Access restricted to trusted networks: 203.0.113.9 is not trusted."}
```

Step 2 has to be done already — without strict mode the proxy's RFC1918 address is
trusted, the probe returns 200, and you learn nothing.

### 4. Set `FORWARDED_ALLOW_IPS` to that one address, then restart

uvicorn reads it once at process start, so this needs a restart, not a reload.

- **One hop, not a CDN's range list.** Put the proxy's own address there — never
  Cloudflare's published ranges. uvicorn walks `X-Forwarded-For` from the right and takes
  the first *untrusted* hop as the client, so trusting the edge's ranges would hand that
  decision to whatever the edge appended.
- **A literal address or a proper network.** `203.0.113.9` or `172.16.0.0/12` — never a
  CIDR with host bits set like `172.18.0.5/16`. uvicorn parses with
  `ipaddress.ip_network(host)` (strict), and on `ValueError` silently keeps the string as
  a *literal* that no IP peer can ever equal. Alfred's own startup validator uses
  `strict=False`, so it will **not** warn you about that one.

### 5. Configure the proxy

It must send a **single** `X-Forwarded-For` holding the real client, `X-Forwarded-Proto:
https` (this is what makes the `alfred_auth` cookie `Secure`), and the **original `Host`**
— a rewritten `Host` changes the RP ID and breaks passkey registration and login.

*nginx-proxy-manager* — turn **Websockets Support** on for the proxy host, or `/ws` and
`/ws/telemetry` never upgrade. `Host` is passed through by default
(`proxy_set_header Host $host;`). Custom directives go in the proxy host's **Advanced**
tab (Custom Nginx Configuration); behind Cloudflare add:

```nginx
set_real_ip_from <cloudflare range>;   # one line per published range
real_ip_header CF-Connecting-IP;
```

so `$remote_addr` is the visitor before nginx writes `X-Forwarded-For`.

*Caddy*, same Cloudflare case:

```caddyfile
alfred.example.com {
    trusted_proxies static <cloudflare ranges>
    client_ip_headers CF-Connecting-IP
    reverse_proxy alfred:8081
}
```

### 6. Verify

- **Boot log** names what you set: `Trusting X-Forwarded-* headers from: 203.0.113.9`.
- **DevTools → Application → Cookies**: `alfred_auth` shows **Secure** — that proves
  `X-Forwarded-Proto` is being honoured, not just forwarded.
- **A 403 from outside the trusted list names the browser's real IP**, not the proxy's.
  If it still names the proxy, step 4 did not take.

Also expect idle WebSocket drops for now: the server answers `{"type":"ping"}` with
`{"type":"pong"}` on `/ws` and `/ws/telemetry`, but no client in this repo sends them yet,
so sockets will drop at the proxy's idle timeout (~100 s on Cloudflare) until one does.

### 7. Optionally close the direct path

If the proxy shares Alfred's Docker network it can reach the container on 8081 with no
published port at all, so the host publish can be narrowed to loopback — leaving the proxy
as the only route in. The shipped `docker-compose.yml` publishes on every interface on
purpose (LAN clients use 8081 directly today) **and is overwritten on every deploy**, so
this belongs in a sibling `docker-compose.override.yml` — see
[The deploy workspace](#the-deploy-workspace):

```yaml
services:
  alfred:
    ports: !override
      - "127.0.0.1:8081:8081"
```

`!override` needs Compose ≥ 2.24. A plain `ports:` in an override file *merges* — it
appends the new mapping and leaves the original all-interfaces binding in place, which is
the opposite of what you wanted.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `alfredctl doctor` shows System 2 ✗ | `OPENROUTER_API_KEY` missing/placeholder — set it in `.env`. |
| Memory recall disabled in logs | You set a gated `EMBEDDING_MODEL` without `HF_TOKEN`. Use the ungated default or provide the token + accept the license. |
| `memory refuses to run` or `episodic recall refuses to run` in logs, naming two different `dim=` values | You changed `EMBEDDING_MODEL` or `EMBEDDING_BACKEND`, so the new vector width no longer matches the index the store was built at; both stores latch and refuse rather than write vectors search can never match. **Quickest undo:** put the previous model/backend back and restart — nothing has been lost. **To go forward:** follow the recovery in the error text itself; it is per-store, tells you whether Alfred has to stop, and one wrong flag (`FT.DROPINDEX … DD`) permanently deletes every episodic memory not yet decayed to cold. Expect to do it twice — hot (Redis) and cold (SQLite) were both built at the old width. Check widths *before* changing models with `docker exec <alfred-container> redis-cli FT.INFO idx:context`. |
| `dimension guard skipped, an embedding model change will NOT be caught here` in logs | The guard could not read the stored width (an `FT.INFO` reply it cannot parse, or vec0 DDL that does not match the expected shape), so it stepped aside rather than guess. A model change will now surface as a raw store error instead of the actionable message above. Check the width by hand — `docker exec <alfred-container> redis-cli FT.INFO idx:context` — before changing `EMBEDDING_MODEL`/`EMBEDDING_BACKEND`. |
| Recall stays empty after that recovery; `FT.INFO idx:context` shows `hash_indexing_failures` climbing | Expected, and not a second fault: dropping the index keeps every `ctx:*` hash but nothing re-embeds the old-width entries. Semantic entries return on the Librarian's next reindex, routines on restart; episodic ones have no re-embed path, so only new writes become searchable. |
| 403 registering a passkey | Your client IP isn't trusted — add its subnet to `ALFRED_TRUSTED_NETWORKS` (the 403 message names the IP). |
| `403 Access restricted to trusted networks: <ip> is not trusted.` naming the proxy's or Docker's address, from a device that *is* on the LAN or tailnet | `FORWARDED_ALLOW_IPS` does not include the proxy, so `X-Forwarded-For` was never trusted and the gate is judging the proxy's own address. Put the address the 403 names into `FORWARDED_ALLOW_IPS` and restart. Without strict mode you get the *silent* version of this instead — the proxy's RFC1918 address is trusted by default, so every internet caller passes the gate. |
| Boot log warns about `FORWARDED_ALLOW_IPS` | Two distinct warnings. **`…is the loopback default…`** — unset or blank, so uvicorn trusts loopback only and `X-Forwarded-*` is ignored; harmless with no proxy, otherwise set it to the proxy's address. **`…entry '…' is not a valid IP or CIDR…`** — a hostname, a typo, or a `*` inside a comma-separated list (uvicorn only wildcards when the *whole* value is `*`); it becomes a literal no IP peer can match. Neither warning fires for a CIDR with host bits set (`172.18.0.5/16`) — Alfred validates with `strict=False` while uvicorn parses strictly, so that one is silently a never-matching literal. Use a bare address or a proper network. |
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
