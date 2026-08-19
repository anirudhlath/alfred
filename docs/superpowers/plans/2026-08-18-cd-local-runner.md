# Continuous Deployment to the Local Runner — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every merge to the trunk of `alfred`, `alfred-home-service` or `alfred-satellite` reaches the house automatically — alfred rebuilt and restarted on lath-server with verified rollback, satellites rsynced to every Pi.

**Architecture:** Checks run on `ubuntu-latest`; exactly one job per repo runs on a self-hosted runner, gated on the aggregate check and on the commit already being on the trunk. `alfred-home-service` has no deploy of its own — it fires a `repository_dispatch` at `alfred`. Workflows stay thin; every real decision lives in tested Python (`alfredctl`, `dev/deploy_satellites.py`).

**Tech Stack:** GitHub Actions (self-hosted runners), Docker Compose, `alfredctl` (typer + rich), Python 3.13 via uv, pytest, `mypy --strict`, ruff (line-length 100), rsync + SSH + avahi for satellites.

**Source spec:** `docs/superpowers/specs/2026-08-18-cd-local-runner-design.md`

---

## Preflight results (already run — 2026-08-18)

The spec's §4 said lath-server was unreachable. **It is this box.** `192.168.50.158` is
`enp6s0`'s own address; hostname `linux-server`. The design machine was pinging itself.
Everything §4 asked to confirm has now been confirmed directly:

| Check | Result |
|---|---|
| `docker` | 29.6.2 ✓ |
| `uv` | 0.11.31 ✓ |
| `git` | 2.55.0 ✓ |
| `rsync` | 3.4.4 ✓ |
| `avahi-browse` | 0.9-rc5 ✓ |
| Python 3.13 | **absent** — system is 3.14.6. `uv` downloads a managed 3.13 on demand; every uv invocation below pins `--python 3.13`. |
| `home-panel` runner | `actions.runner.anirudhlath-ha-home-panel.linux-server.service` — **active (running)** ✓ |

### Four corrections to the spec, settled before planning

1. **Runner install path.** The existing runner lives at
   `~/.local/share/github-runner/ha-home-panel/`, not `~/actions-runner-*`. New runners
   follow the established convention: `~/.local/share/github-runner/<repo>/`.

2. **Deploy workspace is `~/code/alfred-deploy/`** (user decision), not a new
   `~/alfred-deploy/`. That directory already exists and already *is* the manual-deploy
   workspace: it holds the live `alfred` checkout, its `0600` `.env`, the `home-service`
   sibling, and `backups/`. A second similarly-named directory would shadow it.

