# Continuous Deployment to the Local Runner — Design

**Date:** 2026-08-18
**Status:** Approved
**Scope:** `alfred`, `alfred-home-service`, `alfred-satellite`
**Driver:** Every merge to the trunk should reach the house without a human running a
command. `ha-home-panel` already does this for the wall tablet; this design extends the
same pattern to Alfred itself.

## Decisions (settled during brainstorming)

1. **Deploy targets are `alfred` and `alfred-satellite`.** `alfred-home-service` is baked
   into the fat image and has no deployment of its own — its merges trigger an alfred
   redeploy.
2. **One self-hosted runner per repo**, because GitHub offers no org-level runners for
   personal accounts.
3. **Build on the runner** (`alfredctl build` → `docker compose up -d`). Pushing images to
   a registry is deferred to a ticket.
4. **Smoke check with automatic rollback** — a failed deploy restores the previous image.
5. **Landing `feat/containerization` is Phase 0**, not a separate spec: its design already
   exists as `2026-07-19-alfred-containerization-design.md` on the trunk.
6. **Deploy on every merge to the trunk**, not on release tags.
7. **Satellite inventory is a file on the runner plus mDNS discovery**, aiming to retire
   the file in favour of discovery alone.
8. **Secrets are a pre-placed `.env` on the box.** Rendering it from GitHub Secrets is
   deferred to a ticket.

## 1 · Why this shape

`ha-home-panel` establishes the pattern this design copies deliberately:

> Checks run on GitHub's runners; only `deploy` touches this house. […] A pull-request
> build must never be able to write into HA's `www/`, and the surest way to guarantee
> that is for it to run on a machine where that directory does not exist.

The same reasoning applies with more force here. A PR build that could reach lath-server
could restart the assistant that runs the apartment. So: every check runs on
`ubuntu-latest`, exactly one job per repo runs on a self-hosted runner, and that job
cannot start until the aggregate gate has passed **and** the commit is already on the
trunk.

## 2 · Scope, and what is excluded

| Repo | Deploys? | Why |
|---|---|---|
| `alfred` | Yes — the whole stack, one fat container on lath-server | The main event |
| `alfred-home-service` | Indirectly — dispatches an alfred redeploy | Baked into the fat image; no standalone artifact |
| `alfred-satellite` | Yes — pushed to every Pi | Separate devices, separate mechanism |
| `home-assistant` | No | Dev fixture of simulated template entities, explicitly *not* the live-apartment instance; no GitHub remote |
| `signal-bridge` | No | Scaffold; no GitHub remote |
| `alfred-ios` | No | Needs a macOS runner and a TestFlight pipeline — separate project |

## 3 · Phase 0 — land `feat/containerization`

The deploy step calls `alfredctl`, which does not exist on the trunk. The implementation
does exist: 41 commits, +7,258/−484 across 88 files, in a **locked local worktree that was
never pushed** (`worktree-feat+containerization`). Its design spec
(`2026-07-19-alfred-containerization-design.md`) and its operator guide
(`docs/containerization.md`, 443 lines) already describe the production path:

```bash
uv run alfredctl build --tag alfred:latest
ALFRED_SECRETS_PASSPHRASE=… docker compose up -d
```

Phase 0 is therefore a landing task, not a design task: unlock the worktree, push the
branch as `feat/containerization`, merge `origin/master` into it (12 commits of drift,
predominantly dependabot), open a PR with a conventional title, get `ci-ok` green,
squash-merge.

**Why this is a blocker and not a nice-to-have.** The trunk's current
`docker-compose.yml` starts redis, mosquitto, `bus`, `core.reflex` and `home-service` —
but not `core.conscious`, `core.triggers`, `core.channels` or `core.librarian`. Deploying
it would ship an Alfred with no System 2, no trigger engine and no web UI. There is no
"fully deployed" on the trunk today.

## 4 · Phase 1 — runner infrastructure on lath-server

lath-server already runs one runner (`linux-server`, labels `self-hosted, Linux, X64,
home-panel`, registered to `ha-home-panel`). It stays untouched. Two more join it:

