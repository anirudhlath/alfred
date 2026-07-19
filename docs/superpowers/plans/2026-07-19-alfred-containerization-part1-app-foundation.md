# Alfred Containerization — Part 1: App Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Alfred app container-ready by consolidating all runtime-writable state under one configurable data root, generalizing the runner to supervise native binaries, giving secrets a container-friendly backend, and making the trusted-network gate work through container networks — all without changing native behavior.

**Architecture:** Four independent, pure-Python refactors that each ship on their own and keep the existing `pytest` suite green. They establish the exact interfaces (`data_path()`, `core/memory/paths.py`, `ServiceSpec(command=...)`, `ALFRED_SECRETS_BACKEND`, `ALFRED_TRUSTED_NETWORKS`) that Part 2 (the fat Containerfile + `alfredctl`) will consume.

**Tech Stack:** Python 3.13, `uv`, `ruff` (line-length 100), `mypy --strict`, `pytest` + `pytest-asyncio`, `loguru`, `keyring` (+ `keyrings.cryptfile`), `fastapi`.

**Spec:** `docs/superpowers/specs/2026-07-19-alfred-containerization-design.md` (§4 state consolidation, §3 process model, §10 secrets, §7 trusted-network).

## Global Constraints

- Python 3.13+ only; modern syntax (`match`, `X | Y` unions).
- `ruff check . --fix && ruff format .` clean; line-length 100.
- `mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/` clean.
- Type hints on every new function signature; async-first for I/O.
- Tests: `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed).
- Run tests via the worktree venv: `.venv/bin/python -m pytest -x -q` (worktrees default to system Python — create the venv first: `uv venv --python 3.13 && uv pip install -e ".[dev,memory,voice,integrations]"`).
- `shared/` stays dependency-free (no imports from `core`, `bus`, `domains`, `sdk`).
- Never hardcode Redis stream keys — import from `shared.streams`.
- **Pillar 4 (Librarian):** core preference files are read-only at runtime. Package-shipped preference/profile/routine files are **read-only templates**; the *writable* copies live under `ALFRED_DATA_DIR` (first-boot copy, never overwritten).
- Root `conftest.py` autouse `_mock_keyring` swaps in `InMemoryKeyring` — secrets-backend tests must call the backend selector directly (not rely on the fixture).
- Preserve native-dev behavior: with `ALFRED_DATA_DIR` unset, everything defaults to `./data` (the current `credentials.db` behavior) and `python -m runner` still starts exactly the six core services.

---

### Task 1: `data_path()` + data-mode helpers in `shared/config.py`

The single source of truth for where runtime-writable state lives. Everything else in this plan derives from it.

**Files:**
- Modify: `shared/config.py` (add module-level helpers after the `load_dotenv` block, before `AlfredConfig`)
- Test: `tests/shared/test_config_data.py` (create)

**Interfaces:**
- Consumes: nothing (uses `os`, `pathlib`).
- Produces:
  - `data_root() -> Path` — resolved dir from `ALFRED_DATA_DIR` (default `"data"`).
  - `data_path(*parts: str) -> Path` — child path under the root, parent dir ensured.
  - `data_mode() -> str` — `"persistent"` (default) | `"ephemeral"` | `"seed"` from `ALFRED_DATA_MODE`.

- [ ] **Step 1: Write the failing test**

Create `tests/shared/test_config_data.py`:

```python
"""Tests for the data-root helpers in shared.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared import config


def test_data_root_defaults_to_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_DATA_DIR", raising=False)
    assert config.data_root() == (Path("data").resolve())


def test_data_root_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    assert config.data_root() == tmp_path.resolve()


def test_data_path_ensures_parent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    p = config.data_path("nested", "file.db")
    assert p == (tmp_path / "nested" / "file.db").resolve()
    assert p.parent.is_dir()  # parent created, file itself not


def test_data_mode_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_DATA_MODE", raising=False)
    assert config.data_mode() == "persistent"
    monkeypatch.setenv("ALFRED_DATA_MODE", "seed")
    assert config.data_mode() == "seed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/shared/test_config_data.py -v`
Expected: FAIL — `AttributeError: module 'shared.config' has no attribute 'data_root'`.

- [ ] **Step 3: Write minimal implementation**

In `shared/config.py`, after line 13 (`load_dotenv(_env_path)`), add:

```python
def data_root() -> Path:
    """Root directory for all runtime-writable state (env ``ALFRED_DATA_DIR``, default ``data``)."""
    return Path(os.getenv("ALFRED_DATA_DIR", "data")).resolve()


def data_path(*parts: str) -> Path:
    """Resolve a child path under the data root, ensuring its parent directory exists."""
    p = data_root().joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def data_mode() -> str:
    """Data lifecycle mode: ``persistent`` (default) | ``ephemeral`` | ``seed`` (env ``ALFRED_DATA_MODE``)."""
    return os.getenv("ALFRED_DATA_MODE", "persistent")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/shared/test_config_data.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check shared/config.py tests/shared/test_config_data.py --fix && ruff format shared/config.py tests/shared/test_config_data.py
mypy --strict shared/
git add shared/config.py tests/shared/test_config_data.py
git commit -m "feat(config): add data_path/data_root/data_mode helpers"
```

---

### Task 2: `core/memory/paths.py` — centralized memory paths + `seed_defaults()`

One module owns every memory-state path and the first-boot template copy. Keeps the path logic DRY across the ~6 call sites in Tasks 3–5.

**Files:**
- Create: `core/memory/paths.py`
- Test: `tests/core/memory/test_paths.py` (create)

**Interfaces:**
- Consumes: `shared.config.data_path` (Task 1).
- Produces:
  - `scratchpad_path() -> Path`, `episodic_cold_path() -> Path`
  - `routines_dir() -> Path`, `preferences_dir() -> Path`, `profile_dir() -> Path`, `triggers_snapshot_dir() -> Path` (each `mkdir(parents=True, exist_ok=True)`)
  - `seed_defaults() -> None` — idempotent copy of shipped read-only templates into the data dir (never overwrites)

- [ ] **Step 1: Write the failing test**

Create `tests/core/memory/test_paths.py`:

```python
"""Tests for centralized memory paths + first-boot seeding."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.memory import paths


