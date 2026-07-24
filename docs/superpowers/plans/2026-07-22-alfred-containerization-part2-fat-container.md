# Alfred Containerization Part 2 — Fat Container + alfredctl Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the single fat OCI image (redis + mosquitto + 6 core services + home-service
under tini→runner) and the `alfredctl` typer launcher, so `uv run alfredctl up` boots a full
Alfred on Docker or Apple `container` with one command.

**Architecture:** `tini` PID 1 execs `python -m runner --no-reload`; the Part 1 generalized
supervisor (already merged on this branch) starts redis/mosquitto with readiness gates, then
the Python services. All writable state under `ALFRED_DATA_DIR=/data`; model caches under
`ALFRED_MODELS_DIR=/models` + `HF_HOME=/models/hf`. `alfredctl` stages a build context from
`git ls-files` (so gitignored personal files can never enter the image), builds per-branch
images, and wires env/volumes/ports per runtime.

**Tech Stack:** python:3.13-slim-bookworm, redis:8-bookworm (official image bundles
redisearch.so/rejson.so — validated on arm64), Debian mosquitto, tini, uv, typer + rich.

**Spec:** `docs/superpowers/specs/2026-07-19-alfred-containerization-design.md` (§14 phases 5–8)

## Global Constraints

- Python 3.13+; `mypy --strict` clean; `ruff check` + `ruff format` clean (line-length 100);
  full pytest suite green (1129+ currently passing).
- OCI naming: `Containerfile`, never `Dockerfile`. Docker + Apple `container` first-class,
  podman best-effort.
- Only `:8081` published by default. `:1883` and `:8000` opt-in flags. `:6379` never published.
- `alfredctl` imports NOTHING from `core/`, `bus/`, `domains/`, `sdk/` — pure orchestration
  (stdlib + typer + rich + dotenv only).
- The image must never contain: `.env`, `secrets/`, gitignored personal preference/profile
  files. Guaranteed structurally: the build context is staged from
  `git ls-files -co --exclude-standard` output only.
- Base images pinned by name: `python:3.13-slim-bookworm`, `redis:8-bookworm`, `node:22-slim`
  (glibc/OS parity between redis binaries and runtime base — both Debian 12).
- Do not add the container build to the `ci-ok` required aggregate. New CI workflow is
  separate + path-filtered.
- Redis stream names come from `shared.streams`; never hardcode.
- Loguru for logging (never stdlib logging in new code). `runner/supervisor.py` currently
  uses stdlib `logging` — leave that file's existing style alone.
- pytest-asyncio is in `asyncio_mode = "auto"` — async tests need no decorator.

## Carry-forward items this plan MUST land (from `.superpowers/sdd/progress.md`)

1. cryptfile fail-loud when `ALFRED_SECRETS_BACKEND=cryptfile` explicitly set and no
   `ALFRED_SECRETS_PASSPHRASE` (Task 2).
2. Runner returns non-zero when an infra ready_check times out; infra path gets test
   coverage; readiness probes gathered in parallel (Task 4).
3. Image build must exclude private prefs (`core/memory/preferences/*.md`, `profile/*.md`)
   — solved structurally by git-ls-files staging (Task 6a) + defense-in-depth
   `.dockerignore` entries (Task 5).
4. `seed_defaults()` prefs/profile seeding is inert (`.example/` copies never activate) —
   fixed by `.example` promotion (Task 3).

Out of scope (already filed / to file in Task 9): seed-mode dummy **fixtures pack** (fake
user, fake HA snapshot — backlog), data migration from pre-Part-1 layouts (never),
registry publishing, non-gated embedding model default.

---

### Task 1: `models_root()` + route Piper/SpeakerID model caches

Part 1 consolidated *state*; model *caches* still leak: `core/voice/tts.py` downloads
voices into the package tree (`core/voice/models/`), `core/voice/speaker_id.py` uses a
cwd-relative `Path("data")`. The container mounts a shared model-cache volume at
`/models`; these must resolve through one env-controlled root.

**Files:**
- Modify: `shared/config.py` (after `data_mode()`)
- Modify: `core/voice/tts.py`
- Modify: `core/voice/speaker_id.py`
- Test: `tests/shared/test_config_data.py` (append)
- Test: `tests/core/voice/test_model_paths.py` (create; `tests/core/voice/` may need `__init__.py` — check sibling test dirs and match)

**Interfaces:**
- Produces: `shared.config.models_root() -> Path` — env `ALFRED_MODELS_DIR`, default
  `data_root() / "models"`, mkdir'd, resolved. Used by Task 5's image ENV
  (`ALFRED_MODELS_DIR=/models`) and later by any model-downloading code.

- [ ] **Step 1: Write failing tests**

Append to `tests/shared/test_config_data.py`:

```python
def test_models_root_defaults_under_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ALFRED_MODELS_DIR", raising=False)
    assert config.models_root() == (tmp_path / "models").resolve()
    assert config.models_root().is_dir()


def test_models_root_env_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_MODELS_DIR", str(tmp_path / "cache"))
    assert config.models_root() == (tmp_path / "cache").resolve()
```

Create `tests/core/voice/test_model_paths.py`:

```python
"""Model caches must resolve under models_root(), never the package tree or cwd."""

from __future__ import annotations

from pathlib import Path

import pytest  # noqa: TC002


def test_piper_default_model_dir_under_models_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_MODELS_DIR", str(tmp_path))
    from core.voice.tts import _default_model_dir

    assert _default_model_dir() == (tmp_path / "piper").resolve()


def test_speaker_id_model_dir_under_models_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_MODELS_DIR", str(tmp_path))
    from core.voice.speaker_id import _model_dir

    assert _model_dir() == (tmp_path / "spkrec-ecapa-voxceleb").resolve()
```

- [ ] **Step 2: Run tests, verify they fail** (`.venv/bin/python -m pytest tests/shared/test_config_data.py tests/core/voice/test_model_paths.py -q` → import/attribute errors)

- [ ] **Step 3: Implement**

`shared/config.py`, after `data_mode()`:

```python
def models_root() -> Path:
    """Root for downloaded model caches (env ``ALFRED_MODELS_DIR``, default ``<data>/models``).

    Caches, not state: safe to share across worktrees/containers and to delete.
    """
    override = os.getenv("ALFRED_MODELS_DIR", "").strip()
    root = Path(override).resolve() if override else data_root() / "models"
    root.mkdir(parents=True, exist_ok=True)
    return root
```

`core/voice/tts.py` — replace the class-level `DEFAULT_MODEL_DIR` with a lazy resolver
(module-level, so env changes and tests see it):

```python
def _default_model_dir() -> Path:
    from shared.config import models_root

    return models_root() / "piper"
```

In `PiperTTS.__init__`, change the signature default `model_dir: Path = DEFAULT_MODEL_DIR`
to `model_dir: Path | None = None` and resolve first thing:

```python
        model_dir = model_dir if model_dir is not None else _default_model_dir()
```

Delete the `DEFAULT_MODEL_DIR` class attribute. Grep for external users first
(`grep -rn "DEFAULT_MODEL_DIR" --include='*.py' .`) — update any caller to pass nothing
(the new default). Keep `DEFAULT_VOICE`.

`core/voice/speaker_id.py` — replace the module constant
`_MODEL_DIR = Path("data") / "models" / "spkrec-ecapa-voxceleb"` with:

```python
def _model_dir() -> Path:
    from shared.config import models_root

    return models_root() / "spkrec-ecapa-voxceleb"
```

and change the `savedir=str(_MODEL_DIR)` call site to `savedir=str(_model_dir())`.

- [ ] **Step 4: Run tests → PASS; then full gates:** `.venv/bin/python -m pytest -x -q`,
  `mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`,
  `ruff check . && ruff format --check .`

- [ ] **Step 5: Commit** — `feat(config): add models_root() and route Piper/SpeakerID caches through it`

---

### Task 2: cryptfile fail-loud (explicit backend requires passphrase)

**Files:**
- Modify: `shared/secrets.py` (`configure_backend` only)
- Test: `tests/shared/test_secrets_backend.py` (exists from Part 1 Task 7 — append; if named differently, find it: `grep -rln select_backend_name tests/`)

**Interfaces:** none new. Behavior contract:
- `ALFRED_SECRETS_BACKEND=cryptfile` **explicitly set** + empty/unset
  `ALFRED_SECRETS_PASSPHRASE` → `RuntimeError` at `configure_backend()` (import time in the
  container — fail-loud is the point; the image sets the backend explicitly, `alfredctl`
  always supplies a passphrase).
- Backend **auto-detected** cryptfile (non-darwin, no explicit env) + no passphrase →
  keep working with the `"alfred-insecure-default"` key but emit a loguru warning
  (Linux CI/devcontainer imports must not explode; tests swap in InMemoryKeyring anyway).