| Install dir | Registered to | Label |
|---|---|---|
| `~/actions-runner-alfred` | `anirudhlath/alfred` | `alfred-deploy` |
| `~/actions-runner-satellite` | `anirudhlath/alfred-satellite` | `alfred-satellite` |

Each is installed as a service (`./svc.sh install && ./svc.sh start`) so it survives
reboots.

**Deploy workspace.** `~/alfred-deploy/` holds the operator-managed state that no workflow
ever writes:

```
~/alfred-deploy/
  .env                  # HA_TOKEN, CLAUDE_API_KEY, OLLAMA_HOST, … (0600)
  passphrase            # ALFRED_SECRETS_PASSPHRASE (0600)
  satellites.yaml       # Pi inventory: name, host, port, area
  id_ed25519_satellites # SSH key trusted by every Pi (0600)
```

The alfred deploy job runs `docker compose` from this directory against a compose file
copied from the checkout, so the container's `env_file` and volumes are stable across
deploys and independent of the runner's ephemeral workspace.

**Preflight.** lath-server was unreachable from the design machine (`No route to host` on
192.168.50.158), so nothing about its current state is verified. Phase 1 begins by
confirming `docker`, `uv`, Python 3.13, `git`, `rsync` and `avahi-browse` are present and
that the existing `home-panel` runner is healthy, before anything is installed.

## 5 · Phase 2 — the alfred deploy job

### 5.1 Two gate bugs to fix first

`alfred`'s `ci.yml` aggregate job has the id `ci-ok`. A deploy job referencing
`needs.ci-ok.result` will not evaluate — hyphens parse as subtraction in GitHub
expressions. `ha-home-panel` solved this by giving the job the id `gate` and the display
name `ci-ok`, so the required-check setting (which matches the *name*) is unchanged.
Adopt the same fix rather than the `needs['ci-ok'].result` index workaround, so the two
repos read alike.

Second, the deploy job's condition must lead with `always() &&`. Without it the job
inherits the implicit `success()` check, and a **skipped** job anywhere upstream
propagates down the needs graph even when the gate itself succeeded — a deploy that
silently skips on every push. This cost `ha-home-panel` a run to diagnose; it is
documented in that repo's `ci.yml` and is not rediscovered here.

### 5.2 Triggers

`ci.yml` gains `repository_dispatch: types: [home-service-merged]` alongside its existing
`push` and `pull_request` triggers.

### 5.3 The job

```yaml
deploy:
  name: deploy to lath-server
  needs: [gate]
  if: >-
    ${{ always()
    && needs.gate.result == 'success'
    && ((github.event_name == 'push' && github.ref == 'refs/heads/master')
        || github.event_name == 'repository_dispatch') }}
  runs-on: [self-hosted, linux, alfred-deploy]
  concurrency:
    group: deploy-alfred
    cancel-in-progress: false
```

Never cancel a deploy in flight: it may be midway between building an image and starting
the container it replaces.

### 5.4 Steps

1. **Check out `alfred`, and `alfred-home-service` as a sibling directory.**
   `staging.workspace_root()` resolves sibling repos from `git rev-parse
   --git-common-dir`, and the fat image build stages `git ls-files` from *both* repos.
   A checkout nested inside the alfred tree will not be found. `alfred-home-service` is
   public, so the default token suffices.
2. **Set up Python** — `uv venv --python 3.13 && uv sync`. Worktrees and fresh checkouts
   default to system Python, which may be 3.14.
3. **Record the current image** — `docker image inspect -f '{{.Id}}' alfred:latest`,
   saved as the rollback target. On a first deploy there is no such image; the job
   records "none" and, if a later step fails, fails red without attempting a rollback
   rather than pretending one happened.
4. **Build** — `uv run alfredctl build --tag alfred:latest`, then tag the same image
   `alfred:${{ github.sha }}` so history is addressable.
5. **Start** — `docker compose up -d` from `~/alfred-deploy/`.
6. **Verify** — `uv run alfredctl smoke --attach --name alfred`, which polls `/health`
   then checks redis, RediSearch modules and mosquitto *inside the running container*.