def test_paths_derive_from_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    assert paths.scratchpad_path() == (tmp_path / "scratchpad.md").resolve()
    assert paths.episodic_cold_path() == (tmp_path / "episodic_cold.db").resolve()
    assert paths.routines_dir() == (tmp_path / "routines").resolve()
    assert paths.routines_dir().is_dir()
    assert paths.preferences_dir().is_dir()
    assert paths.profile_dir().is_dir()
    assert paths.triggers_snapshot_dir().is_dir()


def test_seed_defaults_copies_templates_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Fake package template dir with one preference file.
    pkg = tmp_path / "pkg_prefs"
    pkg.mkdir()
    (pkg / "core.md").write_text("# seed", encoding="utf-8")
    monkeypatch.setattr(paths, "PKG_PREFERENCES", pkg)
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path / "data"))

    paths.seed_defaults()
    copied = paths.preferences_dir() / "core.md"
    assert copied.read_text(encoding="utf-8") == "# seed"


def test_seed_defaults_never_overwrites(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pkg = tmp_path / "pkg_prefs"
    pkg.mkdir()
    (pkg / "core.md").write_text("# template", encoding="utf-8")
    monkeypatch.setattr(paths, "PKG_PREFERENCES", pkg)
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path / "data"))

    target = paths.preferences_dir() / "core.md"
    target.write_text("# user edited", encoding="utf-8")
    paths.seed_defaults()
    assert target.read_text(encoding="utf-8") == "# user edited"  # untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/memory/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.memory.paths'`.

- [ ] **Step 3: Write minimal implementation**

Create `core/memory/paths.py`:

```python
"""Centralized runtime-state paths for the memory subsystem.

All writable memory state resolves through ``shared.config.data_path`` so it can be
externalized (persistent), thrown away (ephemeral), or seeded (dev). Package-shipped
preference/profile/routine files are read-only templates copied into the data dir on
first boot only.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from shared.config import data_path

_PKG_MEMORY = Path(__file__).resolve().parent  # core/memory

# Shipped read-only template dirs.
PKG_PREFERENCES = _PKG_MEMORY / "preferences"
PKG_PROFILE = _PKG_MEMORY / "profile"
PKG_ROUTINES = _PKG_MEMORY / "routines"


def scratchpad_path() -> Path:
    return data_path("scratchpad.md")


def episodic_cold_path() -> Path:
    return data_path("episodic_cold.db")


def _ensured_dir(name: str) -> Path:
    p = data_path(name)
    p.mkdir(parents=True, exist_ok=True)
    return p


def routines_dir() -> Path:
    return _ensured_dir("routines")


def preferences_dir() -> Path:
    return _ensured_dir("preferences")


def profile_dir() -> Path:
    return _ensured_dir("profile")


def triggers_snapshot_dir() -> Path:
    return _ensured_dir("triggers")


# (template src, data-dir dest factory, glob) — only content files, never package .py.
def _seed_specs() -> list[tuple[Path, Path, str]]:
    return [
        (PKG_PREFERENCES, preferences_dir(), "*.md"),
        (PKG_PROFILE, profile_dir(), "*.md"),
        (PKG_ROUTINES, routines_dir(), "*.yaml"),
    ]


def seed_defaults() -> None:
    """Copy shipped read-only templates into the data dir when missing. Idempotent."""
    for src, dest, pattern in _seed_specs():
        if not src.is_dir():
            continue
        for f in src.rglob(pattern):
            if not f.is_file():
                continue
            target = dest / f.relative_to(src)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/core/memory/test_paths.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check core/memory/paths.py tests/core/memory/test_paths.py --fix && ruff format core/memory/paths.py tests/core/memory/test_paths.py
mypy --strict core/memory/paths.py
git add core/memory/paths.py tests/core/memory/test_paths.py
git commit -m "feat(memory): add centralized state paths + first-boot seeding"
```

---

### Task 3: Route scratchpad + episodic cold store through `paths`

**Files:**
- Modify: `core/memory/scratchpad_writer.py:17`
- Modify: `core/memory/ingestor_main.py:46-51`
- Test: `tests/core/memory/test_state_paths_wiring.py` (create)

**Interfaces:**
- Consumes: `core.memory.paths.scratchpad_path`, `core.memory.paths.episodic_cold_path` (Task 2).
- Produces: no new public API — changes default paths only.

- [ ] **Step 1: Write the failing test**

Create `tests/core/memory/test_state_paths_wiring.py`:

```python
"""The scratchpad writer and cold store default to the data dir, not the package."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.memory import paths
from core.memory.scratchpad_writer import ScratchpadWriter


def test_scratchpad_writer_defaults_under_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    writer = ScratchpadWriter(redis=None)
    assert Path(writer.scratchpad_path) == paths.scratchpad_path()
    assert str(tmp_path) in writer.scratchpad_path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/memory/test_state_paths_wiring.py -v`
Expected: FAIL — default path still resolves under `core/memory/`, not `tmp_path`.

- [ ] **Step 3: Write minimal implementation**

In `core/memory/scratchpad_writer.py`, replace line 17:

```python
# was: _DEFAULT_SCRATCHPAD = str(Path(__file__).resolve().parent / "scratchpad.md")
from core.memory.paths import scratchpad_path
```

Then change the `scratchpad_path` parameter default in `__init__` (line 31) from
`_DEFAULT_SCRATCHPAD` to a lazy default so the env is read at construction time:

```python
    def __init__(
        self,
        redis: Any,
        queue_key: str = SCRATCHPAD_QUEUE,
        scratchpad_path: str | None = None,
    ) -> None:
        self.redis = redis
        self.queue_key = queue_key
        self.scratchpad_path = scratchpad_path or str(_scratchpad_path())
```

Add a private alias import at top to avoid shadowing the parameter name:

```python
from core.memory.paths import scratchpad_path as _scratchpad_path
```

Remove the now-unused `_DEFAULT_SCRATCHPAD` and the `Path` import if unused.