3. **The compose project name must be pinned.** The live container `alfred-alfred-1`
   belongs to compose project `alfred` (derived from the directory name `alfred`) and owns
   volumes `alfred_alfred_data` / `alfred_alfred_models` — which hold the secrets
   passphrase persisted on first boot (#158). Running compose from `~/code/alfred-deploy/`
   would derive the project name `alfred-deploy`, create **empty** volumes, lose the
   passphrase, and collide on `:8081` with the still-running container. Pinning
   `name: alfred` in the compose file makes the project name directory-independent, so the
   existing volumes are adopted and the running container is replaced rather than
   duplicated. This is Task 8 and it is load-bearing.

4. **`alfredctl doctor` cannot target an arbitrary `.env`.** It reads
   `staging.repo_root() / ".env"` (`alfredctl/main.py:79`) with no override, so §5.4 step 3
   is unimplementable as written. Fixed by adding `--env-file` (Task 7), mirroring the
   `--name` fix the spec already planned for `smoke`.

---

## File structure

**`alfred` repo**

| File | Responsibility |
|---|---|
| `alfredctl/main.py` (modify) | Add `--name` to `smoke`, `--env-file` to `doctor`. Pure CLI plumbing — both underlying `run_checks` functions already take the value as a parameter. |
| `docker-compose.yml` (modify) | Pin `name: alfred` (project) and `container_name: alfred` (container). |
| `tests/alfredctl/test_main_helpers.py` (modify) | CLI-layer tests for both new options. |
| `.github/workflows/ci.yml` (modify) | Rename job id `ci-ok` → `gate` keeping display name `ci-ok`; add `repository_dispatch` trigger; add the `deploy` job. |
| `docs/deployment.md` (modify) | CD section: runner runbook, what a deploy does, manual rollback, secret rotation, adding a satellite. |
| `docs/backlog/{low,medium}/*.md` (1 modify, 2 create) | Deferred work from §11. |
| `docs/qa-backlog/*.md` (create ×3) | The three drills from §9. |

**`alfred-home-service` repo**

| File | Responsibility |
|---|---|
| `.github/workflows/ci.yml` (modify) | Same `gate`/`ci-ok` id fix; add the `dispatch` job. |

**`alfred-satellite` repo**

| File | Responsibility |
|---|---|
| `pyproject.toml` (create) | uv project: typer, rich, loguru, PyYAML; ruff line-length 100; `mypy --strict`. |
| `dev/satellites/inventory.py` (create) | `Satellite`, YAML loading, `avahi-browse` output parsing, file ∪ mDNS merge. Pure. |
| `dev/satellites/transport.py` (create) | `Transport` protocol; `SshTransport` (ssh/rsync/TCP probe); `DryRunTransport`. The only untested layer. |
| `dev/satellites/deploy.py` (create) | `DeviceResult`, `deploy_one` (rsync → setup.sh → restart → verify → rollback), `exit_code`. Pure given a transport. |
| `dev/deploy_satellites.py` (create) | typer + rich CLI wiring the three together. |
| `tests/test_inventory.py`, `tests/test_deploy.py` (create) | pytest for the pure layers. |
| `.github/workflows/ci.yml` (create) | shellcheck, `systemd-analyze verify`, config-parity check, PR-title check, `gate`/`ci-ok`, and the satellite `deploy` job. |

---

## Phase ordering

Per spec §12. Phase 1 is operator work on the box (no PR). Phases 2a/2b/3/5 are PRs in
`alfred` / `alfred-home-service`; Phase 4 is PRs in `alfred-satellite`.

| Phase | Deliverable | Depends on |
|---|---|---|
| 1 | Two runner services + `~/code/alfred-deploy/` workspace | — |
| 2a | `smoke --name`, `doctor --env-file`, pinned compose names | — (mergeable before runners exist) |
| 2b | Gate fix + `repository_dispatch` + alfred deploy job | 1, 2a |
| 3 | home-service gate fix + dispatch job + PAT | 2b |
| 4 | Satellite CI, `deploy_satellites.py`, satellite deploy job | 1 |
| 5 | Docs, backlog and QA-backlog tickets | 2b–4 |

Phases 2 and 4 are independent once Phase 1 lands.

---

# Phase 1 — Runner infrastructure on lath-server

**No PR.** This is operator work on the box, run from a normal shell as `anirudhlath`.
The user's interactive shell is **fish** — the commands below are written for fish.

### Task 1: Migrate the deploy workspace to `~/code/alfred-deploy/`

Today `.env` sits inside the checkout (`~/code/alfred-deploy/alfred/.env`) and compose
runs from the checkout. CD needs both to live one level up, in a stable directory no
workflow ever rewrites, so the runner's ephemeral checkout is not the source of truth.

**Files:**
- Create: `~/code/alfred-deploy/.env` (0600, moved)
- Create: `~/code/alfred-deploy/docker-compose.yml` (copied each deploy)
- Create: `~/code/alfred-deploy/satellites.yaml`

- [ ] **Step 1: Confirm the live deployment before touching it**

```fish
docker inspect alfred-alfred-1 --format '{{index .Config.Labels "com.docker.compose.project"}} {{.State.Health.Status}}'
docker volume ls --format '{{.Name}}' | grep alfred
```

Expected: `alfred healthy`, and the three volumes `alfred_alfred_data`,
`alfred_alfred_models`, `alfred_redis_data`. Record them — Task 8 depends on the project
name being exactly `alfred`.

- [ ] **Step 2: Copy `.env` up one level, keeping the original**

The checkout copy stays so manual `alfredctl doctor` from inside the checkout keeps
working until Phase 5 documents the new location.

```fish
cp -p ~/code/alfred-deploy/alfred/.env ~/code/alfred-deploy/.env
chmod 600 ~/code/alfred-deploy/.env
ls -l ~/code/alfred-deploy/.env
```

Expected: `-rw------- 1 anirudhlath anirudhlath ... /home/anirudhlath/code/alfred-deploy/.env`

- [ ] **Step 3: Seed the satellite inventory**

```fish
cp ~/code/alfred-deploy/alfred/config/satellites.yaml.example ~/code/alfred-deploy/satellites.yaml
```

Then edit `~/code/alfred-deploy/satellites.yaml` so each entry reflects a real Pi. The
schema is exactly the example's:

```yaml
satellites:
  - name: kitchen
    host: 192.168.1.40
    port: 10700
    area: Kitchen
```

- [ ] **Step 4: Generate the satellite SSH key**

```fish
ssh-keygen -t ed25519 -N "" -C "alfred-satellite-deploy" -f ~/code/alfred-deploy/id_ed25519_satellites
chmod 600 ~/code/alfred-deploy/id_ed25519_satellites
```

Then install the public half on every Pi listed in `satellites.yaml`:

```fish
for h in (grep 'host:' ~/code/alfred-deploy/satellites.yaml | string replace -r '.*host:\s*' '' | string replace -r '\s*#.*' '')
    ssh-copy-id -i ~/code/alfred-deploy/id_ed25519_satellites.pub $h
end
```

- [ ] **Step 5: Verify the workspace**

```fish
ls -la ~/code/alfred-deploy/
```

Expected: `.env` (0600), `id_ed25519_satellites` (0600), `satellites.yaml`, plus the
pre-existing `alfred/`, `home-service/`, `backups/`, `DEPLOY-FRICTION-LOG.md`.
`docker-compose.yml` is absent — the deploy job copies it in.

### Task 2: Install the `alfred` runner

**Files:**
- Create: `~/.local/share/github-runner/alfred/`

- [ ] **Step 1: Mirror the existing runner's layout**

```fish
systemctl cat actions.runner.anirudhlath-ha-home-panel.linux-server.service | grep -E 'ExecStart|WorkingDirectory|User'
```

Expected: `WorkingDirectory=/home/anirudhlath/.local/share/github-runner/ha-home-panel`.
The new runners go beside it.

- [ ] **Step 2: Download the runner**

Check the current release rather than hardcoding a version:

```fish
set -l ver (curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name | string trim -c v)
mkdir -p ~/.local/share/github-runner/alfred
cd ~/.local/share/github-runner/alfred
curl -fsSL -o runner.tar.gz "https://github.com/actions/runner/releases/download/v$ver/actions-runner-linux-x64-$ver.tar.gz"
tar xzf runner.tar.gz && rm runner.tar.gz
```

- [ ] **Step 3: Register it**

Get a registration token from
`https://github.com/anirudhlath/alfred/settings/actions/runners/new` (it expires in an
hour), then:

```fish
cd ~/.local/share/github-runner/alfred
./config.sh --url https://github.com/anirudhlath/alfred \
            --token <REGISTRATION_TOKEN> \
            --name lath-server-alfred \
            --labels alfred-deploy \
            --work _work \
            --unattended --replace
```

The `self-hosted`, `Linux` and `X64` labels are added automatically; `alfred-deploy` is the
one the workflow selects on.

- [ ] **Step 4: Install and start as a service**

```fish
cd ~/.local/share/github-runner/alfred
sudo ./svc.sh install anirudhlath
sudo ./svc.sh start
```

- [ ] **Step 5: Verify it is online**

```fish
systemctl is-active actions.runner.anirudhlath-alfred.lath-server-alfred.service
```

Expected: `active`. Also confirm the runner shows **Idle** at
`https://github.com/anirudhlath/alfred/settings/actions/runners`.

- [ ] **Step 6: Confirm the runner user can reach docker**

The deploy job runs `docker` as `anirudhlath` with no sudo.

```fish
docker ps > /dev/null; and echo "docker ok"
```

Expected: `docker ok`. If it fails, `sudo usermod -aG docker anirudhlath` and restart the
runner service.

### Task 3: Install the `alfred-satellite` runner

**Files:**
- Create: `~/.local/share/github-runner/alfred-satellite/`

- [ ] **Step 1: Download**

```fish
set -l ver (curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name | string trim -c v)
mkdir -p ~/.local/share/github-runner/alfred-satellite
cd ~/.local/share/github-runner/alfred-satellite
curl -fsSL -o runner.tar.gz "https://github.com/actions/runner/releases/download/v$ver/actions-runner-linux-x64-$ver.tar.gz"
tar xzf runner.tar.gz && rm runner.tar.gz
```

- [ ] **Step 2: Register**

Token from `https://github.com/anirudhlath/alfred-satellite/settings/actions/runners/new`:

```fish
cd ~/.local/share/github-runner/alfred-satellite
./config.sh --url https://github.com/anirudhlath/alfred-satellite \
            --token <REGISTRATION_TOKEN> \
            --name lath-server-satellite \
            --labels alfred-satellite \
            --work _work \
            --unattended --replace
```

- [ ] **Step 3: Install and start**

```fish
cd ~/.local/share/github-runner/alfred-satellite
sudo ./svc.sh install anirudhlath
sudo ./svc.sh start
systemctl is-active actions.runner.anirudhlath-alfred-satellite.lath-server-satellite.service
```

Expected: `active`.

- [ ] **Step 4: Confirm all three runners coexist**

```fish
systemctl list-units --all 'actions.runner*' --no-pager
```

Expected: three units, all `active running` — `ha-home-panel`, `alfred`, `alfred-satellite`.

### Task 4: Clone `alfred-satellite` for Phase 4

Phase 4 writes code in that repo and it is not checked out on this box.

- [ ] **Step 1: Clone into `~/code/`**

Per the workspace convention, clones live in `~/code/`.

```fish
git clone https://github.com/anirudhlath/alfred-satellite ~/code/alfred-satellite
cd ~/code/alfred-satellite; git log --oneline -3
```

- [ ] **Step 2: Inventory what Phase 4's CI must check**

```fish
cd ~/code/alfred-satellite
git rev-parse --abbrev-ref HEAD
ls scripts/ systemd/ 2>/dev/null; ls *.env.example config.env.example 2>/dev/null
ls .github/workflows 2>/dev/null; or echo "no workflows — as the spec expects"
```

Record the trunk branch name, the exact shell scripts under `scripts/`, the two unit file
paths, and the config example's path. **Phase 4's Task 19 (the CI workflow) is written
against `scripts/*.sh`, `systemd/*.service` and `config.env.example`; if this inventory
shows different paths, correct Task 19's globs to match before writing the workflow.**

---

# Phase 2a — `alfredctl` options + pinned compose names

**PR title:** `feat(alfredctl): target a named container for smoke and a chosen .env for doctor`

These are ordinary code changes gated by ordinary CI. They need no runner and can merge
before Phase 1 finishes.

Work in the worktree at
`~/code/alfred-deploy/alfred/worktrees/ci-local-runner-cd`.

### Task 5: Prepare the branch

- [ ] **Step 1: Branch from the fetched trunk**

The worktree currently sits on `ci/local-runner-cd`, which carries the spec and this plan.
The code change gets its own branch off `origin/master`.

```bash
cd ~/code/alfred-deploy/alfred
git fetch origin
git worktree add worktrees/feat-alfredctl-deploy-targets -b feat/alfredctl-deploy-targets origin/master
cd worktrees/feat-alfredctl-deploy-targets
```

- [ ] **Step 2: Create the venv on the right Python**

Worktrees default to system Python, which is 3.14 on this box and will break `uv sync`.

```bash
uv venv --python 3.13
uv sync --all-extras
```

- [ ] **Step 3: Confirm the baseline is green**

```bash
.venv/bin/python -m pytest tests/alfredctl -q
```

Expected: all pass.

### Task 6: `alfredctl smoke --name`

`smoke` builds a `LaunchPlan` with `name=rt.container_name()` — `alfred-<branch-slug>`,
so `alfred-master` on the trunk. Compose runs `alfred-alfred-1` today and `alfred` after
Task 8. Without an override, post-deploy verification execs into a container that does not
exist. `smoke_mod.run_checks(exe, name, base_url, ...)` already takes the name as a
parameter, so this is CLI plumbing only.

**Files:**
- Modify: `alfredctl/main.py:246-268` (the `smoke` command)
- Test: `tests/alfredctl/test_main_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/alfredctl/test_main_helpers.py`:

```python
def _capture_smoke_name(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Run main.smoke with every side effect stubbed; capture the container it targets."""
    seen: dict[str, str] = {}

    def _fake_run_checks(
        exe: str, name: str, base_url: str, timeout: float = 300.0, *, deep: bool = False
    ) -> list[smoke_mod.SmokeCheck]:
        seen["name"] = name
        return [smoke_mod.SmokeCheck("health", True, "GET /health → 200")]

    monkeypatch.setattr(main.rt, "detect", lambda runtime: Runtime("docker", "docker"))
    monkeypatch.setattr(main, "_resolve_url", lambda r, plan: "http://localhost:8081")
    monkeypatch.setattr(main.smoke_mod, "run_checks", _fake_run_checks)
    return seen


def test_smoke_name_option_targets_that_container(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_smoke_name(monkeypatch)
    main.smoke(attach=True, name="alfred")
    assert seen["name"] == "alfred"


def test_smoke_without_name_keeps_branch_container(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_smoke_name(monkeypatch)
    monkeypatch.setattr(main.rt, "container_name", lambda: "alfred-somebranch")
    main.smoke(attach=True)
    assert seen["name"] == "alfred-somebranch"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/alfredctl/test_main_helpers.py -k smoke_ -q`
Expected: FAIL — `TypeError: smoke() got an unexpected keyword argument 'name'`.

- [ ] **Step 3: Add the option**

In `alfredctl/main.py`, add a parameter to `smoke` after `attach`:

```python
    name: Annotated[
        str | None,
        typer.Option("--name", help="Container to check (default: alfred-<branch>)"),
    ] = None,
```

and change the `LaunchPlan` construction inside `smoke` from
`name=rt.container_name(),` to:

```python
        name=name or rt.container_name(),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/alfredctl/test_main_helpers.py -k smoke_ -q`
Expected: 2 passed.

- [ ] **Step 5: Confirm nothing else regressed**

```bash
.venv/bin/python -m pytest tests/alfredctl -q
.venv/bin/python -m ruff check alfredctl tests/alfredctl
.venv/bin/python -m mypy --strict alfredctl/
```

Expected: all pass, no ruff findings, no mypy errors.

- [ ] **Step 6: Commit**

```bash
git add alfredctl/main.py tests/alfredctl/test_main_helpers.py
git commit -m "feat(alfredctl): add smoke --name to check a container by name"
```

### Task 7: `alfredctl doctor --env-file`

`doctor` hardcodes `staging.repo_root() / ".env"`. The deploy job validates
`~/code/alfred-deploy/.env`, which is not inside the runner's checkout.
`doctor_mod.run_checks(env_file, *, online)` already takes the path, so again CLI plumbing.

**Files:**
- Modify: `alfredctl/main.py:72-84` (the `doctor` command)
- Test: `tests/alfredctl/test_main_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/alfredctl/test_main_helpers.py`:

```python
def _capture_doctor_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Run main.doctor with the real checks stubbed; capture the .env path it validates."""
    seen: dict[str, Path] = {}

    def _fake_run_checks(env_file: Path, *, online: bool = True) -> list[doctor_mod.DoctorCheck]:
        seen["env_file"] = env_file
        return [doctor_mod.DoctorCheck(".env", "pass", str(env_file))]

    monkeypatch.setattr(main.doctor_mod, "run_checks", _fake_run_checks)
    return seen


def test_doctor_env_file_option_overrides_repo_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _capture_doctor_env(monkeypatch)
    main.doctor(online=False, env_file=tmp_path / "deploy.env")
    assert seen["env_file"] == tmp_path / "deploy.env"


def test_doctor_without_env_file_uses_repo_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = _capture_doctor_env(monkeypatch)
    monkeypatch.setattr(main.staging, "repo_root", lambda: tmp_path)
    main.doctor(online=False)
    assert seen["env_file"] == tmp_path / ".env"
```

`Path` is currently imported in that file only under `TYPE_CHECKING`; these tests use it at
runtime as a `tmp_path` annotation only, which is fine because the module has
`from __future__ import annotations`. Add the `doctor` module to the existing import block
at the top of the file:

```python
from alfredctl import doctor as doctor_mod
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/alfredctl/test_main_helpers.py -k doctor_ -q`
Expected: FAIL — `TypeError: doctor() got an unexpected keyword argument 'env_file'`.

- [ ] **Step 3: Add the option**

Replace the `doctor` command in `alfredctl/main.py` with:

```python
@app.command()
def doctor(
    online: Annotated[
        bool, typer.Option("--online/--offline", help="Live-probe external endpoints")
    ] = True,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="The .env to validate (default: <repo>/.env)"),
    ] = None,
) -> None:
    """Validate .env and prerequisites before starting the stack (config preflight)."""
    target = env_file or staging.repo_root() / ".env"
    failed = _render_doctor(doctor_mod.run_checks(target, online=online))
    if failed:
        console.print("[red]Preflight failed — fix the ✗ rows above, then re-run.[/red]")
        raise typer.Exit(code=1)
    console.print("[green]Preflight passed.[/green]")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/alfredctl/test_main_helpers.py -k doctor_ -q`
Expected: 2 passed.

- [ ] **Step 5: Check the whole suite and the linters**

```bash
.venv/bin/python -m pytest tests/alfredctl -q
.venv/bin/python -m ruff check alfredctl tests/alfredctl
.venv/bin/python -m mypy --strict alfredctl/
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add alfredctl/main.py tests/alfredctl/test_main_helpers.py
git commit -m "feat(alfredctl): add doctor --env-file to validate an out-of-tree .env"
```

### Task 8: Pin the compose project and container names

**This is the task that protects the live data.** The running container belongs to compose
project `alfred` (derived from the directory name) and owns `alfred_alfred_data`, which
holds the secrets passphrase generated on first boot. The deploy runs compose from
`~/code/alfred-deploy/`, whose directory name would derive the project `alfred-deploy` —
fresh empty volumes and a port collision with the container still running. Pinning
`name: alfred` makes the project name independent of the directory, so the existing volumes
are adopted and the running container is replaced. `container_name: alfred` is what
`smoke --name alfred` targets.

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add both pins**

At the top of `docker-compose.yml`, above `services:`, add:

```yaml
# Pinned so the project name does not depend on the directory compose runs from. CD runs
# `docker compose up -d` from ~/code/alfred-deploy/, which would otherwise derive the
# project `alfred-deploy` — new empty volumes (losing the secrets passphrase persisted in
# alfred_data on first boot) and a second container racing this one for :8081.
name: alfred
```

and under `services: alfred:`, directly beneath `image: alfred:latest`, add:

```yaml
    # Fixed so post-deploy verification can find it: `alfredctl smoke --attach --name alfred`.
    container_name: alfred
```

- [ ] **Step 2: Verify compose still parses and resolves the same volumes**

```bash
docker compose -f docker-compose.yml config | grep -E '^name:|container_name:|alfred_data:|alfred_models:'
```

Expected: `name: alfred`, `container_name: alfred`, and the volume keys unchanged.

- [ ] **Step 3: Confirm the pinned project matches the live one**

```bash
docker inspect alfred-alfred-1 --format '{{index .Config.Labels "com.docker.compose.project"}}'
```

Expected: `alfred` — identical to the pinned `name:`. If this prints anything else, **stop
and re-plan**: the deploy would create fresh volumes and lose the passphrase.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "fix(deploy): pin the compose project and container names"
```

- [ ] **Step 5: Open the PR**

```bash
git push -u origin feat/alfredctl-deploy-targets
gh pr create --title "feat(alfredctl): target a named container for smoke and a chosen .env for doctor" \
  --body "$(cat <<'EOF'
Phase 2a of the CD design (`docs/superpowers/specs/2026-08-18-cd-local-runner-design.md`).

Three small changes the deploy job needs, all mergeable ahead of any runner existing:

- `alfredctl smoke --name` — `smoke --attach` execs into `alfred-<branch-slug>`, but compose
  runs a differently-named container, so post-deploy verification would target a container
  that does not exist (spec §5.5).
- `alfredctl doctor --env-file` — `doctor` hardcoded `<repo>/.env`; the deploy validates a
  `.env` that lives outside the runner's checkout.
- Pinned `name: alfred` and `container_name: alfred` in `docker-compose.yml`. The project
  pin is load-bearing: CD runs compose from `~/code/alfred-deploy/`, which would otherwise
  derive the project `alfred-deploy`, create empty volumes, and lose the secrets passphrase
  persisted in `alfred_data`.

Both CLI options default to the previous behaviour, so nothing existing changes.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# Phase 2b — Gate fix and the alfred deploy job

**PR title:** `ci(deploy): deploy alfred to lath-server on every merge to master`

Depends on Phase 1 (the `alfred-deploy` runner must be online) and Phase 2a (the deploy
calls `smoke --name` and `doctor --env-file`).

### Task 9: Rename the aggregate job id to `gate`

`needs.ci-ok.result` does not evaluate — a hyphen parses as subtraction in a GitHub
expression property path. `ha-home-panel` solved this by giving the job the id `gate` and
the display name `ci-ok`, so the required-check setting (which matches the *name*) is
untouched. Adopt the same fix so the two repos read alike.

**Files:**
- Modify: `.github/workflows/ci.yml` (the `ci-ok` job, last in the file)
- Test: none — workflow YAML. Verified by the run itself in Task 11 Step 8.

- [ ] **Step 1: Branch**

```bash
cd ~/code/alfred-deploy/alfred
git fetch origin
git worktree add worktrees/ci-deploy-alfred -b ci/deploy-alfred origin/master
cd worktrees/ci-deploy-alfred
```

This must be branched **after** Phase 2a merges, so `origin/master` already carries
`smoke --name` and `doctor --env-file`. Confirm:

```bash
git grep -q 'typer.Option("--name"' alfredctl/main.py && echo "2a is in"
```

Expected: `2a is in`. If not, wait for Phase 2a to merge.

- [ ] **Step 2: Rename the job**

In `.github/workflows/ci.yml`, replace:

```yaml
  ci-ok:
    if: always()
    needs: [python, web, spa, pr-title, artifact-guard]
```

with:

```yaml
  # Job id `gate`, display name `ci-ok`. The id is what `needs.` expressions below refer
  # to, and `needs.ci-ok.result` would parse as a subtraction — hyphens are not legal in a
  # property path. The required-check setting matches the NAME, so it is unaffected.
  gate:
    name: ci-ok
    if: always()
    needs: [python, web, spa, pr-title, artifact-guard]
```

Leave the job's `runs-on` and steps exactly as they are.

- [ ] **Step 3: Verify the workflow still parses**

```bash
python3 -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/ci.yml')); print(list(d['jobs'])); assert d['jobs']['gate']['name']=='ci-ok'"
```

Expected: the job list ends with `gate`, and the assertion passes.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: rename the aggregate job id to gate, keeping the ci-ok display name"
```

### Task 10: Add the `repository_dispatch` trigger

Phase 3 makes home-service merges fire `home-service-merged` at this repo.

**Files:**
- Modify: `.github/workflows/ci.yml` (the `on:` block)

- [ ] **Step 1: Add the trigger**

Replace the `on:` block with:

```yaml
on:
  push:
    branches: [master]
  pull_request:
    types: [opened, edited, synchronize, reopened]
  # home-service is baked into the fat image and has no deployment of its own; a merge
  # there fires this so alfred is rebuilt with the new home-service on the trunk.
  repository_dispatch:
    types: [home-service-merged]
```

- [ ] **Step 2: Confirm every gated job still runs on a dispatch**

The `gate` job treats a `skipped` upstream job as failure, so a dispatch that skips any of
`python`, `web`, `spa`, `pr-title`, `artifact-guard` would block the deploy. None of them
carry a job-level `if:`, so all five run on any event. `pr-title` has two mutually
exclusive *steps*, and on a non-PR event the "Not a PR" step runs and the job succeeds.

```bash
python3 - <<'PY'
import yaml
jobs = yaml.safe_load(open('.github/workflows/ci.yml'))['jobs']
gated = ['python', 'web', 'spa', 'pr-title', 'artifact-guard']
bad = [j for j in gated if 'if' in jobs[j]]
print("jobs with a job-level if:", bad or "none — all five run on every event")
assert not bad
PY
```

Expected: `none — all five run on every event`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: accept a home-service-merged repository_dispatch"
```

### Task 11: The deploy job

**Files:**
- Modify: `.github/workflows/ci.yml` (append a `deploy` job after `gate`)

- [ ] **Step 1: Understand the checkout layout before writing it**

`alfredctl build` stages `git ls-files` from *both* repos, resolving the sibling via
`staging.workspace_root() / "home-service"` — the parent of the main checkout. So
home-service must sit **beside** the alfred checkout, named exactly `home-service`.

`actions/checkout` refuses a `path:` outside `$GITHUB_WORKSPACE`, so `../home-service` is
not available. The way to get siblings is to check *both* repos into subdirectories of the
workspace: alfred at `$GITHUB_WORKSPACE/alfred` and home-service at
`$GITHUB_WORKSPACE/home-service`. `workspace_root()` then resolves to `$GITHUB_WORKSPACE`
and finds the sibling. Every step that runs `alfredctl` therefore uses
`working-directory: alfred`.

- [ ] **Step 2: Append the deploy job**

Add to the end of `.github/workflows/ci.yml`:

```yaml
  deploy:
    name: deploy to lath-server
    needs: [gate]
    # `always() &&` is load-bearing. Without it this job inherits the implicit success()
    # check, and a SKIPPED job anywhere upstream propagates down the needs graph even when
    # `gate` itself succeeded — a deploy that silently skips on every push. Taking over the
    # condition with always() stops the propagation; the explicit result check is then what
    # gates the deploy. This cost ha-home-panel a run to diagnose.
    if: >-
      ${{ always()
      && needs.gate.result == 'success'
      && ((github.event_name == 'push' && github.ref == 'refs/heads/master')
          || github.event_name == 'repository_dispatch') }}
    runs-on: [self-hosted, linux, alfred-deploy]
    timeout-minutes: 60
    # Two deploys at once would race between building an image and starting the container
    # it replaces. Queue, never cancel.
    concurrency:
      group: deploy-alfred
      cancel-in-progress: false
    env:
      DEPLOY_DIR: /home/anirudhlath/code/alfred-deploy
    steps:
      # Both repos as siblings under $GITHUB_WORKSPACE: the fat image build stages
      # `git ls-files` from each, resolving home-service as a sibling of the alfred
      # checkout. A nested checkout would not be found.
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          path: alfred
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          repository: anirudhlath/alfred-home-service
          path: home-service

      # The box's system Python is 3.14; a fresh checkout would otherwise default to it.
      - uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9 # v9.0.0
        with:
          python-version: "3.13"
          enable-cache: true
      - name: Install alfredctl
        working-directory: alfred
        run: uv sync

      # Before the build, not after: a misconfigured box should fail in seconds rather than
      # after a full fat-image build. Offline because a deploy must not fail on a transient
      # outage of an external endpoint — what is checked is that the config is sane.
      - name: Preflight
        working-directory: alfred
        run: uv run alfredctl doctor --offline --env-file "$DEPLOY_DIR/.env"

      - name: Record the running image for rollback
        id: rollback
        run: |
          if id=$(docker image inspect -f '{{.Id}}' alfred:latest 2>/dev/null); then
            echo "image=$id" >> "$GITHUB_OUTPUT"
            echo "rollback target: $id"
          else
            echo "image=none" >> "$GITHUB_OUTPUT"
            echo "::notice::no existing alfred:latest — first deploy, nothing to roll back to"
          fi

      - name: Build
        working-directory: alfred
        run: |
          uv run alfredctl build --tag alfred:latest
          docker tag alfred:latest "alfred:${{ github.sha }}"

      # Copied, not symlinked: the deploy workspace must keep working when the runner wipes
      # its checkout between runs.
      - name: Publish the compose file to the deploy workspace
        run: cp alfred/docker-compose.yml "$DEPLOY_DIR/docker-compose.yml"

      - name: Start
        id: start
        working-directory: /home/anirudhlath/code/alfred-deploy
        run: docker compose up -d

      # Polls /health, then checks redis, the RediSearch modules and mosquitto INSIDE the
      # running container. `--name alfred` matches the pinned container_name.
      - name: Verify
        id: verify
        working-directory: alfred
        run: uv run alfredctl smoke --attach --name alfred

      # A rollback that itself fails is louder, not quieter: this exits non-zero either way.
      - name: Roll back
        if: failure() && (steps.start.outcome == 'failure' || steps.verify.outcome == 'failure')
        env:
          TARGET: ${{ steps.rollback.outputs.image }}
        run: |
          if [ "$TARGET" = "none" ]; then
            echo "::error::deploy failed and there is no previous image to restore"
            exit 1
          fi
          echo "::warning::deploy failed — restoring $TARGET"
          docker tag "$TARGET" alfred:latest
          cd "$DEPLOY_DIR"
          docker compose up -d
          cd "$GITHUB_WORKSPACE/alfred"
          if uv run alfredctl smoke --attach --name alfred; then
            echo "::error::deploy failed; rolled back to $TARGET and it verified"
          else
            echo "::error::deploy failed AND the rollback to $TARGET did not verify — alfred may be down"
          fi
          exit 1
```

- [ ] **Step 3: Verify the YAML parses and the job wiring is right**

```bash
python3 - <<'PY'
import yaml
d = yaml.safe_load(open('.github/workflows/ci.yml'))
dep = d['jobs']['deploy']
assert dep['needs'] == ['gate'], dep['needs']
assert 'always()' in dep['if'] and "needs.gate.result == 'success'" in dep['if']
assert dep['concurrency']['cancel-in-progress'] is False
assert dep['runs-on'] == ['self-hosted', 'linux', 'alfred-deploy']
assert 'home-service-merged' in d[True]['repository_dispatch']['types']
print("deploy job wiring OK")
PY
```

Expected: `deploy job wiring OK`.

`d[True]` is not a typo — PyYAML parses the bare key `on:` as the boolean `True`.

- [ ] **Step 4: Verify no PR can reach the runner**

The safety property from §8. Assert it mechanically rather than by reading:

```bash
python3 - <<'PY'
import re, yaml
d = yaml.safe_load(open('.github/workflows/ci.yml'))
selfhosted = [n for n, j in d['jobs'].items()
              if isinstance(j.get('runs-on'), list) and 'self-hosted' in j['runs-on']]
print("self-hosted jobs:", selfhosted)
for n in selfhosted:
    cond = d['jobs'][n]['if']
    assert "github.event_name == 'push'" in cond, n
    assert "refs/heads/master" in cond, n
    assert 'pull_request' not in cond, n
print("no self-hosted job can run on a pull_request")
PY
```

Expected: `self-hosted jobs: ['deploy']` then the confirmation line.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add the lath-server deploy job with verified rollback"
```

- [ ] **Step 6: Open the PR**

```bash
git push -u origin ci/deploy-alfred
gh pr create --title "ci(deploy): deploy alfred to lath-server on every merge to master" \
  --body "$(cat <<'EOF'
Phase 2b of the CD design (`docs/superpowers/specs/2026-08-18-cd-local-runner-design.md`).

- Renames the aggregate job id `ci-ok` → `gate` with `name: ci-ok`. `needs.ci-ok.result`
  never evaluates — a hyphen parses as subtraction in a GitHub expression path. The
  required-check setting matches the display name, so it is unaffected.
- Accepts a `home-service-merged` `repository_dispatch` (Phase 3 fires it).
- Adds a single `deploy` job on the self-hosted `alfred-deploy` runner: preflight →
  record the running image → build → `docker compose up -d` from `~/code/alfred-deploy/`
  → `alfredctl smoke --attach --name alfred` → roll back and fail red on any failure.

`always() &&` leads the deploy condition deliberately: without it a skipped upstream job
propagates through the needs graph and the deploy silently skips on every push.

No pull-request job targets a self-hosted runner.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 7: Confirm the checks pass on the PR**

```bash
gh pr checks --watch
```

Expected: every check green. The `deploy` job will show as **skipped** — it is a
pull_request event. That skip is the safety property working.

- [ ] **Step 8: After merge, watch the first real deploy**

```bash
gh run watch (gh run list --workflow=ci.yml --branch=master --limit=1 --json databaseId --jq '.[0].databaseId')
```

Expected: `gate` green, then `deploy to lath-server` green. Then confirm on the box:

```fish
docker ps --filter name=alfred --format '{{.Names}}\t{{.Image}}\t{{.Status}}'
docker inspect alfred --format '{{index .Config.Labels "com.docker.compose.project"}}'
docker volume ls --format '{{.Name}}' | grep alfred
```

Expected: one container named `alfred`, healthy; project `alfred`; the **same three
volumes as Task 1 Step 1** — `alfred_alfred_data`, `alfred_alfred_models`,
`alfred_redis_data`. If a new `alfred-deploy_*` volume appeared, the project pin did not
take: stop, restore from `alfred:${{ github.sha }}`'s predecessor, and fix Task 8.

---

# Phase 3 — home-service dispatches an alfred redeploy

**Repo:** `anirudhlath/alfred-home-service`, checked out at `~/code/alfred-deploy/home-service`. Trunk is `main`.
**PR title:** `ci: dispatch an alfred redeploy on every merge to main`

home-service is baked into the fat image and has no artifact of its own. Rather than a
third runner and duplicated deploy logic, a merge here posts a `repository_dispatch` at
`alfred`, whose deploy job then checks out both repos at their current trunks — exactly
what the fat image build needs.

### Task 12: Mint the dispatch PAT

- [ ] **Step 1: Create a fine-grained PAT**

At `https://github.com/settings/personal-access-tokens/new`:

- **Resource owner:** `anirudhlath`
- **Repository access:** *Only select repositories* → `anirudhlath/alfred` **only**
- **Permissions:** Repository permissions → **Contents: Read and write** (this is what
  `POST /repos/{owner}/{repo}/dispatches` requires)
- **Expiration:** 1 year

Nothing else. The narrowest thing that can fire a dispatch. Its worst case is triggering a
deploy of code that is already on the trunk.

- [ ] **Step 2: Store it as a secret in home-service**

```fish
gh secret set ALFRED_DISPATCH_TOKEN --repo anirudhlath/alfred-home-service
```

Paste the token when prompted.

- [ ] **Step 3: Verify it can dispatch, before wiring any workflow to it**

```fish
set -l tok (read -s -P "token: ")
curl -sS -o /dev/null -w '%{http_code}\n' \
  -X POST -H "Authorization: Bearer $tok" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/anirudhlath/alfred/dispatches \
  -d '{"event_type":"home-service-merged"}'
```

Expected: `204`. A `404` means the token lacks `contents: write` on `anirudhlath/alfred`
(GitHub returns 404 rather than 403 for a scope it cannot see) — regenerate with the right
permission rather than widening it.

This dispatch is real and will run a deploy of the current trunk. That is a useful first
end-to-end proof; watch it with `gh run list --repo anirudhlath/alfred --limit 3`.

### Task 13: Gate fix and the dispatch job

**Files:**
- Modify: `.github/workflows/ci.yml` (rename `ci-ok` → `gate`, append `dispatch`)

- [ ] **Step 1: Branch**

```bash
cd ~/code/alfred-deploy/home-service
git fetch origin
git worktree add ../home-service-worktrees/ci-dispatch-alfred -b ci/dispatch-alfred origin/main
cd ../home-service-worktrees/ci-dispatch-alfred
```

- [ ] **Step 2: Rename the aggregate job**

In `.github/workflows/ci.yml`, replace:

```yaml
  ci-ok:
    if: always()
    needs: [python, pr-title]
```

with:

```yaml
  # Job id `gate`, display name `ci-ok`. `needs.ci-ok.result` would parse as a subtraction
  # — hyphens are not legal in a GitHub expression property path. The required-check
  # setting matches the NAME, so it is unaffected.
  gate:
    name: ci-ok
    if: always()
    needs: [python, pr-title]
```

Leave the job's steps unchanged.

- [ ] **Step 3: Append the dispatch job**

Add to the end of `.github/workflows/ci.yml`:

```yaml
  # home-service is baked into alfred's fat image and has no deployment of its own. A merge
  # here asks alfred to rebuild, and alfred's deploy job checks out both repos at their
  # current trunks. No third runner, no duplicated deploy logic.
  dispatch:
    name: ask alfred to redeploy
    needs: [gate]
    # `always() &&` is load-bearing: without it a SKIPPED upstream job propagates down the
    # needs graph and this silently skips on every push, even with `gate` green.
    if: >-
      ${{ always()
      && needs.gate.result == 'success'
      && github.event_name == 'push'
      && github.ref == 'refs/heads/main' }}
    runs-on: ubuntu-latest
    steps:
      - name: POST repository_dispatch to anirudhlath/alfred
        env:
          TOKEN: ${{ secrets.ALFRED_DISPATCH_TOKEN }}
        run: |
          if [ -z "$TOKEN" ]; then
            echo "::error::ALFRED_DISPATCH_TOKEN is not set — alfred will not be redeployed"
            exit 1
          fi
          code=$(curl -sS -o /tmp/resp.json -w '%{http_code}' \
            -X POST \
            -H "Authorization: Bearer $TOKEN" \
            -H "Accept: application/vnd.github+json" \
            https://api.github.com/repos/anirudhlath/alfred/dispatches \
            -d '{"event_type":"home-service-merged"}')
          echo "HTTP $code"
          if [ "$code" != "204" ]; then
            cat /tmp/resp.json
            echo "::error::dispatch failed (HTTP $code) — alfred was NOT redeployed"
            exit 1
          fi
          echo "dispatched home-service-merged to anirudhlath/alfred"
```

An unset token fails the job rather than skipping the step: a silently-not-redeployed
house is exactly the failure mode this design exists to prevent.

- [ ] **Step 4: Verify the YAML**

```bash
python3 - <<'PY'
import yaml
d = yaml.safe_load(open('.github/workflows/ci.yml'))
assert d['jobs']['gate']['name'] == 'ci-ok'
disp = d['jobs']['dispatch']
assert disp['needs'] == ['gate']
assert 'always()' in disp['if'] and "refs/heads/main" in disp['if']
assert disp['runs-on'] == 'ubuntu-latest'
assert 'ci-ok' not in d['jobs']
print("dispatch job wiring OK")
PY
```

Expected: `dispatch job wiring OK`.

- [ ] **Step 5: Commit and open the PR**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: dispatch an alfred redeploy on merge to main"
git push -u origin ci/dispatch-alfred
gh pr create --title "ci: dispatch an alfred redeploy on every merge to main" \
  --body "$(cat <<'EOF'
Phase 3 of the CD design (`alfred`'s `docs/superpowers/specs/2026-08-18-cd-local-runner-design.md`).

home-service is baked into alfred's fat image and has no artifact of its own, so a merge
here posts a `home-service-merged` `repository_dispatch` at `anirudhlath/alfred`. Alfred's
deploy job then checks out both repos at their current trunks — which is what the fat
image build needs — so there is no third runner and no duplicated deploy logic.

Also renames the aggregate job id `ci-ok` → `gate` with `name: ci-ok`: `needs.ci-ok.result`
never evaluates, because a hyphen parses as subtraction in a GitHub expression path. The
required-check setting matches the display name and is unaffected.

Uses `ALFRED_DISPATCH_TOKEN`, a fine-grained PAT scoped to `contents: write` on
`anirudhlath/alfred` alone.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: After merge, confirm the chain end to end**

```fish
gh run list --repo anirudhlath/alfred-home-service --limit 1
gh run list --repo anirudhlath/alfred --event repository_dispatch --limit 1
```

Expected: the home-service run is green with `ask alfred to redeploy` succeeded, and a
`repository_dispatch` run appears in `alfred` within a few seconds. Then confirm the house
actually updated:

```fish
docker inspect alfred --format '{{.Created}} {{.State.Health.Status}}'
```

Expected: a `Created` timestamp from the last few minutes and `healthy`.

---

# Phase 4 — Satellite CI and deployment

**Repo:** `anirudhlath/alfred-satellite`, cloned in Phase 1 Task 4 to `~/code/alfred-satellite`.

The repo has no workflows at all today, so CI comes first. The deploy job is thin glue
around `dev/deploy_satellites.py`; every real decision — inventory merging, rollback,
result aggregation — is a pure function with unit tests, and the untestable part is
confined to one transport adapter.

Substitute the trunk branch name recorded in Phase 1 Task 4 Step 2 wherever `main` appears
below.

### Task 14: Scaffold the Python tooling

**Files:**
- Create: `pyproject.toml`, `dev/__init__.py`, `dev/satellites/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Branch**

```bash
cd ~/code/alfred-satellite
git fetch origin
git worktree add worktrees/feat-deploy-tooling -b feat/deploy-tooling origin/main
cd worktrees/feat-deploy-tooling
```

- [ ] **Step 2: Create `pyproject.toml`**

If the repo already has one, merge these sections into it rather than overwriting.

```toml
[project]
name = "alfred-satellite-dev"
version = "0.1.0"
description = "Deployment tooling for the Alfred voice satellite fleet"
requires-python = ">=3.13"
dependencies = [
    "typer>=0.15",
    "rich>=13.9",
    "loguru>=0.7",
    "pyyaml>=6.0",
]

[dependency-groups]
dev = [
    "pytest>=8.3",
    "mypy>=1.14",
    "ruff>=0.9",
    "types-pyyaml>=6.0",
]

[tool.ruff]
line-length = 100

[tool.mypy]
strict = true
python_version = "3.13"

[tool.pytest.ini_options]
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["dev"]
```

- [ ] **Step 3: Create the package skeletons**

```bash
mkdir -p dev/satellites tests
printf '"""Deployment tooling for the Alfred satellite fleet."""\n' > dev/__init__.py
printf '"""Inventory, transport and deploy logic for the satellite fleet."""\n' > dev/satellites/__init__.py
touch tests/__init__.py
```

- [ ] **Step 4: Create the venv and confirm it resolves**

```bash
uv venv --python 3.13
uv sync
.venv/bin/python -c "import typer, rich, loguru, yaml; print('deps ok')"
```

Expected: `deps ok`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock dev tests
git commit -m "chore: scaffold Python deployment tooling"
```

### Task 15: `dev/satellites/inventory.py` — the file ∪ mDNS merge

The canonical inventory is a YAML file on the runner; discovery runs
`avahi-browse -rpt _wyoming._tcp`. Both exist because the file is authoritative today
while discovery earns trust. Three cases, all of which deploy:

- in both → deploy, using the **file's** `name`/`area`
- discovered only → deploy, and warn it is missing from the inventory
- file only, no mDNS answer → deploy at its recorded address anyway (a Pi may not be
  advertising), and warn

**Files:**
- Create: `dev/satellites/inventory.py`
- Test: `tests/test_inventory.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_inventory.py`:

```python
from __future__ import annotations

from pathlib import Path

from dev.satellites.inventory import Satellite, load_file, merge, parse_avahi

# One real `avahi-browse -rpt _wyoming._tcp` line, semicolon-delimited. Fields:
# 0 '=', 1 iface, 2 proto, 3 escaped name, 4 type, 5 domain, 6 hostname, 7 address,
# 8 port, 9 TXT records.
_AVAHI = (
    "=;eth0;IPv4;kitchen;_wyoming._tcp;local;kitchen.local;192.168.1.40;10700;\n"
    "=;eth0;IPv4;office;_wyoming._tcp;local;office.local;192.168.1.41;10700;\n"
)


def test_load_file_reads_the_documented_schema(tmp_path: Path) -> None:
    p = tmp_path / "satellites.yaml"
    p.write_text(
        "satellites:\n"
        "  - name: kitchen\n"
        "    host: 192.168.1.40\n"
        "    port: 10700\n"
        "    area: Kitchen\n"
    )
    assert load_file(p) == [Satellite("kitchen", "192.168.1.40", 10700, "Kitchen", "file")]


def test_load_file_defaults_the_port(tmp_path: Path) -> None:
    p = tmp_path / "satellites.yaml"
    p.write_text("satellites:\n  - name: den\n    host: 192.168.1.42\n")
    assert load_file(p)[0].port == 10700


def test_load_file_missing_is_empty_not_an_error(tmp_path: Path) -> None:
    assert load_file(tmp_path / "absent.yaml") == []


def test_parse_avahi_extracts_name_host_port() -> None:
    assert parse_avahi(_AVAHI) == [
        Satellite("kitchen", "192.168.1.40", 10700, None, "mdns"),
        Satellite("office", "192.168.1.41", 10700, None, "mdns"),
    ]


def test_parse_avahi_ignores_resolution_failures_and_ipv6_dupes() -> None:
    noisy = "+;eth0;IPv4;kitchen;_wyoming._tcp;local\n" + _AVAHI
    # '+' lines are browse-only (unresolved) and carry no address; only '=' lines resolve.
    assert [s.name for s in parse_avahi(noisy)] == ["kitchen", "office"]


def test_merge_in_both_keeps_the_files_name_and_area() -> None:
    file_sats = [Satellite("kitchen", "192.168.1.40", 10700, "Kitchen", "file")]
    found = [Satellite("kitchen-sat", "192.168.1.40", 10700, None, "mdns")]
    assert merge(file_sats, found) == [
        Satellite("kitchen", "192.168.1.40", 10700, "Kitchen", "both")
    ]


def test_merge_discovered_only_is_included_and_marked() -> None:
    found = [Satellite("hallway", "192.168.1.44", 10700, None, "mdns")]
    assert merge([], found) == [Satellite("hallway", "192.168.1.44", 10700, None, "mdns")]


def test_merge_file_only_is_included_and_marked() -> None:
    file_sats = [Satellite("attic", "192.168.1.45", 10700, "Attic", "file")]
    assert merge(file_sats, []) == [Satellite("attic", "192.168.1.45", 10700, "Attic", "file")]


def test_merge_matches_on_name_when_the_address_differs() -> None:
    # The file records a .local name; mDNS answers with an A record.
    file_sats = [Satellite("kitchen", "kitchen.local", 10700, "Kitchen", "file")]
    found = [Satellite("kitchen", "192.168.1.40", 10700, None, "mdns")]
    merged = merge(file_sats, found)
    assert len(merged) == 1
    assert merged[0].source == "both"
    # The discovered address wins — it is the one that answered just now.
    assert merged[0].host == "192.168.1.40"


def test_merge_is_stable_and_deduplicates() -> None:
    file_sats = [
        Satellite("kitchen", "192.168.1.40", 10700, "Kitchen", "file"),
        Satellite("office", "192.168.1.41", 10700, "Office", "file"),
    ]
    found = [
        Satellite("office", "192.168.1.41", 10700, None, "mdns"),
        Satellite("hallway", "192.168.1.44", 10700, None, "mdns"),
    ]
    assert [(s.name, s.source) for s in merge(file_sats, found)] == [
        ("kitchen", "file"),
        ("office", "both"),
        ("hallway", "mdns"),
    ]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_inventory.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dev.satellites.inventory'`.

- [ ] **Step 3: Write the implementation**

Create `dev/satellites/inventory.py`:

```python
"""Satellite inventory: the YAML file, mDNS discovery, and the union of the two.

The file is authoritative today; discovery is earning trust. Every entry from either
source is deployed to — a Pi that has stopped advertising must not silently drop out of
the fleet, and one that appears without being in the file must not be silently ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import yaml

DEFAULT_PORT = 10700
"""wyoming-satellite's default port."""

Source = Literal["file", "mdns", "both"]


@dataclass(frozen=True)
class Satellite:
    """One Pi in the fleet."""

    name: str
    host: str
    port: int = DEFAULT_PORT
    area: str | None = None
    source: Source = "file"


def load_file(path: Path) -> list[Satellite]:
    """Read the inventory YAML. A missing file is an empty fleet, not an error —
    discovery alone is a valid configuration and is the intended end state."""
    if not path.is_file():
        return []
    doc = yaml.safe_load(path.read_text()) or {}
    entries = doc.get("satellites") or []
    return [
        Satellite(
            name=str(e["name"]),
            host=str(e["host"]),
            port=int(e.get("port", DEFAULT_PORT)),
            area=str(e["area"]) if e.get("area") else None,
            source="file",
        )
        for e in entries
    ]


def parse_avahi(output: str) -> list[Satellite]:
    """Parse `avahi-browse -rpt _wyoming._tcp` output.

    Only `=` lines are resolved records carrying an address and port; `+` lines are
    browse-only announcements with neither. Fields are semicolon-delimited:
    `=;iface;proto;name;type;domain;hostname;address;port;txt`.
    """
    found: list[Satellite] = []
    seen: set[tuple[str, int]] = set()
    for line in output.splitlines():
        parts = line.split(";")
        if len(parts) < 9 or parts[0] != "=":
            continue
        name, address, port = parts[3], parts[7], parts[8]
        if not address or not port.isdigit():
            continue
        key = (address, int(port))
        if key in seen:  # the same service answers on IPv4 and IPv6
            continue
        seen.add(key)
        found.append(Satellite(name=name, host=address, port=int(port), source="mdns"))
    return found


def _norm(host: str) -> str:
    """mDNS hostnames arrive fully qualified (`kitchen.local.`); the file records them bare."""
    return host.casefold().rstrip(".")


def _same_device(a: Satellite, b: Satellite) -> bool:
    """Two records describe one Pi if either the name or the address matches.

    Both keys are needed: the file may record `kitchen.local` where mDNS answers with an A
    record (names match, addresses do not), and a Pi may advertise a service name that
    differs from its inventory name (addresses match, names do not).
    """
    return a.name.casefold() == b.name.casefold() or _norm(a.host) == _norm(b.host)


def merge(file_sats: list[Satellite], discovered: list[Satellite]) -> list[Satellite]:
    """Union the two sources, file order first, then discovery-only entries.

    Where both describe the same Pi, the file supplies `name` and `area` (its human
    labels are the ones Home Assistant matches on) and discovery supplies `host` and
    `port` (the address that answered just now beats a recorded one).
    """
    merged: list[Satellite] = []
    matched: list[Satellite] = []
    for f in file_sats:
        hit = next((d for d in discovered if _same_device(f, d)), None)
        if hit is None:
            merged.append(f)
            continue
        matched.append(hit)
        merged.append(replace(f, host=hit.host, port=hit.port, source="both"))
    merged.extend(d for d in discovered if d not in matched)
    return merged
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_inventory.py -q`
Expected: 10 passed.

- [ ] **Step 5: Lint and typecheck**

```bash
.venv/bin/python -m ruff check dev tests
.venv/bin/python -m mypy --strict dev tests
```

Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git add dev/satellites/inventory.py tests/test_inventory.py
git commit -m "feat(deploy): merge the satellite inventory file with mDNS discovery"
```

### Task 16: `dev/satellites/transport.py` — the one untestable layer

SSH, rsync and the TCP probe sit behind a protocol so everything above them is pure. This
module is deliberately thin: it has no branching worth testing, which is the point.

**Files:**
- Create: `dev/satellites/transport.py`
- Test: none directly — `tests/test_deploy.py` (Task 17) drives a fake implementation of
  this protocol, which is what keeps this file small enough not to need its own tests.

- [ ] **Step 1: Write the module**

Create `dev/satellites/transport.py`:

```python
"""How commands and files reach a Pi.

Everything above this module is pure and unit-tested; everything untestable is here.
Keep it that way — if a decision starts creeping into this file, it belongs in deploy.py.
"""

from __future__ import annotations

import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

_SSH_OPTS = [
    "-o", "BatchMode=yes",           # never prompt; an unreachable Pi must fail, not hang
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10",
]


class Transport(Protocol):
    """The three things a deploy does to a device."""

    def run(self, host: str, command: str) -> tuple[int, str]:
        """Run a shell command on the device; return (exit code, combined output)."""
        ...

    def push(self, host: str, src: Path, dest: str) -> tuple[int, str]:
        """Mirror a local directory to a remote path; return (exit code, output)."""
        ...

    def probe(self, host: str, port: int) -> bool:
        """True if a TCP connection to host:port is accepted."""
        ...


@dataclass
class SshTransport:
    """Real SSH/rsync against a Pi."""

    key: Path
    user: str = "pi"
    timeout: float = 600.0

    def _ssh_args(self) -> list[str]:
        return ["ssh", "-i", str(self.key), *_SSH_OPTS]

    def run(self, host: str, command: str) -> tuple[int, str]:
        proc = subprocess.run(
            [*self._ssh_args(), f"{self.user}@{host}", command],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()

    def push(self, host: str, src: Path, dest: str) -> tuple[int, str]:
        # The trailing slash on src is load-bearing: it copies the directory's CONTENTS.
        # Without it rsync would nest the checkout inside dest on every deploy.
        proc = subprocess.run(
            [
                "rsync",
                "-az",
                "--delete",
                "-e",
                " ".join(self._ssh_args()),
                f"{str(src).rstrip('/')}/",
                f"{self.user}@{host}:{dest}",
            ],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        return proc.returncode, (proc.stdout + proc.stderr).strip()

    def probe(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=5.0):
                return True
        except OSError:
            return False


@dataclass
class DryRunTransport:
    """Records what would happen and touches nothing. Backs `--dry-run`."""

    actions: list[str] = field(default_factory=list)

    def run(self, host: str, command: str) -> tuple[int, str]:
        self.actions.append(f"{host}: run {command}")
        return 0, "(dry run)"

    def push(self, host: str, src: Path, dest: str) -> tuple[int, str]:
        self.actions.append(f"{host}: rsync {src}/ -> {dest}")
        return 0, "(dry run)"

    def probe(self, host: str, port: int) -> bool:
        self.actions.append(f"{host}: probe :{port}")
        return True
```

- [ ] **Step 2: Verify it typechecks**

```bash
.venv/bin/python -m ruff check dev
.venv/bin/python -m mypy --strict dev
```

Expected: both clean. `mypy --strict` proving `SshTransport` and `DryRunTransport` both
satisfy `Transport` is the real check here — Task 17 annotates parameters as `Transport`
and passes both.

- [ ] **Step 3: Commit**

```bash
git add dev/satellites/transport.py
git commit -m "feat(deploy): add the satellite SSH/rsync transport behind a protocol"
```

### Task 17: `dev/satellites/deploy.py` — per-device deploy, rollback, aggregation

Per §7.4: move the old tree aside, rsync, run `setup.sh`, restart both units, verify both
are active and the Wyoming port accepts a connection; on any failure restore the previous
tree and record the device as failed. **Every device is attempted even after one fails** —
a single unreachable Pi must not leave the rest of the house on an old build.

**Files:**
- Create: `dev/satellites/deploy.py`
- Test: `tests/test_deploy.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_deploy.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dev.satellites.deploy import (
    PREV_ROOT,
    REMOTE_ROOT,
    SERVICES,
    DeviceResult,
    deploy_all,
    deploy_one,
    exit_code,
)
from dev.satellites.inventory import Satellite


@dataclass
class FakeTransport:
    """A scriptable Transport. `fail_on` matches a substring of the command."""

    fail_on: str | None = None
    push_fails: bool = False
    probe_ok: bool = True
    inactive: bool = False
    commands: list[str] = field(default_factory=list)
    pushes: list[str] = field(default_factory=list)

    def run(self, host: str, command: str) -> tuple[int, str]:
        self.commands.append(command)
        if self.fail_on and self.fail_on in command:
            return 1, f"boom: {command}"
        if self.inactive and "is-active" in command:
            return 3, "inactive"
        if "is-active" in command:
            return 0, "active"
        return 0, "ok"

    def push(self, host: str, src: Path, dest: str) -> tuple[int, str]:
        self.pushes.append(f"{src}->{dest}")
        return (1, "rsync failed") if self.push_fails else (0, "sent")

    def probe(self, host: str, port: int) -> bool:
        return self.probe_ok


_SAT = Satellite("kitchen", "192.168.1.40", 10700, "Kitchen", "file")


def test_happy_path_reports_ok(tmp_path: Path) -> None:
    t = FakeTransport()
    r = deploy_one(t, _SAT, tmp_path)
    assert r.ok and not r.rolled_back
    assert r.name == "kitchen" and r.host == "192.168.1.40"


def test_happy_path_runs_the_documented_sequence(tmp_path: Path) -> None:
    t = FakeTransport()
    deploy_one(t, _SAT, tmp_path)
    joined = "\n".join(t.commands)
    assert f"mv {REMOTE_ROOT} {PREV_ROOT}" in joined
    assert f"sudo {REMOTE_ROOT}/scripts/setup.sh" in joined
    assert f"sudo systemctl restart {' '.join(SERVICES)}" in joined
    for svc in SERVICES:
        assert f"systemctl is-active {svc}" in joined
    assert t.pushes == [f"{tmp_path}->{REMOTE_ROOT}"]


def test_rsync_failure_rolls_back(tmp_path: Path) -> None:
    t = FakeTransport(push_fails=True)
    r = deploy_one(t, _SAT, tmp_path)
    assert not r.ok and r.rolled_back
    assert "rsync" in r.detail
    assert f"mv {PREV_ROOT} {REMOTE_ROOT}" in "\n".join(t.commands)


def test_setup_failure_rolls_back(tmp_path: Path) -> None:
    t = FakeTransport(fail_on="setup.sh")
    r = deploy_one(t, _SAT, tmp_path)
    assert not r.ok and r.rolled_back
    assert "setup.sh" in r.detail


def test_a_dead_unit_rolls_back(tmp_path: Path) -> None:
    t = FakeTransport(inactive=True)
    r = deploy_one(t, _SAT, tmp_path)
    assert not r.ok and r.rolled_back
    assert "is-active" in r.detail


def test_a_closed_port_rolls_back(tmp_path: Path) -> None:
    t = FakeTransport(probe_ok=False)
    r = deploy_one(t, _SAT, tmp_path)
    assert not r.ok and r.rolled_back
    assert "10700" in r.detail


def test_an_unreachable_pi_counts_as_failed_not_skipped(tmp_path: Path) -> None:
    # The very first command fails — the shape of "no route to host". Nothing was moved
    # aside, so there is nothing to restore and the device is simply failed.
    t = FakeTransport(fail_on="mv")
    r = deploy_one(t, _SAT, tmp_path)
    assert not r.ok and not r.rolled_back
    assert "could not stage" in r.detail


def test_every_device_is_attempted_after_one_fails(tmp_path: Path) -> None:
    sats = [
        Satellite("kitchen", "192.168.1.40", 10700, "Kitchen", "file"),
        Satellite("office", "192.168.1.41", 10700, "Office", "file"),
        Satellite("den", "192.168.1.42", 10700, "Den", "file"),
    ]
    transports = {s.host: FakeTransport(push_fails=s.name == "office") for s in sats}
    results = deploy_all(lambda s: transports[s.host], sats, tmp_path)
    assert [r.name for r in results] == ["kitchen", "office", "den"]
    assert [r.ok for r in results] == [True, False, True]
    # The one that failed did not stop the third from being tried.
    assert transports["192.168.1.42"].pushes


def test_exit_code_is_zero_only_when_every_device_passed() -> None:
    ok = DeviceResult("kitchen", "192.168.1.40", True, "deployed")
    bad = DeviceResult("office", "192.168.1.41", False, "rsync failed", rolled_back=True)
    assert exit_code([ok]) == 0
    assert exit_code([ok, bad]) == 1


def test_exit_code_treats_an_empty_fleet_as_failure() -> None:
    # Deploying to nothing is not a pass — it means the inventory and mDNS both came up
    # empty, which is a misconfiguration, not a clean run.
    assert exit_code([]) == 1
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_deploy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dev.satellites.deploy'`.

- [ ] **Step 3: Write the implementation**

Create `dev/satellites/deploy.py`:

```python
"""Per-device deployment, rollback and fleet aggregation.

Pure given a Transport: every decision here is unit-tested against a fake, and the only
untestable code lives in transport.py.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from dev.satellites.inventory import Satellite
from dev.satellites.transport import Transport

REMOTE_ROOT = "/opt/alfred-satellite"
PREV_ROOT = "/opt/alfred-satellite.prev"
SERVICES = ("wyoming-satellite.service", "wyoming-openwakeword.service")


@dataclass(frozen=True)
class DeviceResult:
    """The outcome for one Pi. A device that could not be reached at all is `ok=False`,
    exactly like one whose deploy broke — never a skip."""

    name: str
    host: str
    ok: bool
    detail: str
    rolled_back: bool = False


def _rollback(t: Transport, sat: Satellite, reason: str) -> DeviceResult:
    """Restore the previous tree and restart. Reports the ORIGINAL failure either way —
    what broke the deploy is more useful than what broke the recovery."""
    logger.warning("{}: {} — rolling back", sat.name, reason)
    rc, out = t.run(
        sat.host,
        f"sudo sh -c 'if [ -d {PREV_ROOT} ]; then rm -rf {REMOTE_ROOT} && "
        f"mv {PREV_ROOT} {REMOTE_ROOT}; fi'",
    )
    if rc != 0:
        logger.error("{}: rollback failed: {}", sat.name, out)
        return DeviceResult(sat.name, sat.host, False, f"{reason}; rollback failed: {out}")
    restart_rc, restart_out = t.run(sat.host, f"sudo systemctl restart {' '.join(SERVICES)}")
    if restart_rc != 0:
        logger.error("{}: restart after rollback failed: {}", sat.name, restart_out)
        return DeviceResult(
            sat.name, sat.host, False, f"{reason}; restart after rollback failed", True
        )
    return DeviceResult(sat.name, sat.host, False, reason, rolled_back=True)


def deploy_one(t: Transport, sat: Satellite, checkout: Path) -> DeviceResult:
    """Deploy one device, restoring the previous tree on any failure."""
    logger.info("{} ({}): deploying", sat.name, sat.host)

    rc, out = t.run(
        sat.host,
        f"sudo sh -c 'rm -rf {PREV_ROOT}; if [ -d {REMOTE_ROOT} ]; then "
        f"mv {REMOTE_ROOT} {PREV_ROOT}; fi; mkdir -p {REMOTE_ROOT}'",
    )
    if rc != 0:
        # Nothing was moved aside, so there is nothing to restore.
        return DeviceResult(sat.name, sat.host, False, f"could not stage: {out}")

    rc, out = t.push(sat.host, checkout, REMOTE_ROOT)
    if rc != 0:
        return _rollback(t, sat, f"rsync failed: {out}")

    rc, out = t.run(sat.host, f"sudo {REMOTE_ROOT}/scripts/setup.sh")
    if rc != 0:
        return _rollback(t, sat, f"setup.sh failed: {out}")

    rc, out = t.run(sat.host, f"sudo systemctl restart {' '.join(SERVICES)}")
    if rc != 0:
        return _rollback(t, sat, f"restart failed: {out}")

    for svc in SERVICES:
        rc, out = t.run(sat.host, f"systemctl is-active {svc}")
        if rc != 0:
            return _rollback(t, sat, f"is-active {svc}: {out}")

    if not t.probe(sat.host, sat.port):
        return _rollback(t, sat, f"port {sat.port} refused the connection")

    logger.success("{} ({}): deployed", sat.name, sat.host)
    return DeviceResult(sat.name, sat.host, True, "deployed")


def deploy_all(
    transport_for: Callable[[Satellite], Transport],
    sats: Sequence[Satellite],
    checkout: Path,
) -> list[DeviceResult]:
    """Deploy to every device in order, continuing past failures.

    A single unreachable Pi must not leave the rest of the house on an old build, so an
    exception from one device becomes that device's failed result and the loop goes on.
    """
    results: list[DeviceResult] = []
    for sat in sats:
        try:
            results.append(deploy_one(transport_for(sat), sat, checkout))
        except Exception as exc:  # noqa: BLE001 — one Pi must never abort the fleet
            logger.exception("{} ({}): unhandled failure", sat.name, sat.host)
            results.append(DeviceResult(sat.name, sat.host, False, f"unhandled: {exc}"))
    return results


def exit_code(results: Sequence[DeviceResult]) -> int:
    """0 only when every device passed. An empty fleet is a failure: it means neither the
    inventory nor mDNS produced a device, which is a misconfiguration, not a clean run."""
    return 0 if results and all(r.ok for r in results) else 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_deploy.py -q`
Expected: 10 passed.

- [ ] **Step 5: Lint and typecheck**

```bash
.venv/bin/python -m ruff check dev tests
.venv/bin/python -m mypy --strict dev tests
```

Expected: both clean. If ruff objects to the broad `except Exception`, the `# noqa: BLE001`
is already there and is deliberate — read the docstring before removing it.

- [ ] **Step 6: Commit**

```bash
git add dev/satellites/deploy.py tests/test_deploy.py
git commit -m "feat(deploy): deploy each satellite with rollback and fleet aggregation"
```

### Task 18: `dev/deploy_satellites.py` — the CLI

Thin wiring: resolve the fleet, deploy it, print a per-device table, exit non-zero if any
device failed. `--dry-run` prints the planned actions and touches nothing, so the script is
runnable by hand before it is trusted in CI.

**Files:**
- Create: `dev/deploy_satellites.py`
- Test: `tests/test_deploy.py` (extend — the fleet-resolution helper is pure)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_deploy.py`:

```python
def test_resolve_fleet_unions_the_file_and_discovery(tmp_path: Path) -> None:
    from dev.deploy_satellites import resolve_fleet

    inv = tmp_path / "satellites.yaml"
    inv.write_text("satellites:\n  - name: kitchen\n    host: 192.168.1.40\n    area: Kitchen\n")
    avahi = "=;eth0;IPv4;hallway;_wyoming._tcp;local;hallway.local;192.168.1.44;10700;\n"
    fleet = resolve_fleet(inv, discover=lambda: avahi)
    assert [(s.name, s.source) for s in fleet] == [("kitchen", "file"), ("hallway", "mdns")]


def test_resolve_fleet_skips_discovery_when_asked(tmp_path: Path) -> None:
    from dev.deploy_satellites import resolve_fleet

    inv = tmp_path / "satellites.yaml"
    inv.write_text("satellites:\n  - name: kitchen\n    host: 192.168.1.40\n")

    def _never() -> str:
        raise AssertionError("discovery should not have run")

    assert [s.name for s in resolve_fleet(inv, discover=_never, use_mdns=False)] == ["kitchen"]


def test_resolve_fleet_survives_a_broken_avahi(tmp_path: Path) -> None:
    # avahi-browse missing or the daemon down must not take the whole deploy with it —
    # the file is still authoritative.
    from dev.deploy_satellites import resolve_fleet

    inv = tmp_path / "satellites.yaml"
    inv.write_text("satellites:\n  - name: kitchen\n    host: 192.168.1.40\n")

    def _boom() -> str:
        raise OSError("avahi-browse: not found")

    assert [s.name for s in resolve_fleet(inv, discover=_boom)] == ["kitchen"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_deploy.py -k resolve_fleet -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dev.deploy_satellites'`.

- [ ] **Step 3: Write the CLI**

Create `dev/deploy_satellites.py`:

```python
"""Deploy the Alfred voice satellite fleet.

    uv run python -m dev.deploy_satellites --dry-run
    uv run python -m dev.deploy_satellites --inventory ~/code/alfred-deploy/satellites.yaml \
                                           --key ~/code/alfred-deploy/id_ed25519_satellites

Exits non-zero if any device failed. A partial rollout is reported as a failure, never as
a pass.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from dev.satellites import deploy as deploy_mod
from dev.satellites.inventory import Satellite, load_file, merge, parse_avahi
from dev.satellites.transport import DryRunTransport, SshTransport, Transport

app = typer.Typer(add_completion=False)
console = Console()

# Module-level, not inline defaults: a call in an argument default is evaluated once at
# import and trips ruff's B008.
DEFAULT_INVENTORY = Path("~/code/alfred-deploy/satellites.yaml").expanduser()
DEFAULT_KEY = Path("~/code/alfred-deploy/id_ed25519_satellites").expanduser()


def discover_via_avahi() -> str:
    """Raw `avahi-browse -rpt _wyoming._tcp` output."""
    proc = subprocess.run(
        ["avahi-browse", "-rpt", "_wyoming._tcp"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc.stdout


def resolve_fleet(
    inventory: Path,
    discover: Callable[[], str] = discover_via_avahi,
    *,
    use_mdns: bool = True,
) -> list[Satellite]:
    """The file ∪ mDNS. A discovery failure is a warning, not a stop: the file is still
    authoritative, and a broken avahi must not take the whole rollout with it."""
    from_file = load_file(inventory)
    if not use_mdns:
        return from_file
    try:
        found = parse_avahi(discover())
    except Exception as exc:  # noqa: BLE001 — discovery is best-effort by design
        logger.warning("mDNS discovery failed ({}) — using the inventory file alone", exc)
        return from_file
    fleet = merge(from_file, found)
    for s in fleet:
        if s.source == "mdns":
            logger.warning("{} ({}) answered mDNS but is not in {}", s.name, s.host, inventory)
        elif s.source == "file":
            logger.warning("{} ({}) is in {} but did not answer mDNS", s.name, s.host, inventory)
    return fleet


def _table(results: list[deploy_mod.DeviceResult]) -> Table:
    table = Table(title="alfred satellite deploy")
    table.add_column("device")
    table.add_column("host")
    table.add_column("result")
    table.add_column("detail", overflow="fold")
    for r in results:
        if r.ok:
            verdict = "[green]OK[/green]"
        elif r.rolled_back:
            verdict = "[yellow]FAILED — rolled back[/yellow]"
        else:
            verdict = "[red]FAILED[/red]"
        table.add_row(r.name, r.host, verdict, r.detail)
    return table


@app.command()
def main(
    checkout: Annotated[
        Path, typer.Option(help="Directory rsynced to /opt/alfred-satellite")
    ] = Path(),
    inventory: Annotated[Path, typer.Option(help="satellites.yaml")] = DEFAULT_INVENTORY,
    key: Annotated[Path, typer.Option(help="SSH key trusted by every Pi")] = DEFAULT_KEY,
    user: Annotated[str, typer.Option(help="SSH user on each Pi")] = "pi",
    mdns: Annotated[bool, typer.Option("--mdns/--no-mdns", help="Merge mDNS discovery")] = True,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the planned actions and touch nothing")
    ] = False,
) -> None:
    """Deploy the checkout to every satellite in the fleet."""
    fleet = resolve_fleet(inventory, use_mdns=mdns)
    if not fleet:
        console.print(
            f"[red]No satellites found — {inventory} is empty and mDNS found none.[/red]"
        )
        raise typer.Exit(code=1)

    logger.info("fleet: {}", ", ".join(f"{s.name}@{s.host}" for s in fleet))

    if dry_run:
        # The transport reports success for every step, so deploy_one logs each device as
        # "deployed". Say plainly that nothing happened before those lines scroll past.
        logger.warning("DRY RUN — no device will be contacted or changed")
        dry = DryRunTransport()
        deploy_mod.deploy_all(lambda _s: dry, fleet, checkout.resolve())
        console.print("[bold]Planned actions (dry run — nothing was changed)[/bold]")
        for action in dry.actions:
            console.print(f"  {action}")
        return

    ssh: Transport = SshTransport(key=key, user=user)
    results = deploy_mod.deploy_all(lambda _s: ssh, fleet, checkout.resolve())
    console.print(_table(results))

    code = deploy_mod.exit_code(results)
    if code:
        failed = ", ".join(r.name for r in results if not r.ok)
        console.print(f"[red]Deploy failed on: {failed}[/red]")
    raise typer.Exit(code=code)


if __name__ == "__main__":
    app()
```

`app()` raises `SystemExit` itself via `typer.Exit`, so it is not wrapped in `sys.exit()` —
that would swallow the exit code and always report success.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 23 passed (10 inventory + 10 deploy + 3 resolve_fleet).

- [ ] **Step 5: Prove `--dry-run` really is inert**

```bash
mkdir -p /tmp/sat-inv && printf 'satellites:\n  - name: fake\n    host: 10.255.255.1\n' > /tmp/sat-inv/satellites.yaml
.venv/bin/python -m dev.deploy_satellites --dry-run --no-mdns --inventory /tmp/sat-inv/satellites.yaml
```

Expected: a `DRY RUN — no device will be contacted or changed` warning, then a
`Planned actions` list naming `10.255.255.1` — the `mv`, the rsync, the `setup.sh`, the
restart, the two `is-active` checks and the port probe, in that order. `10.255.255.1` is
unroutable, so if any real SSH were attempted this would hang or error instead.

- [ ] **Step 6: Lint, typecheck, commit**

```bash
.venv/bin/python -m ruff check dev tests
.venv/bin/python -m mypy --strict dev tests
git add dev/deploy_satellites.py tests/test_deploy.py
git commit -m "feat(deploy): add the satellite fleet deploy CLI"
```

### Task 19: Satellite CI workflow

`alfred-satellite` has no workflows at all, so CI comes before the deploy job.

**Before writing this task's file lists, re-read the inventory recorded in Phase 1 Task 4
Step 2.** The globs below assume `scripts/*.sh`, `systemd/*.service` and
`config.env.example`; correct them to whatever that inventory actually showed.

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Confirm the paths this workflow will check**

```bash
cd ~/code/alfred-satellite/worktrees/feat-deploy-tooling
ls scripts/*.sh; ls systemd/*.service; ls config.env.example
```

Expected: each lists at least one file. If any path is wrong, fix the corresponding job
below to match rather than leaving a check that silently passes over nothing.

- [ ] **Step 2: Write the workflow**

Create `.github/workflows/ci.yml`:

```yaml
# Checks run on GitHub's runners; only `deploy` touches this house. A pull-request build
# must never be able to reach a Pi, and the surest way to guarantee that is for it to run
# on a machine with no route to one.

name: CI

on:
  push:
    branches: [main]
  pull_request:
    types: [opened, edited, synchronize, reopened]

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}

jobs:
  shell:
    name: shellcheck
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - name: shellcheck every script
        run: |
          set -euo pipefail
          shopt -s nullglob
          files=(scripts/*.sh)
          if [ ${#files[@]} -eq 0 ]; then
            echo "::error::no scripts/*.sh found — this check would pass over nothing"
            exit 1
          fi
          printf 'checking: %s\n' "${files[@]}"
          shellcheck "${files[@]}"

  units:
    name: systemd units parse
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - name: systemd-analyze verify
        run: |
          set -euo pipefail
          shopt -s nullglob
          units=(systemd/*.service)
          if [ ${#units[@]} -eq 0 ]; then
            echo "::error::no systemd/*.service found — this check would pass over nothing"
            exit 1
          fi
          printf 'verifying: %s\n' "${units[@]}"
          # Unit files reference binaries that do not exist on the runner; those warnings
          # are expected. Only a parse/syntax error should fail the job.
          for u in "${units[@]}"; do
            out=$(systemd-analyze verify "$u" 2>&1 || true)
            echo "$out"
            if echo "$out" | grep -Eq 'Failed to (parse|load)|Unknown (section|lvalue)|Invalid '; then
              echo "::error::$u does not parse"
              exit 1
            fi
          done

  config:
    name: config.env.example covers setup.sh
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      # A variable setup.sh reads but the example never documents is a silent
      # misconfiguration on a Pi nobody looks at.
      - name: every variable setup.sh reads is documented
        run: |
          set -euo pipefail
          declared=$(grep -oE '^[A-Z_][A-Z0-9_]*=' config.env.example | tr -d '=' | sort -u)
          read_vars=$(grep -oE '\$\{?[A-Z_][A-Z0-9_]*' scripts/setup.sh \
                      | tr -d '${' | sort -u)
          missing=""
          for v in $read_vars; do
            case "$v" in
              PATH|HOME|USER|PWD|SHELL|UID|EUID|IFS|BASH*|OSTYPE|RANDOM|LINENO|PS1|PS2|TERM) continue ;;
            esac
            if ! echo "$declared" | grep -qx "$v"; then
              missing="$missing $v"
            fi
          done
          if [ -n "$missing" ]; then
            echo "::error::setup.sh reads variables config.env.example does not declare:$missing"
            exit 1
          fi
          echo "every variable setup.sh reads is declared in config.env.example"

  python:
    name: lint, typecheck, tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v9
        with:
          python-version: "3.13"
          enable-cache: true
      - run: uv sync
      - run: uv run ruff check dev tests
      - run: uv run ruff format --check dev tests
      - run: uv run mypy --strict dev tests
      - run: uv run pytest -q

  pr-title:
    name: conventional PR title
    runs-on: ubuntu-latest
    steps:
      # The PR title becomes the squash commit, so it has to be a conventional commit line.
      - name: Validate conventional PR title
        if: github.event_name == 'pull_request'
        env:
          TITLE: ${{ github.event.pull_request.title }}
        run: |
          echo "PR title: $TITLE"
          if ! printf '%s' "$TITLE" | grep -qE '^(feat|fix|chore|docs|refactor|test|ci|perf)(\([a-zA-Z0-9._/ -]+\))?!?: .+$'; then
            echo "::error::PR title must be a conventional commit line: type(scope)!?: subject, type in feat|fix|chore|docs|refactor|test|ci|perf"
            exit 1
          fi
      - name: Not a PR — nothing to validate
        if: github.event_name != 'pull_request'
        run: echo "push event"

  # Job id `gate`, display name `ci-ok` — `needs.ci-ok.result` would parse as a
  # subtraction. Make `ci-ok` this repo's one required check.
  gate:
    name: ci-ok
    if: always()
    needs: [shell, units, config, python, pr-title]
    runs-on: ubuntu-latest
    steps:
      - name: All gates green?
        env:
          RESULTS: ${{ toJSON(needs) }}
        run: |
          echo "$RESULTS"
          if echo "$RESULTS" | grep -Eq '"result": "(failure|cancelled|skipped)"'; then
            echo "::error::a required job did not succeed"
            exit 1
          fi
```

- [ ] **Step 3: Verify the YAML parses**

```bash
python3 -c "import yaml; d=yaml.safe_load(open('.github/workflows/ci.yml')); print(list(d['jobs'])); assert d['jobs']['gate']['name']=='ci-ok'"
```

Expected: `['shell', 'units', 'config', 'python', 'pr-title', 'gate']`.

- [ ] **Step 4: Commit and open the PR**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add CI for scripts, units, config parity and the deploy tooling"
git push -u origin feat/deploy-tooling
gh pr create --title "feat(deploy): satellite fleet deployment tooling and CI" \
  --body "$(cat <<'EOF'
Phase 4 of the CD design (`alfred`'s `docs/superpowers/specs/2026-08-18-cd-local-runner-design.md`).

This repo had no CI at all. Adds:

- **CI** — shellcheck over `scripts/`, `systemd-analyze verify` on the unit files, a check
  that `config.env.example` declares every variable `setup.sh` reads, ruff/mypy/pytest over
  the new tooling, and the conventional-PR-title check the other repos run. Aggregated by
  `gate` (display name `ci-ok`) — make that the required check.
- **`dev/deploy_satellites.py`** — the deploy logic, as reviewable and runnable code rather
  than YAML. Inventory is the `satellites.yaml` file ∪ `avahi-browse` discovery; each
  device is rsynced, `setup.sh` run, both units restarted, then verified active with the
  Wyoming port probed; any failure restores the previous tree.
- **Every device is attempted even after one fails**, and a partial rollout exits non-zero.
  One unreachable Pi must not leave the rest of the house on an old build, and must not be
  reported as a pass either.
- `--dry-run` prints the planned actions per device and touches nothing.

SSH and rsync sit behind a `Transport` protocol, so inventory merging, rollback decisions
and aggregation are pure functions with unit tests (23 tests).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: After merge, make `ci-ok` the required check**

At `https://github.com/anirudhlath/alfred-satellite/settings/branches`, add a protection
rule for `main` requiring the status check named **`ci-ok`**. It matches the *display
name*, which is why the job id is `gate`.

### Task 20: The satellite deploy job

**Files:**
- Modify: `.github/workflows/ci.yml` (append a `deploy` job)

- [ ] **Step 1: Branch off the merged trunk**

```bash
cd ~/code/alfred-satellite
git fetch origin
git worktree add worktrees/ci-deploy-satellites -b ci/deploy-satellites origin/main
cd worktrees/ci-deploy-satellites
uv venv --python 3.13 && uv sync
```

- [ ] **Step 2: Append the deploy job**

Add to the end of `.github/workflows/ci.yml`:

```yaml
  deploy:
    name: deploy to the satellite fleet
    needs: [gate]
    # `always() &&` is load-bearing: without it a SKIPPED upstream job propagates down the
    # needs graph and this silently skips on every push even with `gate` green.
    if: >-
      ${{ always()
      && needs.gate.result == 'success'
      && github.event_name == 'push'
      && github.ref == 'refs/heads/main' }}
    runs-on: [self-hosted, linux, alfred-satellite]
    timeout-minutes: 30
    # Two rollouts at once would race over /opt/alfred-satellite.prev on every Pi.
    concurrency:
      group: deploy-satellites
      cancel-in-progress: false
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v9
        with:
          python-version: "3.13"
          enable-cache: true
      - run: uv sync

      # Every real decision lives in the tested script; this step is the whole job.
      # It exits non-zero if ANY device failed — a partial rollout is never a pass.
      - name: Deploy the fleet
        run: |
          uv run python -m dev.deploy_satellites \
            --checkout . \
            --inventory /home/anirudhlath/code/alfred-deploy/satellites.yaml \
            --key /home/anirudhlath/code/alfred-deploy/id_ed25519_satellites
```

`--checkout .` rsyncs the runner's checkout, so what lands on each Pi is exactly the commit
that triggered the run.

- [ ] **Step 3: Verify the wiring, including that no PR job is self-hosted**

```bash
python3 - <<'PY'
import yaml
d = yaml.safe_load(open('.github/workflows/ci.yml'))
dep = d['jobs']['deploy']
assert dep['needs'] == ['gate']
assert 'always()' in dep['if'] and "needs.gate.result == 'success'" in dep['if']
assert "refs/heads/main" in dep['if']
assert dep['concurrency']['cancel-in-progress'] is False
selfhosted = [n for n, j in d['jobs'].items()
              if isinstance(j.get('runs-on'), list) and 'self-hosted' in j['runs-on']]
assert selfhosted == ['deploy'], selfhosted
print("satellite deploy wiring OK")
PY
```

Expected: `satellite deploy wiring OK`.

- [ ] **Step 4: Dry-run against the real inventory on the box before merging**

The runner is on this machine, so the exact command the job will run can be rehearsed
inertly:

```bash
uv run python -m dev.deploy_satellites --dry-run \
  --checkout . \
  --inventory /home/anirudhlath/code/alfred-deploy/satellites.yaml \
  --key /home/anirudhlath/code/alfred-deploy/id_ed25519_satellites
```

Expected: a `Planned actions` block listing every Pi in `satellites.yaml`, plus any the
mDNS sweep discovered. Devices found only by mDNS and devices that did not answer mDNS
each log a warning — read them; they are the check that discovery and the file agree.

- [ ] **Step 5: Commit and open the PR**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: deploy the satellite fleet on merge to main"
git push -u origin ci/deploy-satellites
gh pr create --title "ci(deploy): roll out to the satellite fleet on every merge to main" \
  --body "$(cat <<'EOF'
Phase 4 of the CD design, second half. Adds a single `deploy` job on the self-hosted
`alfred-satellite` runner, gated on `ci-ok` and on the commit already being on `main`.

The job is one step — `python -m dev.deploy_satellites` — because every real decision lives
in the tested script merged previously. It exits non-zero if any device failed, so a
partial rollout is reported as a failure.

No pull-request job targets a self-hosted runner.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: After merge, watch the first real rollout**

```bash
gh run watch $(gh run list --workflow=ci.yml --branch=main --limit=1 --json databaseId --jq '.[0].databaseId')
```

Expected: the per-device table with every device `OK`. Then spot-check one Pi:

```bash
ssh -i ~/code/alfred-deploy/id_ed25519_satellites pi@<a-satellite-host> \
  'systemctl is-active wyoming-satellite wyoming-openwakeword; ls -ld /opt/alfred-satellite /opt/alfred-satellite.prev'
```

Expected: both `active`, and both directories present — `.prev` is the previous build kept
for rollback.

---

# Phase 5 — Documentation and deferred work

**PR title:** `docs(deploy): document continuous deployment to lath-server`

Depends on Phases 2b–4, so the docs describe what actually exists.

### Task 21: The CD section of `docs/deployment.md`

**Files:**
- Modify: `docs/deployment.md` (append a section; the existing 126 lines of manual
  deployment stay)

- [ ] **Step 1: Branch**

```bash
cd ~/code/alfred-deploy/alfred
git fetch origin
git worktree add worktrees/docs-cd -b docs/cd-local-runner origin/master
cd worktrees/docs-cd
```

- [ ] **Step 2: Append the section**

Add to the end of `docs/deployment.md`:

```markdown
## Continuous deployment

Every merge to `master` deploys to lath-server. Every merge to `alfred-home-service`'s
`main` fires a `repository_dispatch` that does the same. Every merge to
`alfred-satellite`'s `main` rolls out to the Pi fleet.

Design: `docs/superpowers/specs/2026-08-18-cd-local-runner-design.md`.

### The shape

Checks run on GitHub's runners; exactly one job per repo runs on a self-hosted runner, and
it cannot start until the `ci-ok` aggregate check has passed **and** the commit is already
on the trunk. A pull-request build can never reach this house.

### What an automated deploy does

1. Checks out `alfred` and `alfred-home-service` as **siblings** under the workspace — the
   fat image build stages `git ls-files` from both, resolving home-service as a sibling of
   the alfred checkout.
2. `uv sync` on Python 3.13.
3. `alfredctl doctor --offline --env-file ~/code/alfred-deploy/.env` — a config preflight,
   before the build, so a misconfigured box fails in seconds rather than after a full
   image build. Offline so a transient outage of an external endpoint cannot fail a deploy.
4. Records `docker image inspect -f '{{.Id}}' alfred:latest` as the rollback target.
5. `alfredctl build --tag alfred:latest`, then tags the same image `alfred:<sha>` so
   history is addressable.
6. Copies `docker-compose.yml` into `~/code/alfred-deploy/` and runs `docker compose up -d`
   from there.
7. `alfredctl smoke --attach --name alfred` — polls `/health`, then checks redis, the
   RediSearch modules and mosquitto inside the running container.
8. On any failure of 6 or 7: retags the recorded image back to `alfred:latest`, brings the
   stack back up, re-runs the smoke check, and fails the job red either way. A rollback
   that itself fails is louder, not quieter.

### The deploy workspace

`~/code/alfred-deploy/` holds operator-managed state that no workflow writes:

| Path | What |
|---|---|
| `.env` | `OPENROUTER_API_KEY`, `HA_TOKEN`, `OLLAMA_HOST`, … (0600) |
| `docker-compose.yml` | Copied from the checkout each deploy |
| `satellites.yaml` | Pi inventory: `name`, `host`, `port`, `area` |
| `id_ed25519_satellites` | SSH key trusted by every Pi (0600) |
| `alfred/`, `home-service/` | The manual-deploy checkouts (pull-only) |

No passphrase file: the secrets passphrase is generated and persisted in the `alfred_data`
volume on first boot.

**`docker-compose.yml` pins `name: alfred`.** Compose otherwise derives the project name
from the directory it runs in, so running it from `~/code/alfred-deploy/` would create a
project `alfred-deploy` with **empty** volumes — losing the secrets passphrase — while the
old container still held `:8081`. Do not remove that pin.

### The runners

| Install dir | Registered to | Label |
|---|---|---|
| `~/.local/share/github-runner/alfred` | `anirudhlath/alfred` | `alfred-deploy` |
| `~/.local/share/github-runner/alfred-satellite` | `anirudhlath/alfred-satellite` | `alfred-satellite` |

Both are installed as services (`./svc.sh install anirudhlath && ./svc.sh start`) so they
survive reboots. A third runner, `ha-home-panel`, predates these and is unrelated.

To install another, download the current `actions/runner` release into
`~/.local/share/github-runner/<repo>/`, then:

```bash
./config.sh --url https://github.com/anirudhlath/<repo> --token <REGISTRATION_TOKEN> \
            --name lath-server-<repo> --labels <label> --work _work --unattended --replace
sudo ./svc.sh install anirudhlath && sudo ./svc.sh start
```

Check health with `systemctl list-units 'actions.runner*'`.

### Rolling back by hand

Every deploy tags the image it built as `alfred:<sha>`, so any previous deploy is
recoverable:

```bash
docker images alfred --format '{{.Tag}}\t{{.CreatedSince}}'   # find the sha you want
docker tag alfred:<sha> alfred:latest
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

### Adding a satellite

1. Flash and network the Pi, then trust the deploy key:
   `ssh-copy-id -i ~/code/alfred-deploy/id_ed25519_satellites pi@<host>`
2. Add it to `~/code/alfred-deploy/satellites.yaml` with `name`, `host`, `port` and an
   `area` that matches a Home Assistant area name.
3. Rehearse: `uv run python -m dev.deploy_satellites --dry-run` from an `alfred-satellite`
   checkout.
4. Merge anything to `alfred-satellite`'s `main`, or run the same command without
   `--dry-run`.

A Pi that answers mDNS but is missing from `satellites.yaml` is still deployed to, with a
warning. So is one in the file that does not answer mDNS. Both sources exist because the
file is authoritative today while discovery earns trust; retiring the file is
`docs/backlog/medium/satellite-mdns-only-inventory.md`.

### When a deploy fails

| Symptom | Cause |
|---|---|
| `deploy` job **skipped** on a push | An upstream job skipped and the condition lost its `always() &&` lead. |
| `Preflight failed` | `~/code/alfred-deploy/.env` is missing a required value — the table names the row. |
| Smoke `health` fails, everything else unrun | The container did not come up; `docker logs alfred`. |
| Smoke passes but the house is stale | Check the image the container is running: `docker inspect alfred --format '{{.Image}}'`. |
| A new `alfred-deploy_*` volume appeared | The `name: alfred` pin was removed from `docker-compose.yml`. Restore it before anything else — the running stack is on empty volumes. |
| Satellite job fails with one device red | Read the table; the failed device was rolled back to `/opt/alfred-satellite.prev`. The rest of the fleet did deploy. |
```

- [ ] **Step 3: Commit**

```bash
git add docs/deployment.md
git commit -m "docs(deploy): document continuous deployment to lath-server"
```

### Task 22: CLAUDE.md updates

Three files, in two repos and one plain directory. `/home/anirudhlath/CLAUDE.md` is **not**
in a git repo — edit it directly, no PR.

**Files:**
- Modify: `CLAUDE.md` (in the `alfred` worktree, on the Task 21 branch)
- Modify: `CLAUDE.md` in `alfred-home-service` (its own PR)
- Modify: `/home/anirudhlath/CLAUDE.md` (direct edit)

- [ ] **Step 1: `alfred/CLAUDE.md`**

In the **Branching & PRs** section, after the line beginning `- CI gate is the single
\`ci-ok\` aggregate check`, add:

```markdown
- **Merging to `master` deploys.** The `deploy` job on the self-hosted `alfred-deploy`
  runner rebuilds the fat image on lath-server, restarts the stack from
  `~/code/alfred-deploy/`, smoke-checks it, and rolls back automatically on failure. See
  the "Continuous deployment" section of `docs/deployment.md`. A merge to
  `alfred-home-service` triggers the same deploy via `repository_dispatch`.
- The aggregate job's **id** is `gate` and its **display name** is `ci-ok`. Keep it that
  way: `needs.ci-ok.result` never evaluates (a hyphen parses as subtraction in a GitHub
  expression path), and the required-check setting matches the display name.
```

In the **Gotchas** section, append:

```markdown
- `docker-compose.yml` pins `name: alfred` — do NOT remove it. Compose otherwise derives
  the project name from the directory it runs in, and CD runs it from
  `~/code/alfred-deploy/`, which would create a project `alfred-deploy` with empty volumes
  (losing the secrets passphrase persisted in `alfred_data`) while the old container still
  held `:8081`. `container_name: alfred` is pinned for the same deploy, so
  `alfredctl smoke --attach --name alfred` can find it.
```

- [ ] **Step 2: Commit it on the docs branch**

```bash
git add CLAUDE.md
git commit -m "docs: note that merging to master deploys"
```

- [ ] **Step 3: `home-service/CLAUDE.md` — its own PR**

```bash
cd ~/code/alfred-deploy/home-service
git fetch origin
git worktree add ../home-service-worktrees/docs-cd -b docs/cd-note origin/main
cd ../home-service-worktrees/docs-cd
```

Add to its Branching/PRs section (or create one if absent):

```markdown
- **Merging to `main` redeploys Alfred.** home-service is baked into alfred's fat image and
  has no deployment of its own, so the `dispatch` job posts a `home-service-merged`
  `repository_dispatch` at `anirudhlath/alfred`; alfred's deploy job then rebuilds with
  both repos at their current trunks. See "Continuous deployment" in alfred's
  `docs/deployment.md`.
```

Then:

```bash
git add CLAUDE.md
git commit -m "docs: note that merging to main redeploys alfred"
git push -u origin docs/cd-note
gh pr create --title "docs: note that merging to main redeploys alfred" \
  --body "Phase 5 of the CD design. Records that a merge here fires a \`repository_dispatch\` at \`anirudhlath/alfred\`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 4: `/home/anirudhlath/CLAUDE.md` — direct edit, no PR**

That file is a plain file in a non-git directory. Add a section after **Services on this
box**:

```markdown
## Continuous deployment

Merges deploy to this box automatically. Runners live in
`~/.local/share/github-runner/<repo>/`, installed as systemd services:

| Runner | Repo | Label | Deploys |
|---|---|---|---|
| `ha-home-panel` | `anirudhlath/ha-home-panel` | `home-panel` | The wall tablet's panel bundle |
| `alfred` | `anirudhlath/alfred` | `alfred-deploy` | Rebuilds the fat image, restarts the stack |
| `alfred-satellite` | `anirudhlath/alfred-satellite` | `alfred-satellite` | rsyncs every Pi in the fleet |

`anirudhlath/alfred-home-service` has no runner — merging there fires a
`repository_dispatch` that runs alfred's deploy.

Operator state lives in `~/code/alfred-deploy/` (`.env`, `docker-compose.yml`,
`satellites.yaml`, `id_ed25519_satellites`) and **no workflow writes it**. Full runbook:
`~/code/alfred-deploy/alfred/docs/deployment.md`, "Continuous deployment".

`~/code/alfred-deploy/alfred/docker-compose.yml` pins `name: alfred`. Removing it would
give compose a new project name derived from the directory, creating empty volumes and
losing the secrets passphrase.

Check the runners: `systemctl list-units 'actions.runner*'`.
```

- [ ] **Step 5: Verify the edit landed**

```bash
grep -c 'Continuous deployment' /home/anirudhlath/CLAUDE.md
```

Expected: `1`.

### Task 23: Backlog tickets for the deferred work

Per §11 — tickets, not TODOs.

**Files:**
- Modify: `docs/backlog/low/registry-publish-images.md`
- Create: `docs/backlog/medium/deploy-env-from-github-secrets.md`
- Create: `docs/backlog/medium/satellite-mdns-only-inventory.md`

Work on the Task 21 branch (`docs/cd-local-runner`).

- [ ] **Step 1: Extend the registry ticket**

Append to `docs/backlog/low/registry-publish-images.md`:

```markdown
## Also covers the CD path (added 2026-08-18)

CD currently builds on the runner (`alfredctl build` → `docker compose up -d`), which
works and needs no registry auth. Publishing would let the deploy job pull a digest built
on a GitHub runner instead of spending minutes compiling a fat multi-stage image on
lath-server on every merge — and would make the deployed artifact byte-identical to the
one CI tested rather than a rebuild of the same source.

Shape: `container-build.yml` pushes to GHCR on merge to `master`; the deploy job pulls by
digest and `docker compose up -d`; the rollback target becomes the previous digest rather
than a local image id. See `docs/superpowers/specs/2026-08-18-cd-local-runner-design.md`
§11.
```

- [ ] **Step 2: Create the `.env`-from-secrets ticket**

Create `docs/backlog/medium/deploy-env-from-github-secrets.md`:

```markdown
# Render the Deploy `.env` from GitHub Secrets

## Summary

The deploy `.env` at `~/code/alfred-deploy/.env` is hand-managed on lath-server. Rendering
it from repository secrets at deploy time would make rotation a GitHub action rather than
an SSH session, and would leave the box holding no long-lived plaintext credentials.

## Context / Motivation

- `docs/superpowers/specs/2026-08-18-cd-local-runner-design.md` §11 deferred this: "A
  pre-placed file is the smallest thing that works; rotation via SSH is tolerable for now."
- The current design's safety property is that secrets never enter a workflow — the
  workflows reference `.env` only by path. Rendering from secrets **inverts** that, so it
  needs care: the file would be written by a job, and a compromised workflow could read
  every value.
- The secrets passphrase is out of scope either way: it is generated and persisted in the
  `alfred_data` volume on first boot (#158) and is not in `.env`.

## Proposed shape

- One repository secret per `.env` key, or a single `DEPLOY_ENV` secret holding the file.
- The deploy job writes it to a `0600` file in the runner's workspace and passes it to
  `alfredctl doctor --env-file` and compose's `env_file`.
- The file must not persist between runs — the runner's workspace is reused.

## Open questions

- Does writing the file from a workflow weaken the current guarantee enough to matter on a
  single-maintainer repo where the workflow and the box have the same owner?
- Rotation still requires a deploy to take effect. Is that better or worse than an SSH edit
  plus `docker compose up -d`?
```

- [ ] **Step 3: Create the mDNS-only ticket**

Create `docs/backlog/medium/satellite-mdns-only-inventory.md`:

```markdown
# Retire `satellites.yaml` in Favour of mDNS Discovery

## Summary

Satellite deployment merges a hand-maintained `~/code/alfred-deploy/satellites.yaml` with
`avahi-browse -rpt _wyoming._tcp` discovery. The intended end state is discovery alone —
adding a Pi should mean flashing it, not editing a file on lath-server.

## Context / Motivation

- `docs/superpowers/specs/2026-08-18-cd-local-runner-design.md` §7.3: "Both sources exist
  because the file is authoritative today while discovery earns trust. The intended end
  state is discovery alone; retiring the file is a ticket, not a TODO."
- `dev/satellites/inventory.py` in `alfred-satellite` already implements the union and
  marks each device's `source` as `file`, `mdns` or `both`. Every deploy logs a warning for
  any device that is not `both` — that log is the evidence this ticket waits on.

## Blocked on

Several consecutive rollouts where every device resolves as `both`. Until then a Pi that
stops advertising would silently drop out of the fleet, which is exactly what the file
prevents.

## What retiring it means

- `area` has no mDNS equivalent and is needed for room-aware commands, so it has to move
  into the Pi's own config and be advertised as a TXT record before the file can go.
- `load_file` and the merge stay useful for `--dry-run` against a hypothetical fleet; the
  deploy path stops consulting the file.
```

- [ ] **Step 4: Commit**

```bash
git add docs/backlog
git commit -m "docs(backlog): file the CD design's deferred work"
```

### Task 24: QA-backlog drills

Per §9 — a real deploy is the only thing that proves a deploy. These are deleted once
verified, per the workspace convention.

**Files:**
- Create: `docs/qa-backlog/first-live-deploy-lath-server.md`
- Create: `docs/qa-backlog/deploy-rollback-drill.md`
- Create: `docs/qa-backlog/satellite-deploy-with-offline-pi.md`

- [ ] **Step 1: Create the first-deploy drill**

Create `docs/qa-backlog/first-live-deploy-lath-server.md`:

```markdown
# First Live Deploy to lath-server

**Feature:** Continuous deployment (Phase 2)
**Priority:** high
**Type:** functional

## Prerequisites
- The `alfred-deploy` runner is Idle at github.com/anirudhlath/alfred/settings/actions/runners
- `~/code/alfred-deploy/.env` exists, 0600
- The live container is healthy before starting (`docker inspect alfred-alfred-1 --format '{{.State.Health.Status}}'`)

## Test Steps
1. Note the current state: `docker inspect alfred-alfred-1 --format '{{.Image}}'` and
   `docker volume ls | grep alfred`
2. Merge a trivial PR to `master` (a docs typo is enough)
3. `gh run watch` the resulting CI run
4. After it goes green: `docker ps --filter name=alfred`
5. `docker inspect alfred --format '{{.Image}} {{.Created}} {{.State.Health.Status}}'`
6. `docker volume ls | grep alfred`
7. `docker images alfred --format '{{.Tag}}'`
8. Open `http://localhost:8081` and send a message through the chat

## Expected Result
- Step 3: `gate` green, then `deploy to lath-server` green
- Step 4: exactly ONE container, named `alfred` (not `alfred-alfred-1`, not two)
- Step 5: a fresh `Created` timestamp and `healthy`
- Step 6: the SAME three volumes as step 1 — `alfred_alfred_data`, `alfred_alfred_models`,
  `alfred_redis_data`. **A new `alfred-deploy_*` volume means the `name: alfred` pin
  failed and the stack is running on empty state — stop and fix before anything else.**
- Step 7: `latest` plus a tag matching the merged commit sha
- Step 8: a normal reply, proving the passphrase survived and stored credentials still
  decrypt
```

- [ ] **Step 2: Create the rollback drill**

Create `docs/qa-backlog/deploy-rollback-drill.md`:

```markdown
# Deploy Rollback Drill

**Feature:** Continuous deployment (Phase 2, §5.4 step 8)
**Priority:** high
**Type:** functional / failure-mode

## Prerequisites
- `first-live-deploy-lath-server.md` has passed, so a known-good `alfred:latest` exists
- Note its id: `docker image inspect -f '{{.Id}}' alfred:latest`

## Test Steps
1. On a branch, break the runtime *without* breaking CI — e.g. make the web channel raise
   on startup in a path no unit test covers. Confirm `ci-ok` still goes green on the PR.
2. Merge it.
3. Watch the deploy job.
4. After the job finishes: `docker image inspect -f '{{.Id}}' alfred:latest`
5. `docker inspect alfred --format '{{.State.Health.Status}}'`
6. Read the job log for the rollback step's messages
7. Revert the breaking commit and let the next deploy restore the trunk

## Expected Result
- Step 3: the `Verify` step FAILS (smoke's `health` check times out), the `Roll back` step
  runs, and the job ends **red**
- Step 4: the id matches the one recorded in Prerequisites — the previous image is back
- Step 5: `healthy`
- Step 6: `::error::deploy failed; rolled back to sha256:… and it verified`
- The job is red even though the rollback succeeded. A recovered deploy is still a failed
  deploy.

## Also worth doing once
Delete every `alfred:*` image first and confirm a first-deploy failure reports
`::error::deploy failed and there is no previous image to restore` and fails red, rather
than pretending a rollback happened.
```

- [ ] **Step 3: Create the offline-Pi drill**

Create `docs/qa-backlog/satellite-deploy-with-offline-pi.md`:

```markdown
# Satellite Rollout with a Pi Offline

**Feature:** Satellite deployment (Phase 4, §7.4)
**Priority:** high
**Type:** functional / failure-mode

## Prerequisites
- At least two satellites in `~/code/alfred-deploy/satellites.yaml`
- The `alfred-satellite` runner is Idle
- Every device passes a normal rollout first

## Test Steps
1. Power off exactly one Pi. Confirm: `ping -c1 <that-host>` fails.
2. Merge a trivial PR to `alfred-satellite`'s `main`
3. Watch the run
4. Read the per-device table in the job log
5. Check the job's exit status
6. On a Pi that stayed up: `systemctl is-active wyoming-satellite wyoming-openwakeword`
   and `ls -ld /opt/alfred-satellite /opt/alfred-satellite.prev`
7. Power the offline Pi back on and re-run the workflow

## Expected Result
- Step 4: the table lists EVERY device. The powered-off one is `FAILED` with a detail
  naming the transport failure; the others are `OK`.
- Step 5: the job is **red**. A partial rollout is a failure, never a pass.
- Step 6: both units active, and `.prev` present — the healthy Pis really did deploy. This
  is the property that matters: one dead Pi did not leave the rest of the house on an old
  build.
- Step 7: all devices `OK`, job green.

## Also worth checking
The device that was offline should appear in the file-only warning
(`… is in satellites.yaml but did not answer mDNS`) during step 3.
```

- [ ] **Step 4: Commit and open the PR**

```bash
git add docs/qa-backlog
git commit -m "docs(qa): add the CD verification drills"
git push -u origin docs/cd-local-runner
gh pr create --title "docs(deploy): document continuous deployment to lath-server" \
  --body "$(cat <<'EOF'
Phase 5 of the CD design (`docs/superpowers/specs/2026-08-18-cd-local-runner-design.md`).

- `docs/deployment.md` gains a Continuous deployment section: the runner runbook, what an
  automated deploy does step by step, the deploy workspace, rolling back by hand, rotating
  a secret, adding a satellite, and a failure-triage table.
- `CLAUDE.md` records that merging deploys, and that `docker-compose.yml`'s `name: alfred`
  pin is load-bearing.
- Backlog: extends `registry-publish-images` to cover the CD path; files
  `deploy-env-from-github-secrets` and `satellite-mdns-only-inventory`.
- QA backlog: the three drills that only a real deploy can prove — first live deploy,
  rollback, and a satellite rollout with a Pi powered off.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Clean up the worktrees as their PRs merge**

Per the repo's worktree discipline — delete each worktree as soon as its PR merges.

```bash
cd ~/code/alfred-deploy/alfred
git worktree list
git worktree remove worktrees/<name>
git worktree prune
```

The `ci/local-runner-cd` worktree carrying the spec and this plan is the last to go — merge
it once the plan is agreed, so the spec and plan land on `master` alongside the work.