7. **Roll back on any failure of 5 or 6** — retag the recorded image id back to
   `alfred:latest`, `docker compose up -d`, re-run the smoke check, and fail the job red
   regardless of whether the rollback succeeded. A rollback that itself fails is louder,
   not quieter.

### 5.5 A gap this phase must close

`alfredctl smoke --attach` execs into `rt.container_name()`, which is
`alfred-<branch-slug>` — `alfred-master` on the trunk. Compose names the container
`alfred-alfred-1`. Left alone, the post-deploy verification would exec into a container
that does not exist.

Fix: add a `--name` option to `alfredctl smoke` (defaulting to `rt.container_name()`, so
existing behaviour is unchanged) and pin `container_name: alfred` in `docker-compose.yml`.
The deploy passes `--name alfred`. Unit tests go alongside the branch's existing
`tests/alfredctl/test_smoke.py`.

## 6 · Phase 3 — home-service dispatches an alfred redeploy

`home-service` gets the same `gate`/`ci-ok` id fix, then one new job:

```yaml
dispatch:
  needs: [gate]
  if: >-
    ${{ always() && needs.gate.result == 'success'
    && github.event_name == 'push' && github.ref == 'refs/heads/main' }}
  runs-on: ubuntu-latest
```

It posts `event_type: home-service-merged` to
`repos/anirudhlath/alfred/dispatches` using `ALFRED_DISPATCH_TOKEN` — a fine-grained PAT
scoped to `contents: write` on `anirudhlath/alfred` alone. The alfred deploy then runs on
alfred's runner, checking out both repos at their current trunks, which is exactly what
the fat image build needs.

No third runner, and no duplicated deploy logic. The cost is one PAT, whose scope is
narrow enough that its worst case is dispatching a deploy of code that is already on the
trunk.

## 7 · Phase 4 — satellite deployment

`alfred-satellite` has no workflows at all today, so CI comes first.

### 7.1 CI

`ubuntu-latest` jobs: `shellcheck` over `scripts/`, `systemd-analyze verify` on the two
unit files, a parse check that `config.env.example` defines every variable
`scripts/setup.sh` reads, and the conventional-PR-title check the other repos already
run (the workspace requires it everywhere, since the PR title becomes the squash commit).
Aggregated by the same `gate`/`ci-ok` job, which becomes the repo's required check.

### 7.2 Deploy logic lives in a tested script