In `core/memory/ingestor_main.py`, replace lines 46-51:

```python
    from core.memory.paths import episodic_cold_path

    hot = RedisVectorStore(redis=r, dim=config.embedding_dim)
    cold = SqliteVecStore(
        db_path=str(episodic_cold_path()),
        dim=config.embedding_dim,
    )
```

Remove the now-unused `memory_dir = Path(__file__).resolve().parent` line and the `Path` import if unused.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/core/memory/test_state_paths_wiring.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check core/memory/scratchpad_writer.py core/memory/ingestor_main.py tests/core/memory/test_state_paths_wiring.py --fix
ruff format core/memory/scratchpad_writer.py core/memory/ingestor_main.py tests/core/memory/test_state_paths_wiring.py
mypy --strict core/
git add core/memory/scratchpad_writer.py core/memory/ingestor_main.py tests/core/memory/test_state_paths_wiring.py
git commit -m "refactor(memory): route scratchpad + cold store through data dir"
```

---

### Task 4: Route routines store + triggers snapshot through `paths`

**Files:**
- Modify: `core/memory/routines/store.py:16,30`
- Modify: `core/triggers/__main__.py:43`
- Modify: `core/conscious/__main__.py:142-144, 208-211`
- Modify: `core/channels/admin_api.py:409`
- Test: `tests/core/memory/test_routines_store_path.py` (create)

**Interfaces:**
- Consumes: `core.memory.paths.routines_dir`, `core.memory.paths.triggers_snapshot_dir` (Task 2).
- Produces: no new public API. `RoutineStore(routines_dir=...)` keyword stays valid (backward compatible).

- [ ] **Step 1: Locate the TriggerStore call site**

Run:

```bash
grep -rnE "TriggerStore\(" core/ | grep -v "def " | grep -viE "test"
```

Expected: one or more construction sites passing a `snapshot_dir=` that resolves to
`core/memory/triggers` (e.g. in `core/triggers/store.py` callers or `core/triggers/__main__.py`).
Note each exact line — you will replace its `snapshot_dir` argument in Step 4.

- [ ] **Step 2: Write the failing test**

Create `tests/core/memory/test_routines_store_path.py`:

```python
"""RoutineStore defaults its directory to the data dir."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.memory import paths
from core.memory.routines.store import RoutineStore


def test_routine_store_defaults_under_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    store = RoutineStore()
    assert store._dir == paths.routines_dir()
    assert str(tmp_path) in str(store._dir)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/memory/test_routines_store_path.py -v`
Expected: FAIL — `store._dir` still resolves under the package.

- [ ] **Step 4: Write minimal implementation**

In `core/memory/routines/store.py`, replace line 16 (`_DEFAULT_ROUTINES_DIR`) and the
`__init__` default (line 30). **Use an alias import and KEEP the public `routines_dir`
parameter name** — two callers pass `routines_dir=` by keyword (`core/conscious/__main__.py`,
`core/channels/admin_api.py`), so renaming the parameter would break them:

```python
# remove: _DEFAULT_ROUTINES_DIR = str(Path(__file__).resolve().parent)
from core.memory.paths import routines_dir as _routines_dir


class RoutineStore:
    ...
    def __init__(self, routines_dir: str | None = None) -> None:
        self._dir = Path(routines_dir) if routines_dir else _routines_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache = None
```

The `_routines_dir` alias avoids shadowing the public parameter; existing
`RoutineStore(routines_dir=...)` keyword callers keep working.

Then route every routines/triggers construction site (exact before → after; each modified
file adds `from core.memory.paths import triggers_snapshot_dir` where it uses it):

| File | Before | After |
|---|---|---|
| `core/triggers/__main__.py:43` | `SNAPSHOT_DIR = Path("core/memory/triggers")` | `SNAPSHOT_DIR = triggers_snapshot_dir()` |
| `core/conscious/__main__.py:142-144` | `routine_store = RoutineStore(\n        routines_dir=str(memory_dir / "routines"),\n    )` | `routine_store = RoutineStore()` |
| `core/conscious/__main__.py:208-211` | `snapshot_dir=str(Path(__file__).resolve().parent.parent / "memory" / "triggers"),` | `snapshot_dir=triggers_snapshot_dir(),` |
| `core/channels/admin_api.py:409` | `store = RoutineStore(routines_dir=str(_MEMORY_DIR / "routines"))` | `store = RoutineStore()` |

`TriggerStore.snapshot_dir` accepts `Path | str` (`core/triggers/store.py:37`), so passing the
`Path` from `triggers_snapshot_dir()` is fine. Do NOT remove the `memory_dir` / `_MEMORY_DIR`
locals yet — their remaining cold-store/scratchpad/prefs uses are routed in Task 5, which
removes the now-dead locals.

- [ ] **Step 5: Run tests to verify no regressions**

Run: `.venv/bin/python -m pytest tests/core/memory/ tests/core/channels/ tests/core/triggers/ core/reflex/tests/ -q`
Expected: PASS (new test + existing memory/channels/triggers/reflex tests unaffected). The
conscious/triggers/admin_api call-site edits must not break their existing tests.

- [ ] **Step 6: Lint, type-check, commit**

```bash
.venv/bin/python -m ruff check core/memory/routines/store.py core/triggers/__main__.py core/conscious/__main__.py core/channels/admin_api.py tests/core/memory/test_routines_store_path.py --fix
.venv/bin/python -m ruff format core/memory/routines/store.py core/triggers/__main__.py core/conscious/__main__.py core/channels/admin_api.py tests/core/memory/test_routines_store_path.py
.venv/bin/python -m mypy --strict core/
git add core/memory/routines/store.py core/triggers/__main__.py core/conscious/__main__.py core/channels/admin_api.py tests/core/memory/test_routines_store_path.py
git commit -m "refactor(memory): route routines + trigger snapshots through data dir (defaults + call sites)"
```

---

### Task 5: Route preferences/profile through `paths` + call `seed_defaults()` at startup

**Files:**
- Modify: `core/channels/web_server.py:170-173` (`_get_prefs_dirs`) + lifespan `seed_defaults()`
- Modify: `core/librarian/consolidator.py:57-59` (defaults; remove `_MEMORY_DIR`)
- Modify: `runner/__main__.py` (call `seed_defaults()` before starting services)
- Modify: `core/conscious/__main__.py` (cold store, semantic dirs, scratchpad, Librarian prefs args; remove dead `memory_dir`)
- Modify: `core/librarian/__main__.py` (cold store, semantic dirs, Librarian prefs args; remove dead `_MEMORY_DIR`)
- Modify: `core/channels/admin_api.py` (cold store; remove dead `_MEMORY_DIR`)
- Modify: `core/reflex/__main__.py` (MemoryReader + ReflexEngine prefs dirs; remove dead `memory_dir`)
- Test: `tests/core/memory/test_preferences_paths.py` (create)
- Test: `tests/core/memory/test_no_package_state_paths.py` (create — completeness gate)

**Interfaces:**
- Consumes: `core.memory.paths.preferences_dir`, `.profile_dir`, `.episodic_cold_path`, `.seed_defaults` (Task 2).
- Produces: no new public API. This task COMPLETES the state consolidation — every process
  entry point that previously built package-relative writable-state paths now routes through `paths`.

- [ ] **Step 1: Write the failing test**

Create `tests/core/memory/test_preferences_paths.py`:

```python
"""Preference/profile dirs resolve under the data dir."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.channels.web_server import _get_prefs_dirs
from core.memory import paths