- [ ] **Step 1: Write failing tests** (append; match the existing file's fixture style —
  it monkeypatches env and calls `configure_backend()`/`select_backend_name()` directly):

```python
def test_explicit_cryptfile_without_passphrase_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.delenv("ALFRED_SECRETS_PASSPHRASE", raising=False)
    with pytest.raises(RuntimeError, match="ALFRED_SECRETS_PASSPHRASE"):
        secrets.configure_backend()


def test_explicit_cryptfile_with_passphrase_configures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.setenv("ALFRED_SECRETS_PASSPHRASE", "hunter2")
    secrets.configure_backend()  # must not raise
```

(If the existing test module reassigns the global keyring, restore it the way the existing
tests do — follow the file's established teardown pattern.)

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement** — replace `configure_backend()` body:

```python
def configure_backend() -> None:
    """Configure the active keyring backend based on select_backend_name()."""
    if select_backend_name() != "cryptfile":
        return  # leave keyring's auto-detected native backend in place
    from keyrings.cryptfile.cryptfile import CryptFileKeyring

    from shared.config import data_path

    explicit = os.getenv("ALFRED_SECRETS_BACKEND", "").strip().lower() == "cryptfile"
    passphrase = os.getenv("ALFRED_SECRETS_PASSPHRASE", "")
    if not passphrase:
        if explicit:
            raise RuntimeError(
                "ALFRED_SECRETS_BACKEND=cryptfile requires ALFRED_SECRETS_PASSPHRASE. "
                "Set it in the environment (alfredctl generates and persists one for you)."
            )
        # Auto-detected on a bare Linux host (CI, devcontainer): stay importable, but
        # credentials stored this way are only obfuscated, not protected.
        from loguru import logger

        logger.warning(
            "cryptfile keyring auto-selected without ALFRED_SECRETS_PASSPHRASE — "
            "using an INSECURE default key; do not store real credentials"
        )
        passphrase = "alfred-insecure-default"

    secrets_dir = data_path("secrets")
    secrets_dir.mkdir(parents=True, exist_ok=True)
    kr = CryptFileKeyring()
    kr.file_path = str(secrets_dir / "keyring.cfg")
    kr.keyring_key = passphrase
    keyring.set_keyring(kr)
```

- [ ] **Step 4: Tests + gates → PASS**
- [ ] **Step 5: Commit** — `feat(secrets): fail loud when explicit cryptfile backend lacks a passphrase`

---

### Task 3: `seed_defaults()` — promote `.example` templates to active files

Today `seed_defaults()` rglob-copies `core/memory/preferences/.example/*.md` to
`data/preferences/.example/*.md`, which `MemoryReader` never reads (top-level glob only) —
seeding is inert for prefs/profile. Fix: when a template lives under a `.example/`
directory, its destination drops that path component (lands top-level, active). Never
overwrite an existing destination. Routines already seed correctly (top-level YAML) —
must not regress.

**Files:**
- Modify: `core/memory/paths.py` (`seed_defaults()` and/or its copy helper — read the
  current implementation first; keep its structure, add the `.example` mapping)
- Test: existing seed tests file from Part 1 Task 2/3 (`grep -rln seed_defaults tests/`) — append

**Interfaces:** unchanged signature `seed_defaults() -> None`.

- [ ] **Step 1: Write failing tests** (append, matching existing seed-test style):

```python
def test_seed_promotes_example_templates_to_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    seed_defaults()
    prefs = tmp_path / "preferences"
    # every packaged .example template must land as an ACTIVE top-level file
    pkg_examples = Path(paths.__file__).parent / "preferences" / ".example"
    for tpl in pkg_examples.glob("*.md"):
        assert (prefs / tpl.name).is_file(), tpl.name
    # and no inert .example/ copy in the data dir
    assert not (prefs / ".example").exists()


def test_seed_never_overwrites_existing_active_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    prefs = tmp_path / "preferences"
    prefs.mkdir(parents=True)
    pkg_examples = Path(paths.__file__).parent / "preferences" / ".example"
    name = next(pkg_examples.glob("*.md")).name
    (prefs / name).write_text("user-owned content")
    seed_defaults()
    assert (prefs / name).read_text() == "user-owned content"
```

Adjust imports to the existing test file's conventions (`from core.memory import paths`,
`from core.memory.paths import seed_defaults`). If `profile/.example/` templates exist in
the package, assert the same promotion for `profile/` (check
`ls core/memory/profile/.example/` first; skip the assertion if the dir doesn't exist).

- [ ] **Step 2: Run → FAIL** (`.example` currently copied verbatim)

- [ ] **Step 3: Implement** — in the copy loop of `seed_defaults()`, compute the
destination relative path and strip a `.example` component:

```python
        rel = src.relative_to(template_root)
        parts = [p for p in rel.parts if p != ".example"]
        dest = data_root_dir.joinpath(*parts)
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
```

(Adapt names to the real function body — the guarantee to preserve: only `*.md`/`*.yaml`
globs, idempotent, never overwrites. New guarantee: no `.example/` directories are created
under the data dir.)

- [ ] **Step 4: Tests + gates → PASS** (run the whole memory paths test file plus the
  Part 1 completeness gate `tests/core/memory/test_no_package_state_paths.py`)
- [ ] **Step 5: Commit** — `fix(memory): seed .example templates as active files, not inert copies`

---

### Task 4: Runner container hardening (exit code, parallel gates, adaptive infra)

Four changes:
(a) readiness-gate failure → supervisor exits non-zero (container PID 1 must not report
success when redis never came up);
(b) infra readiness probes awaited in parallel;
(c) redis command adapts: `redis-stack-server` when present (native macOS dev), else
`redis-server` + explicit `--loadmodule` for module `.so` files found in
`ALFRED_REDIS_MODULES_DIR` (default `/usr/local/lib/redis/modules`) — with
persistence flags derived from `data_mode()`;
(d) mosquitto config generated under the data dir (currently hardcodes
`/etc/mosquitto/mosquitto.conf`, which exists on neither macOS nor our image).

**Files:**
- Modify: `runner/supervisor.py` (`run()` only)
- Modify: `runner/__main__.py` (`_infra_services()` + new helpers)
- Test: `tests/runner/test_supervisor.py`, `tests/runner/test_main.py` (locate the Part 1
  runner tests: `ls tests/runner/`; append to the right files)

**Interfaces:**
- Produces: `runner.__main__._redis_command(redis_dir: Path) -> list[str]`,
  `runner.__main__._write_mosquitto_conf() -> Path` (module-level, unit-testable).
- `Supervisor.run()` returns `1` when a ready gate fails (was `0`).

- [ ] **Step 1: Write failing tests**

Supervisor exit code (append to the supervisor test file; mirror the existing
deterministic unit-test style — Part 1 tests call `_await_ready`/`_start_process`
directly; for `run()` use a real spec with an always-false ready_check and a fast
timeout by monkeypatching `Supervisor._await_ready` — keep it simple):

```python
async def test_run_returns_nonzero_when_ready_gate_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _never_ready() -> bool:
        return False

    spec = ServiceSpec(name="redis", command=["sleep", "5"], ready_check=_never_ready)
    sup = Supervisor([spec])

    async def _fast_gate(self: Supervisor, svc: object, timeout: float = 30.0) -> bool:
        return False

    monkeypatch.setattr(Supervisor, "_await_ready", _fast_gate)
    assert await sup.run() == 1
```

Redis command + mosquitto conf (append to the runner main test file):

```python
def test_redis_command_container_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_DATA_MODE", "persistent")
    modules = tmp_path / "mods"
    modules.mkdir()
    (modules / "redisearch.so").touch()
    monkeypatch.setenv("ALFRED_REDIS_MODULES_DIR", str(modules))
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    cmd = _redis_command(tmp_path / "redis")
    assert cmd[0] == "redis-server"
    assert "--appendonly" in cmd and cmd[cmd.index("--appendonly") + 1] == "yes"
    assert str(modules / "redisearch.so") in cmd
    assert "--bind" in cmd


def test_redis_command_ephemeral_disables_persistence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_DATA_MODE", "ephemeral")
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: None)
    cmd = _redis_command(tmp_path / "redis")
    assert cmd[cmd.index("--appendonly") + 1] == "no"


def test_redis_command_prefers_stack_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("runner.__main__.shutil.which", lambda _: "/opt/redis-stack-server")
    assert _redis_command(tmp_path / "redis")[0] == "redis-stack-server"


def test_mosquitto_conf_generated_under_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_DATA_MODE", "ephemeral")
    conf = _write_mosquitto_conf()
    assert conf == tmp_path / "mosquitto" / "mosquitto.conf"
    text = conf.read_text()
    assert "listener 1883" in text
    assert "persistence false" in text
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement**

`runner/supervisor.py` `run()` — replace the sequential gate block with a parallel gather
and track failure:

```python
        infra = [m for m in self._managed if m.spec.ready_check is not None]
        rest = [m for m in self._managed if m.spec.ready_check is None]

        gate_failed = False
        monitor_tasks = [asyncio.create_task(self._monitor(m)) for m in infra]
        if infra:
            ready = await asyncio.gather(*(self._await_ready(m) for m in infra))
            if not all(ready):
                gate_failed = True
                self._shutdown.set()
        if not self._shutdown.is_set():
            monitor_tasks += [asyncio.create_task(self._monitor(m)) for m in rest]
```

and the return:

```python
        crashed = any(svc.restart_count > svc.spec.max_restarts for svc in self._managed)
        return 1 if (crashed or gate_failed) else 0
```

`runner/__main__.py` — add `import shutil` and `from shared.config import data_mode,
data_path` (extend the existing import), then:

```python
def _redis_command(redis_dir: Path) -> list[str]:
    """Redis argv: redis-stack-server when installed (native dev), else redis-server
    with explicit module loads (container). Persistence follows ALFRED_DATA_MODE."""
    redis_dir.mkdir(parents=True, exist_ok=True)
    if shutil.which("redis-stack-server"):
        return ["redis-stack-server", "--dir", str(redis_dir)]
    cmd = ["redis-server", "--dir", str(redis_dir), "--bind", "127.0.0.1"]
    if data_mode() == "persistent":
        cmd += ["--appendonly", "yes"]
    else:
        cmd += ["--save", "", "--appendonly", "no"]
    modules_dir = Path(os.getenv("ALFRED_REDIS_MODULES_DIR", "/usr/local/lib/redis/modules"))
    for mod in ("redisearch.so", "rejson.so"):
        path = modules_dir / mod
        if path.exists():
            cmd += ["--loadmodule", str(path)]
    return cmd


def _write_mosquitto_conf() -> Path:
    """Generate a mosquitto config under the data dir (persistence per data mode)."""
    conf = data_path("mosquitto", "mosquitto.conf")
    persistence = "true" if data_mode() == "persistent" else "false"
    conf.write_text(
        "listener 1883 0.0.0.0\n"
        "allow_anonymous true\n"
        f"persistence {persistence}\n"
        f"persistence_location {conf.parent}/\n"
        "log_dest stdout\n"
    )
    return conf
```

In `_infra_services()`, replace the redis/mosquitto specs:

```python
    return [
        ServiceSpec(
            name="redis",
            command=_redis_command(data_root() / "redis"),
            ready_check=_redis_ready,
        ),
        ServiceSpec(
            name="mosquitto",
            command=["mosquitto", "-c", str(_write_mosquitto_conf())],
            ready_check=_mqtt_ready,
        ),
        ServiceSpec(
            name="home-service",
            command=["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"],
        ),
    ]
```

(delete the old inline `redis_dir` mkdir — `_redis_command` owns it now).

- [ ] **Step 4: Tests + gates → PASS**
- [ ] **Step 5: Commit** — `feat(runner): non-zero exit on failed ready gates, parallel readiness, adaptive redis/mosquitto commands`

---

### Task 5: Fat Containerfile (+ .dockerignore hardening) with build validation

**Files:**
- Rewrite: `Containerfile`
- Modify: `.dockerignore` (defense-in-depth), delete `.containerignore` (redundant — the
  staged-context build never consults ignore files; keep exactly one for manual builds)
- No pytest for this task; the test IS the build + in-container asserts (Step 3).

**Interfaces:**
- Produces: image contract used by Tasks 6–8: paths `/app` (source, on PYTHONPATH),
  `/app/sdk` (alfred_sdk importable), `/srv/home-service` (app importable), ENV as below,
  `ENTRYPOINT ["tini","--","python","-m","runner","--no-reload"]`, EXPOSE 8081.
- **Build context layout (staged by alfredctl in Task 6a, manually here):** context root
  contains `alfred/` (this repo's files) and `home-service/` (sibling repo's files);
  build with `-f alfred/Containerfile`.

- [ ] **Step 1: Write the new `Containerfile`:**

```dockerfile
# Alfred — single fat OCI image: redis + mosquitto + 6 core services + home-service,
# supervised by tini → python -m runner (ALFRED_MANAGE_INFRA=1).
#
# Build context = a STAGED workspace dir containing alfred/ and home-service/,
# produced from `git ls-files` so gitignored files (.env, secrets, personal
# preferences) can never enter the image. Build via:  uv run alfredctl build
#
# Base pins: python:3.13-slim-bookworm and redis:8-bookworm are both Debian 12,
# so the copied redis binaries + modules link against matching glibc/openssl.

FROM node:22-slim AS webbuild
WORKDIR /web
COPY alfred/web/package.json alfred/web/package-lock.json ./
RUN npm ci
COPY alfred/web/ ./
RUN npm run build

FROM redis:8-bookworm AS redis

FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# tini: PID 1 (zombie reaping + signal forwarding for the multi-process container)
# mosquitto(+clients): MQTT edge broker; libgomp1: RediSearch OpenMP runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini mosquitto mosquitto-clients libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Redis 8 (official image) bundles redisearch/rejson modules — copy binaries + modules
COPY --from=redis /usr/local/bin/redis-server /usr/local/bin/redis-cli /usr/local/bin/
COPY --from=redis /usr/local/lib/redis/modules/ /usr/local/lib/redis/modules/

WORKDIR /app

# Dependency layer first (cache-friendly): install deps only, not the project
COPY alfred/pyproject.toml /app/pyproject.toml
RUN uv pip install --system --no-cache -r pyproject.toml \
        --extra voice --extra memory --extra integrations
COPY home-service/pyproject.toml /srv/home-service/pyproject.toml
RUN uv pip install --system --no-cache -r /srv/home-service/pyproject.toml

# Source trees (run from source via PYTHONPATH — one copy, no site-packages duplicate)
COPY alfred/bus/ /app/bus/
COPY alfred/core/ /app/core/
COPY alfred/domains/ /app/domains/
COPY alfred/runner/ /app/runner/
COPY alfred/sdk/ /app/sdk/
COPY alfred/shared/ /app/shared/
COPY alfred/telemetry/ /app/telemetry/
COPY home-service/app/ /srv/home-service/app/
COPY home-service/alfred_ext/ /srv/home-service/alfred_ext/

COPY --from=webbuild /web/dist /app/web/dist

# /app: monorepo packages · /app/sdk: alfred_sdk for home-service · /srv/home-service: app
ENV PYTHONPATH=/app:/app/sdk:/srv/home-service \
    PYTHONUNBUFFERED=1 \
    ALFRED_MANAGE_INFRA=1 \
    ALFRED_DATA_DIR=/data \
    ALFRED_MODELS_DIR=/models \
    HF_HOME=/models/hf \
    ALFRED_SECRETS_BACKEND=cryptfile

EXPOSE 8081

# Model downloads on a cold cache can take minutes — generous start period
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8081/health', timeout=3)"]

ENTRYPOINT ["tini", "--", "python", "-m", "runner", "--no-reload"]
```

- [ ] **Step 2: Harden `.dockerignore`** (defense-in-depth for anyone running a manual
`docker build`; the staged path never reads it). Append:

```
# Personal (gitignored) memory content — must never enter an image
core/memory/preferences/*.md
core/memory/profile/*.md
!core/memory/preferences/.example
!core/memory/profile/.example
core/voice/models/
secrets/
```

Delete `.containerignore` (`git rm .containerignore`).

- [ ] **Step 3: Build + validate (Docker on this machine; this validates arm64 too since
the host is Apple Silicon).** Stage a context manually:

```bash
STAGE=$(mktemp -d)
mkdir -p "$STAGE/alfred" "$STAGE/home-service"
git ls-files -z -co --exclude-standard | while IFS= read -r -d '' f; do
  [ -f "$f" ] && mkdir -p "$STAGE/alfred/$(dirname "$f")" && cp "$f" "$STAGE/alfred/$f"
done
cd /Users/anirudhlath/code/private/alfred/home-service && git ls-files -z -co --exclude-standard | while IFS= read -r -d '' f; do
  [ -f "$f" ] && mkdir -p "$STAGE/home-service/$(dirname "$f")" && cp "$f" "$STAGE/home-service/$f"
done
cd - && docker build -t alfred:part2-dev -f "$STAGE/alfred/Containerfile" "$STAGE"
```

Expected: build succeeds (torch/speechbrain wheels exist for linux/arm64; if `piper-tts`
or a transitive wheel is missing on arm64, STOP and report — do not silently drop the
voice extra).

Then assert the image contract:

```bash
docker run --rm alfred:part2-dev python -c "import bus, core.reflex, core.channels, runner, alfred_sdk, app.server; print('imports ok')"
docker run --rm alfred:part2-dev sh -c "redis-server --version && mosquitto -h 2>&1 | head -1 && tini --version"
docker run --rm alfred:part2-dev sh -c "redis-server --port 7777 --loadmodule /usr/local/lib/redis/modules/redisearch.so --daemonize no & sleep 1; redis-cli -p 7777 MODULE LIST | grep -i search && redis-cli -p 7777 shutdown nosave"
```

All three must succeed. Also verify no personal files:
`docker run --rm alfred:part2-dev sh -c "ls /app/core/memory/preferences/ /app/core/memory/profile/ 2>/dev/null"`
→ must show only `.example` dirs (and any tracked files), no `personal.md`, and
`/app/.env` must not exist.

- [ ] **Step 4: Commit** — `feat(container): fat Containerfile — redis+mosquitto+core+home-service under tini/runner`
  (commit `Containerfile`, `.dockerignore`, deletion of `.containerignore`)

---

### Task 6a: `alfredctl` foundation — staging, runtime abstraction, build command

**Files:**
- Create: `alfredctl/__init__.py` (empty docstring module)
- Create: `alfredctl/staging.py`
- Create: `alfredctl/runtime.py`
- Create: `alfredctl/main.py` (typer app: `build` command only in this task)
- Modify: `pyproject.toml` (deps + script + packages + mypy)
- Modify: `.github/workflows/ci.yml` (mypy-targets: add `alfredctl/`)
- Test: `tests/alfredctl/__init__.py`? — match how `tests/shared/` is laid out (no
  `__init__.py` there → don't add one), `tests/alfredctl/test_staging.py`,
  `tests/alfredctl/test_runtime.py`

**Interfaces (consumed by Tasks 6b/7):**
- `alfredctl.staging.repo_root() -> Path` — `git rev-parse --show-toplevel` from cwd.
- `alfredctl.staging.workspace_root() -> Path` — parent of the MAIN checkout:
  `git rev-parse --git-common-dir` → resolve → `.git`'s repo dir's parent (works from
  worktrees).
- `alfredctl.staging.stage_context(dest: Path) -> Path` — stages `alfred/` +
  `home-service/` from `git ls-files -z -co --exclude-standard`; raises
  `FileNotFoundError` with a clone hint if `workspace_root()/home-service` is missing.
- `alfredctl.runtime.Runtime` — frozen dataclass: `name: str` (`docker|container|podman`),
  `exe: str`.
- `alfredctl.runtime.detect(preferred: str | None) -> Runtime` — order: explicit
  preferred → `container` (darwin only) → `docker` → `podman`; `RuntimeError` if none.
- `alfredctl.runtime.branch_slug() -> str` — current branch sanitized
  `[^a-z0-9-]+` → `-`, lowercased, trimmed to 40 chars, fallback `"detached"`.
- `alfredctl.runtime.image_tag() -> str` = `f"alfred:{branch_slug()}"`,
  `alfredctl.runtime.container_name() -> str` = `f"alfred-{branch_slug()}"`.
- `alfredctl.runtime.host_gateway(rt: Runtime) -> str` — docker →
  `host.docker.internal`, podman → `host.containers.internal`, container → first host of
  the `default` network subnet from `container network inspect default` (parse JSON
  `subnet`; fallback `"192.168.64.1"`).
- `alfredctl.runtime.trusted_subnet(rt: Runtime) -> str` — docker `172.16.0.0/12`,
  podman `10.88.0.0/16`, container `192.168.64.0/24`.

- [ ] **Step 1: pyproject wiring.** Base deps add `"typer>=0.15"`, `"rich>=13.0"`.
Add:

```toml
[project.scripts]
alfredctl = "alfredctl.main:app"
```

Extend `[tool.setuptools.packages.find] include` with `"alfredctl*"`. Extend the
pytest `testpaths` with `"tests"` if not already covered (it is — root `tests/` is in
testpaths). Update `.github/workflows/ci.yml` `mypy-targets` to
`"alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/"`.
Run `uv pip install -e ".[dev,memory,voice,integrations]"` to pick up the script + deps,
and `uv lock` if the lockfile is stale (`uv lock --check` first).

- [ ] **Step 2: Write failing tests**

`tests/alfredctl/test_staging.py`:

```python
"""stage_context() must include tracked+untracked files and exclude gitignored ones."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest  # noqa: TC002

from alfredctl import staging


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture()
def fake_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for repo in ("alfred", "home-service"):
        root = tmp_path / repo
        root.mkdir()
        _git(root, "init", "-q")
        (root / ".gitignore").write_text(".env\nsecret.md\n")
        (root / "kept.py").write_text("x = 1\n")
        (root / ".env").write_text("SECRET=1\n")
        (root / "secret.md").write_text("personal\n")
        _git(root, "add", "-A")
        _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        (root / "untracked_new.py").write_text("y = 2\n")  # new file, not ignored
    monkeypatch.setattr(staging, "repo_root", lambda: tmp_path / "alfred")
    monkeypatch.setattr(staging, "workspace_root", lambda: tmp_path)
    return tmp_path


def test_stage_includes_tracked_and_untracked(fake_workspace: Path, tmp_path: Path) -> None:
    dest = staging.stage_context(tmp_path / "stage")
    assert (dest / "alfred" / "kept.py").is_file()
    assert (dest / "alfred" / "untracked_new.py").is_file()
    assert (dest / "home-service" / "kept.py").is_file()


def test_stage_excludes_gitignored(fake_workspace: Path, tmp_path: Path) -> None:
    dest = staging.stage_context(tmp_path / "stage")
    assert not (dest / "alfred" / ".env").exists()
    assert not (dest / "alfred" / "secret.md").exists()


def test_stage_missing_home_service_raises(
    fake_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    shutil.rmtree(fake_workspace / "home-service")
    with pytest.raises(FileNotFoundError, match="home-service"):
        staging.stage_context(tmp_path / "stage")
```

`tests/alfredctl/test_runtime.py`:

```python
from __future__ import annotations

import pytest  # noqa: TC002

from alfredctl import runtime


def test_detect_prefers_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime.shutil, "which", lambda name: f"/bin/{name}")
    assert runtime.detect("podman").name == "podman"


def test_detect_order_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.setattr(runtime.shutil, "which", lambda name: f"/bin/{name}")
    assert runtime.detect(None).name == "container"


def test_detect_skips_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime.shutil, "which", lambda name: "/bin/docker" if name == "docker" else None
    )
    assert runtime.detect(None).name == "docker"


def test_detect_none_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="No container runtime"):
        runtime.detect(None)


def test_slug_sanitizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_current_branch", lambda: "worktree-feat+Container/Z")
    assert runtime.branch_slug() == "worktree-feat-container-z"


def test_gateway_per_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    assert runtime.host_gateway(runtime.Runtime("docker", "docker")) == "host.docker.internal"
    assert (
        runtime.host_gateway(runtime.Runtime("podman", "podman")) == "host.containers.internal"
    )
```

- [ ] **Step 3: Run → FAIL** (module doesn't exist)

- [ ] **Step 4: Implement**

`alfredctl/staging.py`:

```python
"""Stage a clean OCI build context from git metadata.

The context contains only files git would track (tracked + untracked-not-ignored),
so gitignored content — .env, secrets/, personal memory files, virtualenvs — can
never enter the image, regardless of ignore-file support in the active runtime.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

CLONE_HINT = (
    "home-service repo not found at {path}.\n"
    "Clone it next to the alfred repo:\n"
    "  git clone https://github.com/anirudhlath/alfred-home-service {path}"
)


def repo_root() -> Path:
    """Root of the current checkout (worktree-aware)."""
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True
    )
    return Path(out.stdout.strip())


def workspace_root() -> Path:
    """Parent directory of the MAIN checkout (where sibling repos live).

    Uses --git-common-dir so invocations from linked worktrees still resolve the
    main repository location.
    """
    out = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(out.stdout.strip()).parent.parent


def _listed_files(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "-co", "--exclude-standard"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return [f for f in out.stdout.split("\0") if f]


def _copy_repo(repo: Path, dest: Path) -> None:
    for rel in _listed_files(repo):
        src = repo / rel
        if not src.is_file():  # ls-files can list deleted-but-tracked paths
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)


def stage_context(dest: Path) -> Path:
    """Stage alfred/ + home-service/ into *dest* and return it."""
    home_service = workspace_root() / "home-service"
    if not home_service.is_dir():
        raise FileNotFoundError(CLONE_HINT.format(path=home_service))
    if dest.exists():
        shutil.rmtree(dest)
    _copy_repo(repo_root(), dest / "alfred")
    _copy_repo(home_service, dest / "home-service")
    return dest
```

`alfredctl/runtime.py`:

```python
"""Container runtime detection and per-runtime knowledge (gateways, subnets, naming)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Runtime:
    name: str  # docker | container | podman
    exe: str


_DETECT_ORDER_DARWIN = ("container", "docker", "podman")
_DETECT_ORDER_OTHER = ("docker", "podman")


def detect(preferred: str | None) -> Runtime:
    """Resolve the container runtime: explicit choice, else platform preference order."""
    if preferred:
        exe = shutil.which(preferred)
        if exe is None:
            raise RuntimeError(f"Requested runtime {preferred!r} not found on PATH")
        return Runtime(preferred, exe)
    order = _DETECT_ORDER_DARWIN if sys.platform == "darwin" else _DETECT_ORDER_OTHER
    for name in order:
        exe = shutil.which(name)
        if exe is not None:
            return Runtime(name, exe)
    raise RuntimeError(
        "No container runtime found. Install Docker, Apple container, or Podman."
    )


def _current_branch() -> str:
    out = subprocess.run(
        ["git", "branch", "--show-current"], check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def branch_slug() -> str:
    branch = _current_branch() or "detached"
    slug = re.sub(r"[^a-z0-9-]+", "-", branch.lower()).strip("-")
    return slug[:40] or "detached"


def image_tag() -> str:
    return f"alfred:{branch_slug()}"


def container_name() -> str:
    return f"alfred-{branch_slug()}"


def host_gateway(rt: Runtime) -> str:
    """Address at which the container reaches the HOST (for Ollama/LM Studio/HA)."""
    if rt.name == "docker":
        return "host.docker.internal"
    if rt.name == "podman":
        return "host.containers.internal"
    return _apple_vmnet_gateway(rt)


def _apple_vmnet_gateway(rt: Runtime) -> str:
    try:
        out = subprocess.run(
            [rt.exe, "network", "inspect", "default"],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(out.stdout)
        entry = payload[0] if isinstance(payload, list) else payload
        subnet = str(entry.get("subnet", ""))
        if subnet:
            base = subnet.split("/")[0].rsplit(".", 1)[0]
            return f"{base}.1"
    except Exception:  # noqa: BLE001 — any failure falls back to the documented default
        pass
    return "192.168.64.1"


def trusted_subnet(rt: Runtime) -> str:
    """Container-side source subnet to add to ALFRED_TRUSTED_NETWORKS."""
    return {
        "docker": "172.16.0.0/12",
        "podman": "10.88.0.0/16",
        "container": "192.168.64.0/24",
    }[rt.name]
```

`alfredctl/main.py` (build only; 6b extends):

```python
"""alfredctl — build and run the Alfred fat container on Docker/Apple container/Podman."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from alfredctl import runtime as rt
from alfredctl import staging

app = typer.Typer(help="Alfred container launcher", no_args_is_help=True)
console = Console()

RuntimeOpt = Annotated[
    str | None, typer.Option("--runtime", help="docker | container | podman (default: auto)")
]


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    return subprocess.run(cmd, check=True, **kwargs)  # type: ignore[call-overload]


@app.command()
def build(
    runtime: RuntimeOpt = None,
    tag: Annotated[str | None, typer.Option(help="Image tag (default alfred:<branch>)")] = None,
) -> None:
    """Build the fat image from a git-staged context (alfred + home-service)."""
    r = rt.detect(runtime)
    image = tag or rt.image_tag()
    with tempfile.TemporaryDirectory(prefix="alfred-ctx-") as tmp:
        ctx = staging.stage_context(Path(tmp) / "ctx")
        console.print(f"Building [bold]{image}[/bold] with {r.name}…")
        _run([r.exe, "build", "-t", image, "-f", str(ctx / "alfred" / "Containerfile"), str(ctx)])
    console.print(f"[green]Built {image}[/green]")


if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Tests + gates → PASS.** Also run
  `uv run alfredctl build --help` (typer wiring sanity) — do NOT run a real build here
  (Task 5 already validated the image; alfredctl's real build is exercised in the final
  smoke).
- [ ] **Step 6: Commit** — `feat(alfredctl): staging, runtime detection, and build command`

---

### Task 6b: `alfredctl up / down / logs / shell / urls`

**Files:**
- Create: `alfredctl/launch.py` (arg assembly — pure, unit-testable)
- Modify: `alfredctl/main.py` (add commands)
- Test: `tests/alfredctl/test_launch.py`

**Interfaces:**
- `alfredctl.launch.LaunchPlan` — frozen dataclass: `run_args: list[str]` (full argv
  after the exe), `url_hint: str`, `name: str`, `image: str`.
- `alfredctl.launch.build_plan(rt_: Runtime, *, mode: str, persist: Path | None, models: Path, hf_cache: Path | None, expose_ha: bool, expose_home: bool, port: int, extra_env: list[str], env_file: Path | None, passphrase: str) -> LaunchPlan`.
- Env semantics (implemented in `build_plan`):
  - `--env-file` (default: repo `.env` if it exists) parsed with
    `dotenv.dotenv_values`; each key passed as `-e KEY=VALUE`. **Localhost rewrite:** for
    keys `OLLAMA_HOST`, `LMSTUDIO_HOST`, `HA_HOST`, `OTEL_EXPORTER_OTLP_ENDPOINT`, any
    `localhost`/`127.0.0.1` in the value is replaced with `host_gateway(rt)`.
  - Always set: `ALFRED_DATA_MODE=<mode>`, `ALFRED_SECRETS_PASSPHRASE=<passphrase>`,
    `ALFRED_TRUSTED_NETWORKS=<user value + "," + trusted_subnet(rt)>` (comma-join, skip
    empty), `HF_TOKEN` passthrough from host env if set.
  - `extra_env` items (`KEY=VALUE`) appended last (win).
- Volume semantics: models dir always mounted at `/models` (default
  `~/.cache/alfred/models`, created if missing). `--hf-cache PATH` additionally mounts
  PATH read-write at `/models/hf` (overrides the default HF subdir — pass as its own
  `-v`). `persist` mounted at `/data` only when mode==persistent (default:
  `<repo_root>/data`).
- Port semantics: docker/podman → `-p {port}:8081` (+ `-p 1883:1883` if expose_ha,
  `-p 8000:8000` if expose_home); Apple container → no `-p` flags ever;
  docker on linux → extra `--add-host=host.docker.internal:host-gateway`.
- `url_hint`: docker/podman → `http://localhost:{port}`; container →
  `"resolve-ip"` sentinel (the `up` command resolves the live IP after start via
  `container inspect <name>` and prints `http://<ip>:8081`).

- [ ] **Step 1: Write failing tests** (`tests/alfredctl/test_launch.py`; pure — no
subprocess):

```python
from __future__ import annotations

from pathlib import Path

import pytest  # noqa: TC002

from alfredctl.launch import build_plan
from alfredctl.runtime import Runtime

DOCKER = Runtime("docker", "docker")
APPLE = Runtime("container", "container")


def _plan(rt=DOCKER, **kw):  # type: ignore[no-untyped-def]
    defaults = dict(
        mode="ephemeral",
        persist=None,
        models=Path("/m"),
        hf_cache=None,
        expose_ha=False,
        expose_home=False,
        port=8081,
        extra_env=[],
        env_file=None,
        passphrase="pp",
    )
    defaults.update(kw)
    return build_plan(rt, **defaults)  # type: ignore[arg-type]


def test_only_8081_published_by_default() -> None:
    args = _plan().run_args
    assert args.count("-p") == 1
    assert "8081:8081" in args
    assert not any("6379" in a for a in args)


def test_apple_container_publishes_nothing() -> None:
    assert "-p" not in _plan(rt=APPLE).run_args


def test_expose_flags_add_ports() -> None:
    args = _plan(expose_ha=True, expose_home=True).run_args
    assert "1883:1883" in args and "8000:8000" in args


def test_persistent_mounts_data(tmp_path: Path) -> None:
    args = _plan(mode="persistent", persist=tmp_path).run_args
    assert f"{tmp_path}:/data" in args


def test_ephemeral_does_not_mount_data() -> None:
    assert not any(a.endswith(":/data") for a in _plan().run_args)


def test_models_always_mounted() -> None:
    assert any(a.endswith(":/models") for a in _plan().run_args)


def test_localhost_rewritten_to_gateway(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OLLAMA_HOST=http://localhost:11434\nHA_TOKEN=abc\n")
    args = _plan(env_file=env_file).run_args
    assert "OLLAMA_HOST=http://host.docker.internal:11434" in args
    assert "HA_TOKEN=abc" in args


def test_trusted_subnet_injected() -> None:
    args = _plan().run_args
    assert any(a.startswith("ALFRED_TRUSTED_NETWORKS=") and "172.16.0.0/12" in a for a in args)


def test_mode_and_passphrase_set() -> None:
    args = _plan(mode="seed").run_args
    assert "ALFRED_DATA_MODE=seed" in args
    assert "ALFRED_SECRETS_PASSPHRASE=pp" in args
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement `alfredctl/launch.py`:**

```python
"""Assemble the runtime `run` invocation for one Alfred container."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

from alfredctl.runtime import Runtime, container_name, host_gateway, image_tag, trusted_subnet

_GATEWAY_REWRITE_KEYS = ("OLLAMA_HOST", "LMSTUDIO_HOST", "HA_HOST", "OTEL_EXPORTER_OTLP_ENDPOINT")


@dataclass(frozen=True)
class LaunchPlan:
    run_args: list[str]
    url_hint: str
    name: str
    image: str


def _env_pairs(
    rt: Runtime,
    mode: str,
    env_file: Path | None,
    extra_env: list[str],
    passphrase: str,
) -> list[str]:
    gateway = host_gateway(rt)
    merged: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        merged.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    for key in _GATEWAY_REWRITE_KEYS:
        if key in merged:
            merged[key] = merged[key].replace("localhost", gateway).replace("127.0.0.1", gateway)
    trusted = ",".join(x for x in (merged.get("ALFRED_TRUSTED_NETWORKS", ""), trusted_subnet(rt)) if x)
    merged["ALFRED_TRUSTED_NETWORKS"] = trusted
    merged["ALFRED_DATA_MODE"] = mode
    merged["ALFRED_SECRETS_PASSPHRASE"] = passphrase
    if os.getenv("HF_TOKEN"):
        merged.setdefault("HF_TOKEN", os.environ["HF_TOKEN"])
    for item in extra_env:
        key, _, value = item.partition("=")
        merged[key] = value
    pairs: list[str] = []
    for key, value in merged.items():
        pairs += ["-e", f"{key}={value}"]
    return pairs


def build_plan(
    rt: Runtime,
    *,
    mode: str,
    persist: Path | None,
    models: Path,
    hf_cache: Path | None,
    expose_ha: bool,
    expose_home: bool,
    port: int,
    extra_env: list[str],
    env_file: Path | None,
    passphrase: str,
) -> LaunchPlan:
    name = container_name()
    image = image_tag()
    args = ["run", "--detach", "--name", name]
    if rt.name != "container":
        args += ["-p", f"{port}:8081"]
        if expose_ha:
            args += ["-p", "1883:1883"]
        if expose_home:
            args += ["-p", "8000:8000"]
        if rt.name == "docker" and sys.platform == "linux":
            args += ["--add-host", "host.docker.internal:host-gateway"]
    args += ["-v", f"{models}:/models"]
    if hf_cache is not None:
        args += ["-v", f"{hf_cache}:/models/hf"]
    if mode == "persistent" and persist is not None:
        args += ["-v", f"{persist}:/data"]
    args += _env_pairs(rt, mode, env_file, extra_env, passphrase)
    args += [image]
    url = "resolve-ip" if rt.name == "container" else f"http://localhost:{port}"
    return LaunchPlan(run_args=args, url_hint=url, name=name, image=image)
```

- [ ] **Step 4: Add commands to `alfredctl/main.py`:**

```python
@app.command()
def up(
    runtime: RuntimeOpt = None,
    mode: Annotated[str, typer.Option(help="persistent | ephemeral | seed")] = "persistent",
    persist: Annotated[Path | None, typer.Option(help="Host dir for /data (persistent mode)")] = None,
    models: Annotated[Path | None, typer.Option(help="Host dir for the model cache volume")] = None,
    hf_cache: Annotated[Path | None, typer.Option(help="Existing HF cache to mount at /models/hf")] = None,
    expose_ha: Annotated[bool, typer.Option("--expose-ha", help="Publish :1883 (HA edge broker)")] = False,
    expose_home: Annotated[bool, typer.Option("--expose-home", help="Publish :8000 (home-service)")] = False,
    port: Annotated[int, typer.Option(help="Host port for the web UI (docker/podman)")] = 8081,
    env: Annotated[list[str], typer.Option("--env", "-e", help="Extra KEY=VALUE for the container")] = [],  # noqa: B006
    do_build: Annotated[bool, typer.Option("--build/--no-build", help="Build the image first")] = True,
) -> None:
    """Start this branch's Alfred container (build first if needed)."""
    r = rt.detect(runtime)
    if mode not in ("persistent", "ephemeral", "seed"):
        raise typer.BadParameter("mode must be persistent | ephemeral | seed")
    if do_build:
        build(runtime=r.name, tag=None)
    repo = staging.repo_root()
    models_dir = models or Path.home() / ".cache" / "alfred" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    persist_dir = (persist or repo / "data").resolve() if mode == "persistent" else None
    if persist_dir is not None:
        persist_dir.mkdir(parents=True, exist_ok=True)
    env_file = repo / ".env"
    plan = launch.build_plan(
        r,
        mode=mode,
        persist=persist_dir,
        models=models_dir.resolve(),
        hf_cache=hf_cache.resolve() if hf_cache else None,
        expose_ha=expose_ha,
        expose_home=expose_home,
        port=port,
        extra_env=list(env),
        env_file=env_file if env_file.is_file() else None,
        passphrase=_passphrase(mode, persist_dir),
    )
    _run([r.exe, "rm", "-f", plan.name], check=False)
    _run([r.exe, *plan.run_args])
    console.print(f"[green]{plan.name} started[/green] → {_resolve_url(r, plan)}")


def _passphrase(mode: str, persist_dir: Path | None) -> str:
    """Secrets passphrase: env wins; persistent mode persists a generated one (0600)."""
    if os.getenv("ALFRED_SECRETS_PASSPHRASE"):
        return os.environ["ALFRED_SECRETS_PASSPHRASE"]
    if mode == "persistent" and persist_dir is not None:
        marker = persist_dir / ".secrets-passphrase"
        if marker.is_file():
            return marker.read_text().strip()
        value = secrets.token_urlsafe(32)
        marker.write_text(value + "\n")
        marker.chmod(0o600)
        return value
    return secrets.token_urlsafe(32)  # ephemeral/seed: fresh per run


def _resolve_url(r: rt.Runtime, plan: launch.LaunchPlan) -> str:
    if plan.url_hint != "resolve-ip":
        return plan.url_hint
    try:
        out = subprocess.run(
            [r.exe, "inspect", plan.name], check=True, capture_output=True, text=True
        )
        payload = json.loads(out.stdout)
        entry = payload[0] if isinstance(payload, list) else payload
        networks = entry.get("networks") or []
        address = str(networks[0].get("address", "")) if networks else ""
        ip = address.split("/")[0]
        if ip:
            return f"http://{ip}:8081"
    except Exception:  # noqa: BLE001
        pass
    return "http://<container-ip>:8081 (container inspect failed — check `container ls`)"


@app.command()
def down(runtime: RuntimeOpt = None) -> None:
    """Stop and remove this branch's container."""
    r = rt.detect(runtime)
    _run([r.exe, "rm", "-f", rt.container_name()], check=False)
    console.print(f"[green]{rt.container_name()} removed[/green]")


@app.command()
def logs(
    runtime: RuntimeOpt = None,
    follow: Annotated[bool, typer.Option("--follow", "-f")] = False,
) -> None:
    """Stream container logs."""
    r = rt.detect(runtime)
    cmd = [r.exe, "logs"] + (["-f"] if follow else []) + [rt.container_name()]
    subprocess.run(cmd, check=False)


@app.command()
def shell(runtime: RuntimeOpt = None) -> None:
    """Exec an interactive shell inside the container."""
    r = rt.detect(runtime)
    subprocess.run([r.exe, "exec", "-it", rt.container_name(), "bash"], check=False)


@app.command()
def urls(runtime: RuntimeOpt = None) -> None:
    """Print the reachable URL(s) for the running container."""
    r = rt.detect(runtime)
    plan = launch.LaunchPlan(
        run_args=[], url_hint="resolve-ip" if r.name == "container" else "http://localhost:8081",
        name=rt.container_name(), image=rt.image_tag(),
    )
    console.print(_resolve_url(r, plan))
```

Add the needed imports to `main.py` (`import json`, `import os`, `import secrets`,
`from alfredctl import launch`) and give `_run` a `check: bool = True` parameter
(`def _run(cmd: list[str], check: bool = True) -> ...` → `subprocess.run(cmd, check=check)`).
Note `build(runtime=r.name, tag=None)` calls the typer command as a plain function —
fine because both params are plain options.

- [ ] **Step 5: Tests + gates → PASS** (`pytest tests/alfredctl -q`, mypy incl.
  `alfredctl/`, ruff)
- [ ] **Step 6: Commit** — `feat(alfredctl): up/down/logs/shell/urls with per-runtime wiring`

---

### Task 7: `alfredctl smoke`

**Files:**
- Create: `alfredctl/smoke.py`
- Modify: `alfredctl/main.py` (add `smoke` command)
- Test: `tests/alfredctl/test_smoke.py`

**Interfaces:**
- `alfredctl.smoke.SmokeCheck` — dataclass: `name: str`, `passed: bool`, `detail: str`.
- `alfredctl.smoke.run_checks(exe: str, name: str, base_url: str, timeout: float = 300.0) -> list[SmokeCheck]`
  — pure-ish; subprocess/http via injected small helpers so tests can monkeypatch.
- Checks (in order):
  1. `health` — poll `GET {base_url}/health` until 200 or timeout (2s interval,
     `urllib.request`; this is a bounded startup gate, not steady-state polling).
  2. `redis` — `exec <name> redis-cli ping` → `PONG`.
  3. `redisearch` — `exec <name> redis-cli MODULE LIST` contains `search`.
  4. `mqtt` — `exec <name> mosquitto_pub -h localhost -t alfred/smoke -m ok` → rc 0.
  5. `spa` — `GET {base_url}/` returns 200 with `text/html`.
  6. `data-dir` — `exec <name> sh -c "ls /data/scratchpad.md /data/routines"` rc 0
     (state consolidation actually landed in the container).
- `smoke` command: `up` in `--mode seed` (unless `--attach` targets a running container),
  run checks, print a rich table, `down` (unless `--keep`), exit 1 on any failure.

- [ ] **Step 1: Write failing tests** — monkeypatch the exec/http helpers:

```python
from __future__ import annotations

import pytest  # noqa: TC002

from alfredctl import smoke


def test_all_checks_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "_http_get", lambda url, timeout=3.0: (200, "text/html", "<html>"))
    monkeypatch.setattr(smoke, "_exec_in", lambda exe, name, *cmd: (0, "PONG\nsearch"))
    checks = smoke.run_checks("docker", "alfred-x", "http://localhost:8081", timeout=1.0)
    assert all(c.passed for c in checks)
    assert [c.name for c in checks] == [
        "health", "redis", "redisearch", "mqtt", "spa", "data-dir",
    ]


def test_health_timeout_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "_http_get", lambda url, timeout=3.0: (503, "", ""))
    monkeypatch.setattr(smoke, "_exec_in", lambda exe, name, *cmd: (0, "PONG\nsearch"))
    monkeypatch.setattr(smoke, "_POLL_INTERVAL", 0.01)
    checks = smoke.run_checks("docker", "alfred-x", "http://x", timeout=0.05)
    assert checks[0].name == "health" and not checks[0].passed


def test_redis_failure_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "_http_get", lambda url, timeout=3.0: (200, "text/html", "ok"))

    def _exec(exe: str, name: str, *cmd: str) -> tuple[int, str]:
        return (1, "") if cmd[0] == "redis-cli" else (0, "ok")

    monkeypatch.setattr(smoke, "_exec_in", _exec)
    checks = smoke.run_checks("docker", "alfred-x", "http://x", timeout=1.0)
    by_name = {c.name: c for c in checks}
    assert not by_name["redis"].passed
    assert by_name["mqtt"].passed
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Implement `alfredctl/smoke.py`:**

```python
"""Containerized smoke checks: is a running Alfred container actually alive?"""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

_POLL_INTERVAL = 2.0


@dataclass(frozen=True)
class SmokeCheck:
    name: str
    passed: bool
    detail: str


def _http_get(url: str, timeout: float = 3.0) -> tuple[int, str, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.headers.get("content-type", ""), resp.read(2048).decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, "", ""
    except Exception as e:  # noqa: BLE001
        return 0, "", str(e)


def _exec_in(exe: str, name: str, *cmd: str) -> tuple[int, str]:
    proc = subprocess.run([exe, "exec", name, *cmd], capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout + proc.stderr


def run_checks(exe: str, name: str, base_url: str, timeout: float = 300.0) -> list[SmokeCheck]:
    checks: list[SmokeCheck] = []

    deadline = time.monotonic() + timeout
    status = 0
    while time.monotonic() < deadline:
        status, _, _ = _http_get(f"{base_url}/health")
        if status == 200:
            break
        time.sleep(_POLL_INTERVAL)
    checks.append(SmokeCheck("health", status == 200, f"GET /health → {status}"))
    if status != 200:
        return checks  # nothing else can pass; report what we have

    rc, out = _exec_in(exe, name, "redis-cli", "ping")
    checks.append(SmokeCheck("redis", rc == 0 and "PONG" in out, out.strip()[:80]))

    rc, out = _exec_in(exe, name, "redis-cli", "MODULE", "LIST")
    checks.append(SmokeCheck("redisearch", rc == 0 and "search" in out.lower(), "MODULE LIST"))

    rc, out = _exec_in(exe, name, "mosquitto_pub", "-h", "localhost", "-t", "alfred/smoke", "-m", "ok")
    checks.append(SmokeCheck("mqtt", rc == 0, out.strip()[:80] or "publish ok"))

    status, ctype, _ = _http_get(f"{base_url}/")
    checks.append(SmokeCheck("spa", status == 200 and "text/html" in ctype, f"GET / → {status} {ctype}"))

    rc, out = _exec_in(exe, name, "sh", "-c", "ls /data/scratchpad.md /data/routines")
    checks.append(SmokeCheck("data-dir", rc == 0, out.strip()[:80]))

    return checks
```

- [ ] **Step 4: Add the `smoke` command to `main.py`:**

```python
@app.command()
def smoke(
    runtime: RuntimeOpt = None,
    keep: Annotated[bool, typer.Option("--keep", help="Leave the container running")] = False,
    attach: Annotated[bool, typer.Option("--attach", help="Check the already-running container")] = False,
    hf_cache: Annotated[Path | None, typer.Option(help="Existing HF cache to mount at /models/hf")] = None,
    timeout: Annotated[float, typer.Option(help="Seconds to wait for /health")] = 300.0,
) -> None:
    """Boot (seed mode) + verify the containerized stack, then tear it down."""
    r = rt.detect(runtime)
    if not attach:
        up(runtime=r.name, mode="seed", hf_cache=hf_cache)
    plan = launch.LaunchPlan(
        run_args=[], url_hint="resolve-ip" if r.name == "container" else "http://localhost:8081",
        name=rt.container_name(), image=rt.image_tag(),
    )
    base_url = _resolve_url(r, plan)
    checks = smoke_mod.run_checks(r.exe, plan.name, base_url, timeout=timeout)
    table = Table(title=f"alfred smoke — {plan.name}")
    table.add_column("check")
    table.add_column("result")
    table.add_column("detail")
    for c in checks:
        table.add_row(c.name, "[green]PASS[/green]" if c.passed else "[red]FAIL[/red]", c.detail)
    console.print(table)
    if not keep and not attach:
        down(runtime=r.name)
    if not all(c.passed for c in checks):
        raise typer.Exit(code=1)
```

with imports `from rich.table import Table` and `from alfredctl import smoke as smoke_mod`.
Note: when calling `up(...)` internally, pass only keyword args that exist; typer
commands are plain functions — supply defaults explicitly if the call signature requires
(`mode="seed"`, `hf_cache=hf_cache`).

- [ ] **Step 5: Tests + gates → PASS**
- [ ] **Step 6: Commit** — `feat(alfredctl): containerized smoke command`

---

### Task 8: Prod compose-of-one, legacy script deletion, CI build workflow

**Files:**
- Rewrite: `docker-compose.yml`
- Delete: `scripts/dev-up.sh`, `scripts/dev-down.sh`, `scripts/dev-logs.sh`
- Modify: `README.md` (Setup/Run section → alfredctl quickstart; grep for `dev-up`
  references repo-wide and fix: `grep -rn "dev-up\|dev-down\|dev-logs" --include='*.md' .`
  — update `docs/**` hits too; leave `docs/superpowers/specs|plans/**` history files alone)
- Create: `.github/workflows/container-build.yml`
- Modify: `.env.example` (add `ALFRED_MODELS_DIR`, `HF_TOKEN` comment lines under the
  containerization section)

**Interfaces:** none new.

- [ ] **Step 1: `docker-compose.yml` (compose-of-one, prod):**

```yaml
# Production deployment — one fat image, external state.
# Build the image first:  uv run alfredctl build --tag alfred:latest
# Then:                   ALFRED_SECRETS_PASSPHRASE=... docker compose up -d
services:
  alfred:
    image: alfred:latest
    env_file: .env
    environment:
      ALFRED_DATA_MODE: persistent
      ALFRED_SECRETS_PASSPHRASE: ${ALFRED_SECRETS_PASSPHRASE:?set ALFRED_SECRETS_PASSPHRASE}
    volumes:
      - alfred_data:/data
      - alfred_models:/models
    ports:
      - "8081:8081"
      # - "1883:1883"   # opt-in: real Home Assistant publishing to the edge broker
    extra_hosts:
      - "host.docker.internal:host-gateway"   # reach host Ollama on Linux
    restart: unless-stopped
volumes:
  alfred_data:
  alfred_models:
```

- [ ] **Step 2: Delete legacy scripts** (`git rm scripts/dev-up.sh scripts/dev-down.sh
scripts/dev-logs.sh`) and update every doc reference found by the grep to the alfredctl
equivalent (`uv run alfredctl up --mode ephemeral`, `... logs -f`, `... down`). In
`README.md`, the quickstart becomes:

```bash
git clone https://github.com/anirudhlath/alfred && cd alfred
uv venv --python 3.13 && uv pip install -e ".[dev]"
uv run alfredctl up --mode seed     # builds the image, starts everything, prints the URL
```

with a short matrix: persistent (prod-ish), ephemeral (worktree testing), seed (demo);
a note that Docker/Apple container/Podman are auto-detected; and external inference
(`OPENROUTER_API_KEY` in `.env`, or host Ollama — reachable automatically via the
injected gateway host). Native (non-container) dev remains: `uv run python -m runner`
with your own Redis Stack + Mosquitto (one sentence, no Homebrew script).

- [ ] **Step 3: `.github/workflows/container-build.yml`:**

```yaml
name: container-build

on:
  pull_request:
    paths:
      - "Containerfile"
      - ".dockerignore"
      - "alfredctl/**"
      - "runner/**"
      - "pyproject.toml"
      - "uv.lock"
      - ".github/workflows/container-build.yml"
  workflow_dispatch:

jobs:
  build:
    strategy:
      matrix:
        include:
          - runner: ubuntu-latest
            arch: amd64
          - runner: ubuntu-24.04-arm
            arch: arm64
    runs-on: ${{ matrix.runner }}
    timeout-minutes: 45
    steps:
      - uses: actions/checkout@v4
        with:
          path: alfred
      - uses: actions/checkout@v4
        with:
          repository: anirudhlath/alfred-home-service
          path: home-service
      - uses: astral-sh/setup-uv@v5
      - name: Build fat image (staged context)
        working-directory: alfred
        run: |
          uv venv --python 3.13
          # staging.py is stdlib-only — no project install needed
          .venv/bin/python -c "
          import sys; sys.path.insert(0, '.')
          from pathlib import Path
          from alfredctl.staging import stage_context
          stage_context(Path('/tmp/alfred-ctx'))
          "
          docker build -t alfred:ci -f /tmp/alfred-ctx/alfred/Containerfile /tmp/alfred-ctx
      - name: Assert image contract
        run: |
          docker run --rm alfred:ci python -c "import bus, core.reflex, runner, alfred_sdk, app.server"
          docker run --rm alfred:ci sh -c "redis-server --port 7777 --loadmodule /usr/local/lib/redis/modules/redisearch.so --daemonize no & sleep 1; redis-cli -p 7777 MODULE LIST | grep -qi search"
          docker run --rm alfred:ci tini --version
```

Note: this workflow is **not** part of the `ci-ok` aggregate — do not touch `ci.yml`'s
aggregate job. (If `ubuntu-24.04-arm` is unavailable for the repo plan, keep the matrix
entry — it fails visibly and non-blocking — and note it in the PR description.)

- [ ] **Step 4: `.env.example` additions** (under the containerization block):

```
# Model cache root (piper/speechbrain; HF models go to $HF_HOME) — container default /models
ALFRED_MODELS_DIR=
# HuggingFace token — required for the first download of the gated embeddinggemma model
HF_TOKEN=
```

- [ ] **Step 5: Gates** (ruff/mypy unaffected but run anyway; pytest full suite; verify
  `docker compose config` parses: `docker compose -f docker-compose.yml config -q` with
  `ALFRED_SECRETS_PASSPHRASE=x` set)
- [ ] **Step 6: Commit** — `feat(deploy): compose-of-one, container-build CI, retire dev-up scripts`

---

### Task 9: Docs, PRD, CLAUDE.md, backlog + QA tickets

**Files:**
- Rewrite: `docs/containerization.md` (full doc replacing the Part 1 stub: architecture +
  mermaid topology (adapt from the spec §2 diagram), image contents table, alfredctl
  command reference, data modes table, model-cache volume + HF_TOKEN note, secrets
  passphrase flow, trusted networks, prod compose, runtime matrix
  (docker/container/podman: ports vs IP, gateway host), troubleshooting (Apple container
  version-skew fix: stale launchd agents from an old Homebrew install → bootout +
  `container system start`; `container network ls` must show `default`))
- Modify: `docs/architecture.md` (add container topology: one fat container node wrapping
  the existing services diagram; note only :8081 exposed)
- Modify: `docs/PRD.md` (Capability Catalog: one-command containerized deployment row →
  status Shipped, reference docs/containerization.md; bump "statuses current as of" date
  to 2026-07-22)
- Modify: `CLAUDE.md` (repo root): Tech Stack line (alfredctl), Key Paths (add
  `alfredctl/`), Workflow (container quickstart), Running the System (alfredctl variant
  first, native second, drop dev-up.sh), Gotchas (add: models under
  `ALFRED_MODELS_DIR`/`models_root()`; image build stages context from git ls-files —
  gitignored files never reach the image; explicit cryptfile backend requires passphrase)
- Create: `docs/backlog/medium/seed-mode-fixtures-pack.md` (dummy HA snapshot + sample
  user + sample memories loaded in seed mode — spec §4 deferred)
- Create: `docs/backlog/low/registry-publish-images.md` (publish multi-arch images to
  GHCR; alfredctl pulls instead of building)
- Create: `docs/backlog/low/secrets-passphrase-host-keychain.md` (store the generated
  passphrase in the host keychain instead of `<data>/.secrets-passphrase`)
- Create: `docs/backlog/low/non-gated-embedding-default.md` (if not already filed —
  check `ls docs/backlog/*/`; spec §6 mentions it)
- Create: `docs/qa-backlog/container-apple-e2e.md`, `docs/qa-backlog/container-podman-smoke.md`,
  `docs/qa-backlog/container-prod-compose-cachyos.md`, `docs/qa-backlog/container-persistent-mode-retention.md`
  (QA template per convention; the Apple-container ticket covers: up/urls/smoke/down on
  Apple container, WebAuthn passkey registration through the vmnet subnet, chat via host
  Ollama gateway)
- Modify: `docs/containerization.md` note on devcontainer: **deviation from spec §11** —
  the devcontainer intentionally keeps its lightweight dev compose (editing/test loop;
  building the fat image on codespace start would be prohibitive); revisit if cloud
  full-stack runs are needed.

**Interfaces:** none.

- [ ] **Step 1:** Write all files above. For QA tickets use the repo's QA template
  (Feature/Priority/Type/Prerequisites/Test Steps/Expected Result/Notes).
- [ ] **Step 2:** `grep -rn "dev-up" --include='*.md' . | grep -v superpowers` → zero
  live references (specs/plans history excluded).
- [ ] **Step 3: Commit** — `docs: containerization guide, PRD row, CLAUDE.md, backlog + QA tickets`

---

## After all tasks

1. **Final whole-branch review** (most capable model) over `b02bc0f..HEAD` — Part 1 was
   already reviewed; scope the final review to the Part 2 commits but give the reviewer
   the full-branch package for context.
2. **Full containerized smoke (the user's explicit gate — do not skip):**
   - `uv run alfredctl build` (Docker) → `uv run alfredctl smoke --hf-cache ~/.cache/huggingface`
     → all checks PASS.
   - `uv run alfredctl up --mode seed --hf-cache ~/.cache/huggingface` → verify end-to-end
     event flow (publish an MQTT state event into the container broker, watch reflex
     consume it via `alfredctl logs`), reflex→Ollama over the injected gateway, SPA loads,
     telemetry lands under `/data/research/`, teardown with `alfredctl down`.
   - Repeat `up`/health/`down` on **Apple container** (`--runtime container`).
   - `persistent` mode retention: up → create state → down → up → state survives.
3. Update `.superpowers/sdd/progress.md` (Part 2 section) + workspace-level CLAUDE.md
   references to dev-up.sh (outside repo — direct edit).
4. finishing-a-development-branch: push `feat/containerization`, open the PR.