The job is thin glue around `dev/deploy_satellites.py` — typer + rich CLI, loguru
logging, `mypy --strict`, pytest-covered, per the workspace Python conventions. This
mirrors `ha-home-panel`, where the workflow's final step is `node dev/deploy.mjs
.staging` and every real decision lives in reviewable, runnable code.

### 7.3 Inventory: file ∪ mDNS

The canonical inventory (`~/alfred-deploy/satellites.yaml`) carries `name`, `host`,
`port` and `area` — the same shape as `config/satellites.yaml.example` in this repo,
whose real counterpart is gitignored. Discovery runs `avahi-browse -rpt _wyoming._tcp`
and the two are merged:

- Present in both → deploy, using the file's `name`/`area`.
- Discovered only → deploy, and warn that it is missing from the inventory.
- In the file only, no mDNS answer → attempt the deploy at its recorded address anyway
  (a Pi may not be advertising), and warn.

Both sources exist because the file is authoritative today while discovery earns trust.
The intended end state is discovery alone; retiring the file is a ticket, not a TODO.

### 7.4 Per-device deploy

For each device, over SSH with the runner's key:

1. `rsync` the checkout to `/opt/alfred-satellite`, moving the previous tree to
   `/opt/alfred-satellite.prev` first.
2. Run `sudo scripts/setup.sh`.
3. Restart `wyoming-satellite.service` and `wyoming-openwakeword.service`.
4. Verify both report `systemctl is-active`, and that the Wyoming port accepts a TCP
   connection.
5. On any failure: restore `.prev`, restart, and record the device as failed.

**Every device is attempted even after one fails.** A single unreachable Pi must not
leave the rest of the house on an old build. A device that cannot be reached at all counts
as failed, exactly like one whose deploy broke. The job ends by printing a per-device
table and exiting non-zero if any device failed — a partial deploy is reported as a
failure, never as a pass.

`--dry-run` prints the planned actions per device and touches nothing, so the script is
runnable by hand before it is trusted in CI.

### 7.5 Testability

SSH and rsync sit behind a small transport interface. That leaves inventory merging,
rollback decisions and result aggregation as pure functions with unit tests, and confines
the untestable part to one thin adapter.

## 8 · Error handling and safety properties

- **A PR can never deploy.** Deploy jobs are gated on `push`/`repository_dispatch` and on
  the aggregate gate, and run on runners no PR job targets.
- **Concurrent deploys queue, never cancel** (`cancel-in-progress: false`).
- **A skipped upstream job cannot masquerade as success** — `alfred`'s gate already
  treats `skipped` as failure, and the deploy asserts `needs.gate.result == 'success'`
  explicitly rather than relying on implicit `success()`.
- **Rollback is verified, not assumed** — the smoke check re-runs after a rollback.
- **Secrets never enter a workflow.** `.env` and the passphrase live on the box; the
  workflows reference them only by path.
- **Failure is loud.** A failed deploy or a partially-failed satellite rollout fails the
  job red; nothing is downgraded to a warning.

## 9 · Testing strategy

Workflow YAML cannot be meaningfully unit-tested, so the design keeps the workflows thin
and puts every real decision in code that can be:

| Component | How it is tested |
|---|---|
| `alfredctl smoke --name` | pytest, alongside existing `tests/alfredctl/test_smoke.py` |
| `dev/deploy_satellites.py` | pytest — inventory merge, rollback decision, aggregation |
| Deploy/rollback end to end | QA backlog — only a real deploy proves it |
| Satellite rollout with a Pi offline | QA backlog |

QA-backlog items (per the workspace convention, deleted once verified):

- `first-live-deploy-lath-server.md` — merge a trivial PR, confirm the house updates.
- `deploy-rollback-drill.md` — deliberately ship a broken image, confirm automatic
  restoration and a red job.
- `satellite-deploy-with-offline-pi.md` — power one Pi off, confirm the others still
  deploy and the job fails with an accurate table.

## 10 · Documentation

- **New:** `docs/deployment.md` in `alfred` — runner installation runbook, what a deploy
  does step by step, how to roll back by hand, how to rotate secrets, how to add a
  satellite.
- **Updated:** workspace `CLAUDE.md` gains a CD section; `alfred/CLAUDE.md` and
  `home-service/CLAUDE.md` note that merges deploy.

## 11 · Deferred work (tickets, not TODOs)

| Ticket | Why deferred |
|---|---|
| `docs/backlog/low/registry-publish-images.md` (**exists** on the containerization branch — extend to cover the CD path: build and push on a GitHub runner, pull a digest on the box) | Building on the runner works and needs no registry auth |
| `docs/backlog/medium/deploy-env-from-github-secrets.md` (**new**) — render `.env` from repo secrets instead of a hand-managed file | A pre-placed file is the smallest thing that works; rotation via SSH is tolerable for now |
| `docs/backlog/medium/satellite-mdns-only-inventory.md` (**new**) — retire `satellites.yaml` once discovery is trusted | Discovery must prove itself against a known-good inventory first |

## 12 · Build sequence

| Phase | Deliverable | Depends on |
|---|---|---|
| 0 | `feat/containerization` merged to `master` | — |
| 1 | Two runner services + `~/alfred-deploy/` on lath-server | 0 (nothing to deploy before it) |
| 2 | `alfredctl smoke --name`, pinned `container_name`, gate fix, alfred deploy job | 0, 1 |
| 3 | home-service gate fix + dispatch job, PAT | 2 |
| 4 | satellite CI, `deploy_satellites.py`, satellite deploy job | 1 |
| 5 | `docs/deployment.md`, CLAUDE.md updates, backlog + QA-backlog tickets | 2–4 |

Phases 2 and 4 are independent once Phase 1 lands and can proceed in parallel.