def test_prefs_dirs_under_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    prefs, profile = _get_prefs_dirs()
    assert prefs == paths.preferences_dir()
    assert profile == paths.profile_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/memory/test_preferences_paths.py -v`
Expected: FAIL — `_get_prefs_dirs` still returns package `core/memory/{preferences,profile}`.

- [ ] **Step 3: Write minimal implementation**

In `core/channels/web_server.py`, replace `_get_prefs_dirs` (lines 170-173):

```python
def _get_prefs_dirs() -> tuple[Path, Path]:
    """Return (preferences_dir, profile_dir) for semantic memory (under ALFRED_DATA_DIR)."""
    from core.memory.paths import preferences_dir, profile_dir

    return preferences_dir(), profile_dir()
```

In `core/librarian/consolidator.py`, replace lines 57-59:

```python
from core.memory.paths import preferences_dir as _preferences_dir
from core.memory.paths import profile_dir as _profile_dir

_DEFAULT_PREFERENCES_DIR = str(_preferences_dir())
_DEFAULT_PROFILE_DIR = str(_profile_dir())
```

> Note: `_preferences_dir()` reads the env at import time. That matches current behavior
> (module-level default). Callers that pass an explicit `preferences_dir=` are unaffected.

In `runner/__main__.py`, inside `main()` after `config = AlfredConfig.from_env()` and before
constructing the `Supervisor`, add:

```python
    from core.memory.paths import seed_defaults

    seed_defaults()  # first-boot: copy read-only templates into ALFRED_DATA_DIR
```

In `core/channels/web_server.py` lifespan startup (find the `@asynccontextmanager`
`async def lifespan(...)` / startup block that runs before the app serves), add near the top
of startup:

```python
    from core.memory.paths import seed_defaults

    seed_defaults()  # idempotent — safe when the runner already seeded
```

Then route every remaining cold-store / scratchpad / preferences / profile construction site
in the process entry points (exact before → after). Each modified file adds
`from core.memory.paths import episodic_cold_path, preferences_dir, profile_dir` (import only
what it uses). **Leave read-only package resources alone** — e.g.
`core/memory/sqlite_vec_store.py`'s `_SCHEMA_V1_PATH`/`_MIGRATION_V2_PATH` are shipped SQL
assets, NOT writable state; do not touch them.

| File | Before | After |
|---|---|---|
| `core/conscious/__main__.py:155-158` | `db_path=str(memory_dir / "episodic_cold.db"),` | `db_path=str(episodic_cold_path()),` |
| `core/conscious/__main__.py:163-166` | `semantic_dirs=[\n                memory_dir / "preferences",\n                memory_dir / "profile",\n            ],` | `semantic_dirs=[\n                preferences_dir(),\n                profile_dir(),\n            ],` |
| `core/conscious/__main__.py:271-274` | `scratchpad_writer = ScratchpadWriter(\n        redis=r,\n        scratchpad_path=str(memory_dir / "scratchpad.md"),\n    )` | `scratchpad_writer = ScratchpadWriter(redis=r)` |
| `core/conscious/__main__.py:295-296` | `preferences_dir=str(memory_dir / "preferences"),\n                profile_dir=str(memory_dir / "profile"),` | *(delete both lines — `Librarian` defaults are now `paths`-based)* |
| `core/conscious/__main__.py:141` | `memory_dir = Path(__file__).resolve().parent.parent / "memory"` | *(delete — now unused)* |
| `core/librarian/__main__.py:46-49` | `db_path=str(_MEMORY_DIR / "episodic_cold.db"),` | `db_path=str(episodic_cold_path()),` |
| `core/librarian/__main__.py:54-57` | `semantic_dirs=[\n                _MEMORY_DIR / "preferences",\n                _MEMORY_DIR / "profile",\n            ],` | `semantic_dirs=[\n                preferences_dir(),\n                profile_dir(),\n            ],` |
| `core/librarian/__main__.py:79-80` | `preferences_dir=str(_MEMORY_DIR / "preferences"),\n            profile_dir=str(_MEMORY_DIR / "profile"),` | *(delete both lines)* |
| `core/librarian/__main__.py:30` | `_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"` | *(delete — now unused)* |
| `core/channels/admin_api.py:105-106` | `db_path=str(_MEMORY_DIR / "episodic_cold.db"), dim=config.embedding_dim` | `db_path=str(episodic_cold_path()), dim=config.embedding_dim` |
| `core/channels/admin_api.py:49` | `_MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"` | *(delete — unused after Task 4's routines edit + this cold-store edit)* |
| `core/reflex/__main__.py:191-193` | `preferences_dir=memory_dir / "preferences",\n        profile_dir=memory_dir / "profile",` | `preferences_dir=preferences_dir(),\n        profile_dir=profile_dir(),` |
| `core/reflex/__main__.py:196` | `preferences_dir=str(memory_dir / "preferences"),` | `preferences_dir=str(preferences_dir()),` |
| `core/reflex/__main__.py:190` | `memory_dir = Path(__file__).resolve().parent.parent / "memory"` | *(delete — now unused)* |

`Librarian.__init__` already defaults `preferences_dir`/`profile_dir` to
`_DEFAULT_PREFERENCES_DIR`/`_DEFAULT_PROFILE_DIR` (`core/librarian/consolidator.py:130-131`),
which this task rewires to the `paths`-based values — so deleting the explicit args at the two
`Librarian(...)` call sites makes them use the correct defaults.

- [ ] **Step 3b: Write the completeness-gate test**

Create `tests/core/memory/test_no_package_state_paths.py`:

```python
"""No process entry point may construct package-relative writable-state paths."""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
# Matches `.parent.parent / "memory"` — the entry-point pattern reaching into the
# installed package for writable state. Read-only assets use `.parent / "episodic"`,
# which this pattern does not match.
_PATTERN = re.compile(r"""\.parent\.parent\s*/\s*["']memory["']""")


def test_no_package_relative_memory_state_paths() -> None:
    offenders: list[str] = []
    for f in (_REPO / "core").rglob("*.py"):
        s = str(f)
        if "/tests/" in s or f.name == "paths.py":
            continue
        if _PATTERN.search(f.read_text(encoding="utf-8")):
            offenders.append(str(f.relative_to(_REPO)))
    assert not offenders, f"package-relative memory state paths remain: {offenders}"
```

Run it once BEFORE the routing edits to confirm it FAILS listing the offenders
(`core/conscious/__main__.py`, `core/librarian/__main__.py`, `core/channels/admin_api.py`,
`core/reflex/__main__.py`, `core/librarian/consolidator.py`), then again AFTER all edits to
confirm it PASSES. This gate is what proves the consolidation is complete.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/core/memory/test_preferences_paths.py tests/core/memory/test_no_package_state_paths.py -v`
Expected: PASS.

- [ ] **Step 5: Run regressions across every touched entry point**

Run: `.venv/bin/python -m pytest tests/core/channels/ tests/core/memory/ core/librarian/ core/reflex/tests/ -q`
Expected: PASS (no path regressions in channels/memory/librarian/reflex; conscious/admin_api
entry-point edits must not break their tests).

- [ ] **Step 6: Lint, type-check, commit**

```bash
FILES="core/channels/web_server.py core/channels/admin_api.py core/librarian/consolidator.py core/librarian/__main__.py core/conscious/__main__.py core/reflex/__main__.py runner/__main__.py tests/core/memory/test_preferences_paths.py tests/core/memory/test_no_package_state_paths.py"
.venv/bin/python -m ruff check $FILES --fix
.venv/bin/python -m ruff format $FILES
.venv/bin/python -m mypy --strict core/ runner/
git add $FILES
git commit -m "refactor(memory): complete state consolidation — route all entry points + seed on boot"
```

---

### Task 6: Generalize `runner` to supervise native binaries + readiness gate + infra services

Lets the container's runner launch `redis-stack-server` and `mosquitto` (native binaries)
alongside the Python modules, gate their readiness before starting dependents, and add
`home-service` — all guarded by an env flag so native `python -m runner` is unchanged.

**Files:**
- Modify: `runner/supervisor.py` (`ServiceSpec`, `_start_process`, `_watch_dirs_for_module`, `run`)
- Modify: `runner/__main__.py` (`SERVICES` built conditionally on `ALFRED_MANAGE_INFRA`)
- Test: `tests/runner/test_supervisor_native.py` (create)
- Test: `tests/runner/test_service_list.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `ServiceSpec(name, module=None, command=None, delay=0.0, max_restarts=5, watch_dirs=[], ready_check=None)` — exactly one of `module`/`command` required; `ready_check: Callable[[], Awaitable[bool]] | None`.
  - `build_services() -> list[ServiceSpec]` in `runner/__main__.py` — the six core services, plus `redis`, `mosquitto`, `home-service` when `ALFRED_MANAGE_INFRA` is truthy.

- [ ] **Step 1: Write the failing tests**

Create `tests/runner/test_supervisor_native.py`:

```python
"""Supervisor runs native-command services and gates on readiness."""

from __future__ import annotations

import pytest

from runner.supervisor import ServiceSpec


def test_spec_requires_exactly_one_of_module_or_command() -> None:
    with pytest.raises(ValueError):
        ServiceSpec(name="bad")  # neither
    with pytest.raises(ValueError):
        ServiceSpec(name="bad", module="bus", command=["redis-server"])  # both
    # valid forms do not raise:
    ServiceSpec(name="ok-mod", module="bus")
    ServiceSpec(name="ok-cmd", command=["redis-server"])


async def test_await_ready_probes_until_true() -> None:
    # Deterministic unit test of the readiness gate — no subprocess race.
    from runner.supervisor import Supervisor, _ManagedService

    calls: list[int] = []

    async def ready() -> bool:
        calls.append(1)
        return len(calls) >= 2  # False on first probe, True on second

    sup = Supervisor([], reload=False)
    svc = _ManagedService(ServiceSpec(name="probe", command=["sleep", "1"], ready_check=ready))
    ok = await sup._await_ready(svc, timeout=5.0)
    assert ok is True
    assert len(calls) >= 2  # probed more than once before readiness


async def test_native_command_spawns_via_start_process() -> None:
    # Verifies the command branch of _start_process without driving run().
    from runner.supervisor import Supervisor, _ManagedService

    sup = Supervisor([], reload=False)
    svc = _ManagedService(ServiceSpec(name="probe", command=["true"]))
    await sup._start_process(svc)
    assert svc.process is not None
    await svc.process.wait()
    assert svc.process.returncode == 0
```

Create `tests/runner/test_service_list.py`:

```python
"""runner.__main__.build_services respects ALFRED_MANAGE_INFRA."""

from __future__ import annotations

import pytest

from runner.__main__ import build_services


def test_core_only_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_MANAGE_INFRA", raising=False)
    names = {s.name for s in build_services()}
    assert names == {"bridge", "reflex", "triggers", "conscious", "channels", "memory-ingestor"}


def test_infra_added_when_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_MANAGE_INFRA", "1")
    names = {s.name for s in build_services()}
    assert {"redis", "mosquitto", "home-service"}.issubset(names)
    # redis/mosquitto are native-command services with readiness checks:
    by_name = {s.name: s for s in build_services()}
    assert by_name["redis"].command is not None
    assert by_name["redis"].ready_check is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/runner/test_supervisor_native.py tests/runner/test_service_list.py -v`
Expected: FAIL — `ServiceSpec` has no `command`/`ready_check`; `build_services` doesn't exist.

- [ ] **Step 3: Implement `ServiceSpec` + supervisor changes**

In `runner/supervisor.py`, update imports and `ServiceSpec`:

```python
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ServiceSpec:
    """Specification for a supervised child service (python module or native command)."""

    name: str
    module: str | None = None
    command: list[str] | None = None
    delay: float = 0.0
    max_restarts: int = 5
    watch_dirs: list[str] = field(default_factory=list)
    ready_check: Callable[[], Awaitable[bool]] | None = None

    def __post_init__(self) -> None:
        if (self.module is None) == (self.command is None):
            raise ValueError(
                f"{self.name}: exactly one of 'module' or 'command' must be set"
            )
```

Update `_start_process` (line 87-96) to handle both forms:

```python
    async def _start_process(self, svc: _ManagedService) -> None:
        """Spawn a service as a child process (python module or native command)."""
        if svc.spec.command is not None:
            cmd = list(svc.spec.command)
        else:
            assert svc.spec.module is not None
            cmd = [sys.executable, "-u", "-m", svc.spec.module]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        svc.process = proc
        logger.info("[%s] started (PID %d)", svc.spec.name, proc.pid)
```

Guard `_watch_dirs_for_module` against native services in `_watch_files` (line 185):

```python
            if svc.spec.module is None:
                continue  # native commands are not hot-reloaded
            for d in _watch_dirs_for_module(svc.spec.module, self._root):
```

Add a bounded readiness probe (startup-only, not steady-state — respects the no-polling
rule, which targets runtime loops) and gate non-infra services on it. Add this method:

```python
    async def _await_ready(self, svc: _ManagedService, timeout: float = 30.0) -> bool:
        """Probe a service's ready_check until it passes or timeout (startup gate only)."""
        check = svc.spec.ready_check
        if check is None:
            return True
        deadline = timeout
        interval = 0.25
        elapsed = 0.0
        while elapsed < deadline and not self._shutdown.is_set():
            try:
                if await check():
                    logger.info("[%s] ready", svc.spec.name)
                    return True
            except Exception:  # noqa: BLE001 — probe failures are expected pre-readiness
                pass
            await asyncio.sleep(interval)
            elapsed += interval
        logger.error("[%s] not ready after %.0fs", svc.spec.name, timeout)
        return False
```

In `run()` (line 248), split infra (has `ready_check`) from the rest and gate:

```python
        infra = [m for m in self._managed if m.spec.ready_check is not None]
        rest = [m for m in self._managed if m.spec.ready_check is None]

        monitor_tasks = [asyncio.create_task(self._monitor(m)) for m in infra]
        for m in infra:
            if not await self._await_ready(m):
                self._shutdown.set()
                break
        if not self._shutdown.is_set():
            monitor_tasks += [asyncio.create_task(self._monitor(m)) for m in rest]
```

(Replace the single `monitor_tasks = [...]` line at 263 with the block above; the rest of
`run()` — watcher, `await self._shutdown.wait()`, teardown — is unchanged.)

- [ ] **Step 4: Implement `build_services()` in `runner/__main__.py`**

Replace the module-level `SERVICES = [...]` list with a function, and add infra specs:

```python
def build_services() -> list[ServiceSpec]:
    services = [
        ServiceSpec(name="bridge", module="bus"),
        ServiceSpec(name="reflex", module="core.reflex", delay=1.0),
        ServiceSpec(name="triggers", module="core.triggers"),
        ServiceSpec(name="conscious", module="core.conscious", delay=2.0,
                    watch_dirs=["core/conscious/prompts"]),
        ServiceSpec(name="channels", module="core.channels", delay=2.0,
                    watch_dirs=["core/voice", "core/conscious/prompts"]),
        ServiceSpec(name="memory-ingestor", module="core.memory.ingestor_main", delay=1.5),
    ]
    if os.getenv("ALFRED_MANAGE_INFRA", "").lower() in ("1", "true", "yes"):
        services = _infra_services() + services
    return services


def _infra_services() -> list[ServiceSpec]:
    async def _redis_ready() -> bool:
        import redis.asyncio as aioredis

        client = aioredis.Redis(host="localhost", port=6379, socket_timeout=2.0)
        try:
            return bool(await client.ping())
        finally:
            await client.aclose()

    async def _mqtt_ready() -> bool:
        import asyncio as _a

        try:
            _, writer = await _a.wait_for(_a.open_connection("localhost", 1883), timeout=2.0)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    redis_dir = data_root() / "redis"
    redis_dir.mkdir(parents=True, exist_ok=True)
    return [
        ServiceSpec(
            name="redis",
            command=["redis-stack-server", "--dir", str(redis_dir)],
            ready_check=_redis_ready,
        ),
        ServiceSpec(name="mosquitto", command=["mosquitto", "-c", "/etc/mosquitto/mosquitto.conf"],
                    ready_check=_mqtt_ready),
        ServiceSpec(name="home-service",
                    command=["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]),
    ]
```

Update `main()` to call `build_services()` instead of the removed `SERVICES` constant, and
add `import os` + `from shared.config import data_root` at the top. Redis/mosquitto config
file paths are finalized in Part 2 (the Containerfile provides `mosquitto.conf` and the
redis data dir); the `ready_check` probes are what Part 2 relies on.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/runner/ -v`
Expected: PASS (new tests + existing `tests/runner/test_supervisor.py`).

- [ ] **Step 6: Lint, type-check, commit**

```bash
ruff check runner/ tests/runner/ --fix && ruff format runner/ tests/runner/
mypy --strict runner/
git add -A
git commit -m "feat(runner): supervise native binaries + readiness gate + optional infra services"
```

---

### Task 7: Container-friendly secrets backend in `shared/secrets.py`

`keyring` has no working backend inside a Linux container (no Secret Service). Add explicit
backend selection: encrypted-file (`keyrings.cryptfile`) under `ALFRED_DATA_DIR/secrets`,
unlocked by `ALFRED_SECRETS_PASSPHRASE`; macOS keeps the native Keychain by default.

**Files:**
- Modify: `pyproject.toml` (add `keyrings.cryptfile` to base deps)
- Modify: `shared/secrets.py` (add `configure_backend()`, call at import)
- Test: `tests/shared/test_secrets_backend.py` (create)

**Interfaces:**
- Consumes: `shared.config.data_path` — **but `shared/` must stay dependency-free of `core`**; `shared.config` is same-package, allowed.
- Produces:
  - `select_backend_name() -> str` — `"cryptfile"` when `ALFRED_SECRETS_BACKEND=cryptfile` (or auto on non-macOS/container), else `"native"`.
  - `configure_backend() -> None` — sets `keyring`'s active backend accordingly.

- [ ] **Step 1: Write the failing test**

Create `tests/shared/test_secrets_backend.py`:

```python
"""Secrets backend selection is env/platform driven."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared import secrets


def test_explicit_cryptfile_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    assert secrets.select_backend_name() == "cryptfile"


def test_native_selection_on_macos_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_SECRETS_BACKEND", raising=False)
    monkeypatch.setattr(secrets.sys, "platform", "darwin")
    assert secrets.select_backend_name() == "native"


def test_auto_cryptfile_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_SECRETS_BACKEND", raising=False)
    monkeypatch.setattr(secrets.sys, "platform", "linux")
    assert secrets.select_backend_name() == "cryptfile"


def test_configure_cryptfile_sets_keyring(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_PASSPHRASE", "test-pass")
    secrets.configure_backend()
    import keyring

    kr = keyring.get_keyring()
    assert kr.__class__.__name__ == "CryptFileKeyring"
    # round-trips through the encrypted file:
    secrets.set_secret("demo", "token", "sekret")
    assert secrets.get_secret("demo", "token") == "sekret"
    assert (tmp_path / "secrets").is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/shared/test_secrets_backend.py -v`
Expected: FAIL — `select_backend_name`/`configure_backend` don't exist; `keyrings.cryptfile` not installed.

- [ ] **Step 3: Add the dependency**

In `pyproject.toml`, add to the base `dependencies` list (after `keyring>=25.0`):

```toml
    "keyrings.cryptfile>=1.3",
```

Install into the worktree venv:

```bash
uv pip install "keyrings.cryptfile>=1.3"
```

- [ ] **Step 4: Write minimal implementation**

In `shared/secrets.py`, add `import os` and `import sys` at the top, and after the `SERVICE`
constant add:

```python
def select_backend_name() -> str:
    """Choose the keyring backend: 'native' (macOS default) or 'cryptfile' (container/Linux)."""
    explicit = os.getenv("ALFRED_SECRETS_BACKEND", "").strip().lower()
    if explicit in ("cryptfile", "native"):
        return explicit
    return "native" if sys.platform == "darwin" else "cryptfile"


def configure_backend() -> None:
    """Configure the active keyring backend based on select_backend_name()."""
    if select_backend_name() != "cryptfile":
        return  # leave keyring's auto-detected native backend in place
    from keyrings.cryptfile.cryptfile import CryptFileKeyring

    from shared.config import data_path

    secrets_dir = data_path("secrets")
    secrets_dir.mkdir(parents=True, exist_ok=True)
    kr = CryptFileKeyring()
    kr.file_path = str(secrets_dir / "keyring.cfg")
    kr.keyring_key = os.getenv("ALFRED_SECRETS_PASSPHRASE", "alfred-insecure-default")
    keyring.set_keyring(kr)


configure_backend()
```

> The autouse `_mock_keyring` fixture in the root `conftest.py` runs *after* import and
> overrides `keyring` with `InMemoryKeyring`, so `configure_backend()` at import never
> interferes with the rest of the suite. The test above exercises `configure_backend()`
> explicitly and re-sets the backend itself.

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/shared/test_secrets_backend.py tests/shared/test_secrets.py -v`
Expected: PASS (new backend tests + existing secrets tests still green under the mock fixture).

- [ ] **Step 6: Lint, type-check, commit**

```bash
ruff check shared/secrets.py tests/shared/test_secrets_backend.py --fix && ruff format shared/secrets.py tests/shared/test_secrets_backend.py
mypy --strict shared/
git add pyproject.toml uv.lock shared/secrets.py tests/shared/test_secrets_backend.py
git commit -m "feat(secrets): add container-friendly cryptfile keyring backend"
```

---

### Task 8: `ALFRED_TRUSTED_NETWORKS` for the trusted-network gate

Requests through a container network arrive from the bridge/vmnet gateway, not localhost —
which would block first-run passkey registration. Make the trusted ranges configurable.

**Files:**
- Modify: `core/channels/web_server.py` (`require_trusted_network` + a `_trusted_networks()` helper)
- Test: `tests/core/channels/test_trusted_network.py` (create)

**Interfaces:**
- Consumes: nothing new (`os`, `ipaddress`, `contextlib` already imported in the module).
- Produces: `require_trusted_network` additionally allows client IPs within any CIDR in
  `ALFRED_TRUSTED_NETWORKS` (comma-separated), plus the existing localhost + Tailscale ranges.

- [ ] **Step 1: Write the failing test**

Create `tests/core/channels/test_trusted_network.py`:

```python
"""require_trusted_network honors ALFRED_TRUSTED_NETWORKS."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from core.channels.web_server import require_trusted_network


class _Req:
    def __init__(self, host: str) -> None:
        self.client = type("C", (), {"host": host})()


async def test_localhost_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS", raising=False)
    await require_trusted_network(_Req("127.0.0.1"))  # no raise


async def test_container_gateway_blocked_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_TRUSTED_NETWORKS", raising=False)
    with pytest.raises(HTTPException):
        await require_trusted_network(_Req("172.17.0.1"))


async def test_container_subnet_allowed_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_TRUSTED_NETWORKS", "172.16.0.0/12,192.168.64.0/24")
    await require_trusted_network(_Req("172.17.0.1"))  # docker bridge — no raise
    await require_trusted_network(_Req("192.168.64.5"))  # apple container vmnet — no raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/core/channels/test_trusted_network.py -v`
Expected: FAIL on `test_container_subnet_allowed_when_configured` — `172.17.0.1` raises 403.

- [ ] **Step 3: Write minimal implementation**

In `core/channels/web_server.py`, replace `require_trusted_network` (lines 179-190) and add a
helper above it:

```python
def _trusted_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Trusted CIDRs: Tailscale CGNAT + any from ALFRED_TRUSTED_NETWORKS (comma-separated)."""
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [_TAILSCALE_RANGE]
    for cidr in os.getenv("ALFRED_TRUSTED_NETWORKS", "").split(","):
        cidr = cidr.strip()
        if cidr:
            with contextlib.suppress(ValueError):
                nets.append(ipaddress.ip_network(cidr, strict=False))
    return nets


async def require_trusted_network(request: Request) -> None:
    """FastAPI dependency — restrict to localhost, Tailscale, or configured container nets."""
    client_host = request.client.host if request.client else ""
    if client_host in ("127.0.0.1", "::1", "testclient"):
        return
    try:
        addr = ipaddress.ip_address(client_host)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access restricted to trusted networks")
    for net in _trusted_networks():
        with contextlib.suppress(TypeError):  # IPv4 addr vs IPv6 net → TypeError, skip
            if addr in net:
                return
    raise HTTPException(status_code=403, detail="Access restricted to trusted networks")
```

Confirm `import contextlib` is present at the top of `web_server.py` (add it if not — it is
used elsewhere in the module).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/core/channels/test_trusted_network.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check core/channels/web_server.py tests/core/channels/test_trusted_network.py --fix
ruff format core/channels/web_server.py tests/core/channels/test_trusted_network.py
mypy --strict core/
git add -A
git commit -m "feat(channels): make trusted-network ranges configurable via ALFRED_TRUSTED_NETWORKS"
```

---

### Task 9: Full-suite regression + docs touch-up

**Files:**
- Modify: `.env.example` (document new env vars)
- Modify: `docs/` (note `ALFRED_DATA_DIR`/`ALFRED_DATA_MODE` — a short `docs/containerization.md` stub Part 2 will expand)

- [ ] **Step 1: Document new env vars**

Append to `.env.example`:

```bash
# --- Containerization (Part 1) ---
# Root dir for all runtime-writable state (SQLite, scratchpad, routines, preferences, secrets).
ALFRED_DATA_DIR=./data
# Data lifecycle: persistent (prod) | ephemeral (dev/worktree) | seed (dev w/ dummy fixtures)
ALFRED_DATA_MODE=persistent
# Secrets backend: native (macOS Keychain) | cryptfile (container/Linux encrypted file)
ALFRED_SECRETS_BACKEND=
# Passphrase for the cryptfile keyring backend (required when ALFRED_SECRETS_BACKEND=cryptfile)
ALFRED_SECRETS_PASSPHRASE=
# Extra trusted CIDRs for WebAuthn/admin (comma-separated) — set to the container subnet
ALFRED_TRUSTED_NETWORKS=
# Runner manages redis/mosquitto/home-service as child processes (set inside the container)
ALFRED_MANAGE_INFRA=
```

- [ ] **Step 2: Run the full backend suite**

Run: `HF_HUB_OFFLINE=1 .venv/bin/python -m pytest -x -q`
Expected: all previously-passing tests still pass (1107+ backend), plus the ~20 new tests
from Tasks 1–8. (Investigate any failure per systematic-debugging — do not skip.)

- [ ] **Step 3: Full type check**

Run: `mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add .env.example docs/
git commit -m "docs: document data-dir + secrets + trusted-network env vars (containerization part 1)"
```

---

## Self-Review

**1. Spec coverage (§ mapping):**
- §4 state consolidation → Tasks 1–5 (data_path, paths.py, scratchpad/cold, routines/triggers, preferences+seed). ✅
- §4 data modes (`ALFRED_DATA_MODE`) → Task 1 (`data_mode()`); ephemeral/seed *behavior* (fixtures, redis persistence flags) is wired by the container in **Part 2** — Part 1 only exposes the switch. ✅ (intentional)
- §3 process model (native ServiceSpec, readiness, infra services) → Task 6. ✅
- §10 secrets backend → Task 7. ✅
- §7 trusted-network → Task 8. ✅
- **Deferred to Part 2 (correctly):** fat Containerfile, `alfredctl`, model cache volume, port exposure, prod compose-of-one, deletion of `dev-up.sh`, docs rewrite, arm64 validation.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to". The one grep step (Task 4 Step 1) is a concrete discovery action with the exact command and expected result, not a placeholder — the transformation it feeds is fully specified in Step 4.

**3. Type consistency:** `data_path`/`data_root`/`data_mode` (Task 1) used verbatim in Tasks 2, 6, 7. `preferences_dir`/`profile_dir`/`routines_dir`/`scratchpad_path`/`episodic_cold_path`/`triggers_snapshot_dir`/`seed_defaults` (Task 2) consumed by exact name in Tasks 3–5. `ServiceSpec(command=, ready_check=)` + `build_services()` (Task 6) match the test expectations. `select_backend_name`/`configure_backend` (Task 7) and `_trusted_networks` (Task 8) match their tests.

**Note on Task 6 readiness probe vs. no-polling rule:** `_await_ready` is a bounded *startup* probe (there is no event to subscribe to for "the subprocess we just spawned now accepts connections"). It is not a steady-state loop, so it is consistent with the no-polling philosophy, which targets runtime loops. Flagged intentionally.
