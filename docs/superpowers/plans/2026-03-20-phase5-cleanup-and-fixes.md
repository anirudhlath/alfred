# Phase 5: Cleanup, Fixes & Production Readiness

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all Critical (C1-C4) and Important (I1-I8) items from `docs/backlog/remaining-work.md` so the system is production-ready for a first deployment.

**Architecture:** No new components. This phase moves a type alias to its proper home, consolidates duplicate readers, adds caching and indexing to existing stores, batches LLM calls, caps Redis stream growth, wires the Librarian into the unified runner on a schedule, and implements the signal-cli subprocess. Each task is self-contained and independently testable.

**Tech Stack:** Python 3.13+, Redis Streams, aiosqlite, Pydantic v2, pytest + pytest-asyncio, ruff, mypy --strict

**Parallelism note:** Tasks 1-4 touch entirely separate files and can be executed by parallel subagents. Tasks 5-6 both touch `consolidator.py` (do sequentially). Tasks 7-8 both touch MemoryReader (do sequentially). Tasks 9-12 are all independent.

---

## File Structure

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `shared/types.py` | Canonical `AioRedis` type alias |
| Modify | 16 files (see Task 1) | Update `AioRedis` import paths |
| Modify | `core/conscious/identity.py` | Extract "sir"/"guest" to constants |
| Modify | `shared/config.py` | Add `claude_max_tokens` field |
| Modify | `core/conscious/engine.py` | Use configurable max_tokens |
| Modify | `core/librarian/consolidator.py` | Fix imports, batch entity extraction, add MAXLEN |
| Modify | `core/memory/episodic/store.py` | Add MAXLEN to xadd |
| Move | `core/conscious/memory_reader.py` → `core/memory/reader.py` | Shared MemoryReader |
| Delete | `core/reflex/memory_reader.py` | Replaced by shared reader |
| Modify | `core/memory/routines/store.py` | In-memory index with invalidation |
| Modify | `tests/core/channels/test_web_server.py` | Assert onboarding file contents |
| Modify | `core/channels/signal_bridge/bridge.py` | Implement signal-cli subprocess |
| Modify | `core/conscious/__main__.py` | Wire Librarian periodic scheduling |
| Create | `tests/shared/test_types.py` | Verify AioRedis importable |
| Create | `tests/core/memory/test_reader.py` | Tests for relocated MemoryReader |
| Create | `tests/core/librarian/test_batch_extraction.py` | Tests for batched entity extraction |
| Create | `tests/core/channels/test_signal_send.py` | Tests for signal-cli subprocess |
| Create | `tests/core/librarian/test_scheduler.py` | Test Librarian periodic task |

---

## Task 1: Move AioRedis Type Alias to shared/types.py (I1)

**Files:**
- Create: `shared/types.py`
- Create: `tests/shared/test_types.py`
- Modify: `core/reflex/runner.py:26` (remove definition, re-export for backward compat)
- Modify: 16 consumer files (update TYPE_CHECKING imports)

- [ ] **Step 1: Write failing test**

```python
"""Verify AioRedis is importable from shared.types."""

from __future__ import annotations


def test_aioredis_importable() -> None:
    from shared.types import AioRedis

    assert AioRedis is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/shared/test_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.types'`

- [ ] **Step 3: Create shared/types.py**

```python
"""Shared type aliases used across multiple packages."""

from __future__ import annotations

from typing import Any

import redis.asyncio as aioredis

# PEP 695 type alias — the canonical location for cross-package use.
type AioRedis = aioredis.Redis[Any]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/shared/test_types.py -v`
Expected: PASS

- [ ] **Step 5: Update core/reflex/runner.py to import from shared**

Replace the type alias definition (line 26) with a re-export:

```python
# Replace:
#   type AioRedis = aioredis.Redis[Any]
# With:
from shared.types import AioRedis as AioRedis  # re-export for backward compat
```

Remove the now-unused `from typing import Any` if no other usage remains, and remove `import redis.asyncio as aioredis` if the only usage was the type alias (check — runner.py likely still uses aioredis directly).

- [ ] **Step 6: Remove duplicate AioRedis definitions**

Two files define their own `AioRedis` instead of importing — replace with the shared import:

- `bus/bridge.py:23` — has `type AioRedis = aioredis.Redis[Any]`. Delete this line, add `from shared.types import AioRedis` to its TYPE_CHECKING block.
- `core/reflex/tool_registry.py:19` — has `AioRedis = Any`. Delete this line, add `from shared.types import AioRedis` to its TYPE_CHECKING block.

- [ ] **Step 7: Update all TYPE_CHECKING imports across consumer files**

In every file that imports `from core.reflex.runner import AioRedis` (whether in a TYPE_CHECKING block or at module level), change to `from shared.types import AioRedis`.

**Exception:** `core/triggers/engine.py` and `core/triggers/store.py` use `AioRedis` as a runtime annotation (marked `# noqa: TC001`). Keep these as **module-level imports** — do NOT move to TYPE_CHECKING. Just change the source: `from shared.types import AioRedis  # noqa: TC001`.

**Important:** Two files import `AioRedis` alongside other symbols on a single line:
- `core/reflex/__main__.py` — `from core.reflex.runner import AioRedis, ensure_consumer_group, ...`
- `core/conscious/__main__.py` — `from core.reflex.runner import AioRedis, ensure_consumer_group, ...`

For both: split into two imports — keep `from core.reflex.runner import ensure_consumer_group, ...` and add a separate `from shared.types import AioRedis` in the TYPE_CHECKING block.

Files to update:

```
core/channels/signal_bridge/__main__.py
core/channels/signal_bridge/bridge.py
core/librarian/__main__.py
core/librarian/consolidator.py
core/reflex/__main__.py  (split combined import — see note above)
core/reflex/context_reader.py
core/conscious/__main__.py
core/conscious/engine.py
core/conscious/session.py
core/conscious/cost.py
core/triggers/engine.py
core/triggers/store.py
core/notifications/publisher.py
core/memory/episodic/store.py
```

- [ ] **Step 8: Update CLAUDE.md and rules files**

In `CLAUDE.md`, Gotchas section (line 133), change:
```
- Import `AioRedis` type alias from `core.reflex.runner` — never redefine as `Any`
```
to:
```
- Import `AioRedis` type alias from `shared.types` — never redefine as `Any`
```

In `.claude/rules/core/trigger-engine.md`, if it mentions importing AioRedis from `core.reflex.runner`, update to `shared.types`.

- [ ] **Step 9: Run full test suite + mypy**

Run: `uv run python -m pytest -x -q && uv run ruff check . && uv run mypy --strict shared/types.py`
Expected: All pass

- [ ] **Step 10: Commit**

```bash
git add shared/types.py tests/shared/test_types.py
git add core/ bus/ shared/ CLAUDE.md .claude/rules/
git commit -m "refactor: move AioRedis type alias to shared/types.py"
```

---

## Task 2: Extract Identity Constants (I4)

**Files:**
- Modify: `core/conscious/identity.py`
- Modify: `tests/core/conscious/test_identity.py` (if it uses literal "sir")
- Modify: `tests/core/conscious/test_identity_local_trust.py` (if it uses literal "sir")

- [ ] **Step 1: Add constants at module level in identity.py**

At the top of `core/conscious/identity.py`, after imports:

```python
# Identity constants
IDENTITY_SIR = "sir"
IDENTITY_GUEST = "guest"
```

- [ ] **Step 2: Replace all literal usages in identity.py**

Replace every `identity="sir"` with `identity=IDENTITY_SIR` and every `identity="guest"` with `identity=IDENTITY_GUEST`. Also replace the comparison `if identity_claim == "sir":` with `if identity_claim == IDENTITY_SIR:`.

Lines to change:
- Line 26: `identity="sir"` → `identity=IDENTITY_SIR`
- Line 33: `identity="guest"` → `identity=IDENTITY_GUEST`
- Line 44: `identity="sir"` → `identity=IDENTITY_SIR`
- Line 51: `identity="guest"` → `identity=IDENTITY_GUEST`
- Line 72: `if identity_claim == "sir":` → `if identity_claim == IDENTITY_SIR:`
- Line 74: `identity="sir"` → `identity=IDENTITY_SIR`
- Line 83: `identity="guest"` → `identity=IDENTITY_GUEST`

- [ ] **Step 3: Run existing identity tests**

Run: `uv run python -m pytest tests/core/conscious/test_identity.py tests/core/conscious/test_identity_local_trust.py -v`
Expected: All PASS (no behavior change)

- [ ] **Step 4: Run ruff + mypy**

Run: `uv run ruff check core/conscious/identity.py && uv run mypy --strict core/conscious/identity.py`
Expected: Clean

- [ ] **Step 5: Commit**

```bash
git add core/conscious/identity.py
git commit -m "refactor(identity): extract sir/guest magic strings to constants"
```

---

## Task 3: Configurable max_tokens (I3)

**Files:**
- Modify: `shared/config.py`
- Modify: `core/conscious/engine.py:235`

- [ ] **Step 1: Add field to AlfredConfig**

In `shared/config.py`, add after `claude_model` (line 33):

```python
    claude_max_tokens: int = 2048
```

And in `from_env()`, add after the `claude_model` line (line 71):

```python
            claude_max_tokens=int(os.getenv("CLAUDE_MAX_TOKENS", "2048")),
```

- [ ] **Step 2: Update ConsciousEngine to accept and use the config value**

In `core/conscious/engine.py`, the constructor (lines 61-77) has many parameters. Add `claude_max_tokens` right after `claude_api_key` (line 72), before the optional `memory_reader`:

```python
        claude_api_key: str = "",
        claude_max_tokens: int = 2048,  # ← ADD THIS LINE
        memory_reader: MemoryReader | None = None,
```

Store it in `__init__` body, after `self._api_key = claude_api_key` (line 87):

```python
        self._api_key = claude_api_key
        self._max_tokens = claude_max_tokens  # ← ADD THIS LINE
        self._memory_reader = memory_reader
```

Replace line 235 (`"max_tokens": 2048`) with:

```python
            "max_tokens": self._max_tokens,
```

- [ ] **Step 3: Update __main__.py to pass config value**

In `core/conscious/__main__.py`, where `ConsciousEngine(...)` is constructed, add:

```python
        claude_max_tokens=config.claude_max_tokens,
```

- [ ] **Step 4: Run existing engine tests**

Run: `uv run python -m pytest tests/core/conscious/test_engine.py -v`
Expected: All PASS (default 2048 preserves behavior)

- [ ] **Step 5: Commit**

```bash
git add shared/config.py core/conscious/engine.py core/conscious/__main__.py
git commit -m "feat(config): make claude_max_tokens configurable via CLAUDE_MAX_TOKENS env var"
```

---

## Task 4: Onboarding Test File Assertions (I6)

**Files:**
- Modify: `tests/core/channels/test_web_server.py:62-110`

- [ ] **Step 1: Add assertions to existing test**

After the existing `assert resp.status_code == 200` line (line 109), add:

```python
    # Verify preference files were actually written with correct content
    assert "personal.md" in written_files, "personal.md should have been written"
    personal_content = written_files["personal.md"]
    # Parse YAML frontmatter to verify structured data, not string matching
    assert personal_content.startswith("---\n"), "Preference files should have YAML frontmatter"
    import yaml
    # Extract frontmatter between --- markers
    parts = personal_content.split("---\n", 2)
    frontmatter = yaml.safe_load(parts[1])
    assert frontmatter is not None, "Frontmatter should parse as valid YAML"

    assert "proactivity.md" in written_files, "proactivity.md should have been written"
    proactivity_content = written_files["proactivity.md"]
    proactivity_parts = proactivity_content.split("---\n", 2)
    proactivity_fm = yaml.safe_load(proactivity_parts[1])
    assert proactivity_fm is not None, "Proactivity frontmatter should parse as valid YAML"
```

- [ ] **Step 2: Run the test**

Run: `uv run python -m pytest tests/core/channels/test_web_server.py::test_onboarding_endpoint_saves_preferences -v`
Expected: PASS (if the endpoint correctly writes files with these values). If it fails, inspect what `written_files` actually contains and adjust assertions to match the actual output format.

- [ ] **Step 3: Commit**

```bash
git add tests/core/channels/test_web_server.py
git commit -m "test(onboarding): assert preference file contents, not just HTTP status"
```

---

## Task 5: Fix Librarian Imports (I5)

**Files:**
- Modify: `core/librarian/consolidator.py`

- [ ] **Step 1: Move json import to module level**

In `core/librarian/consolidator.py`, add `import json` to the module-level imports (after `import logging`):

```python
import json
import logging
```

Remove the `import json` from inside `_extract_entities()` (line 94) and `_update_semantic_memory()` (if present).

- [ ] **Step 2: Move litellm to TYPE_CHECKING**

Add to the TYPE_CHECKING block:

```python
if TYPE_CHECKING:
    from core.memory.episodic.store import EpisodicStore
    from core.memory.routines.store import RoutineStore
    from shared.types import AioRedis
```

Note: `litellm` is NOT added to TYPE_CHECKING — it's imported at runtime inside method bodies since it's an optional dependency.

In the method bodies (`_extract_entities` and `_update_semantic_memory`), keep the runtime `import litellm` since it's a deferred dependency (may not be installed). But structure it properly:

```python
    async def _extract_entities(self, text: str) -> list[str]:
        if not self._api_key:
            return []
        try:
            import litellm  # runtime import — optional dependency

            response = await litellm.acompletion(...)
            raw = response.choices[0].message.content or "[]"
            result: list[str] = json.loads(raw)  # now uses module-level json
            return result
        except Exception as exc:
            logger.warning("Entity extraction failed: %s", exc)
            return []
```

- [ ] **Step 3: Run librarian tests**

Run: `uv run python -m pytest tests/core/librarian/ -v`
Expected: All PASS

- [ ] **Step 4: Run mypy**

Run: `uv run mypy --strict core/librarian/consolidator.py`
Expected: Clean

- [ ] **Step 5: Commit**

```bash
git add core/librarian/consolidator.py
git commit -m "refactor(librarian): module-level json import, structured litellm deferred import"
```

---

## Task 6: Batch Entity Extraction + Stream MAXLEN (C3 + C4)

**Files:**
- Modify: `core/librarian/consolidator.py`
- Modify: `core/memory/episodic/store.py:53`
- Create: `tests/core/librarian/test_batch_extraction.py`

- [ ] **Step 1: Write failing test for batch extraction**

```python
"""Tests for batched entity extraction in the Librarian."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.librarian.consolidator import Librarian


@pytest.fixture()
def librarian() -> Librarian:
    return Librarian(
        redis=AsyncMock(),
        episodic_store=AsyncMock(),
        routine_store=AsyncMock(),
        claude_api_key="test-key",
    )


@pytest.mark.asyncio
async def test_batch_extraction_single_llm_call(librarian: Librarian) -> None:
    """Multiple scratchpad lines should produce exactly ONE LLM call for entities."""
    lines = [
        "2026-03-19T10:00:00Z [reflex] home.turn_on_light({entity: light.living_room}) → success",
        "2026-03-19T10:05:00Z [reflex] home.set_temperature({entity: climate.main, temp: 72}) → success",
        "2026-03-19T10:10:00Z [conscious] user='good morning' → 42 chars (actions=none, tokens=100+20)",
    ]

    mock_response = AsyncMock()
    mock_response.choices = [
        AsyncMock(
            message=AsyncMock(
                content=(
                    '[["light.living_room", "living room"], '
                    '["climate.main", "main thermostat"], '
                    '["none"]]'
                )
            )
        )
    ]
    mock_response.usage = AsyncMock(prompt_tokens=200, completion_tokens=50)

    with patch("litellm.acompletion", return_value=mock_response) as mock_llm:
        entries = await librarian._extract_episodic_entries(lines)

    # Exactly ONE LLM call for all 3 lines
    assert mock_llm.call_count == 1
    assert len(entries) == 3
    assert "light.living_room" in entries[0].entities


@pytest.mark.asyncio
async def test_batch_extraction_no_api_key() -> None:
    """Without API key, entities should be empty (no LLM call)."""
    lib = Librarian(
        redis=AsyncMock(),
        episodic_store=AsyncMock(),
        routine_store=AsyncMock(),
        claude_api_key="",
    )
    lines = ["2026-03-19T10:00:00Z [reflex] action → result"]
    entries = await lib._extract_episodic_entries(lines)
    assert len(entries) == 1
    assert entries[0].entities == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/core/librarian/test_batch_extraction.py -v`
Expected: FAIL — currently makes 3 LLM calls instead of 1

- [ ] **Step 3: Replace _extract_entities + _extract_episodic_entries with batched version**

Replace both methods in `core/librarian/consolidator.py`:

```python
    async def _extract_entities_batch(self, summaries: list[str]) -> list[list[str]]:
        """Extract entities from multiple summaries in a single LLM call.

        Returns a list of entity lists, one per input summary.
        """
        if not self._api_key or not summaries:
            return [[] for _ in summaries]
        try:
            import litellm  # runtime import — optional dependency

            numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(summaries))
            response = await litellm.acompletion(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract named entities from each numbered home automation observation. "
                            "Return a JSON array of arrays — one inner array per observation. "
                            "Each inner array contains entity names (devices, rooms, people, services). "
                            'Example for 2 observations: [["light.living_room", "living room"], '
                            '["climate.main", "thermostat"]]'
                        ),
                    },
                    {"role": "user", "content": numbered},
                ],
                max_tokens=max(200, 100 * len(summaries)),
                api_key=self._api_key,
            )
            raw = response.choices[0].message.content or "[]"
            parsed: list[list[str]] = json.loads(raw)
            # Ensure we have the right number of results
            while len(parsed) < len(summaries):
                parsed.append([])
            return parsed[: len(summaries)]
        except Exception as exc:
            logger.warning("Batch entity extraction failed: %s", exc)
            return [[] for _ in summaries]

    async def _extract_episodic_entries(self, scratchpad_lines: list[str]) -> list[EpisodicEntry]:
        """Extract episodic entries from scratchpad lines.

        Uses a single batched LLM call for entity extraction across all lines.
        """
        # Parse all lines first
        parsed: list[tuple[str, str]] = []  # (source, summary)
        for line in scratchpad_lines:
            parts = line.split("] ", 1)
            source = "unknown"
            summary = line
            if len(parts) == 2:
                source_part = parts[0].split("[", 1)
                if len(source_part) == 2:
                    source = source_part[1]
                summary = parts[1]
            parsed.append((source, summary.strip()))

        # Single batched LLM call for all entity extraction
        summaries = [s for _, s in parsed]
        all_entities = await self._extract_entities_batch(summaries)

        entries: list[EpisodicEntry] = []
        for (source, summary), entities in zip(parsed, all_entities, strict=True):
            entries.append(
                EpisodicEntry(
                    id=str(uuid4()),
                    timestamp=datetime.now(UTC),
                    source=source,
                    summary=summary,
                    entities=entities,
                    valence="neutral",
                )
            )
        return entries
```

Also remove the old `_extract_entities()` method (lines 89-120) since it's replaced by `_extract_entities_batch()`.

- [ ] **Step 3b: Update existing test_consolidator_intelligence.py**

`tests/core/librarian/test_consolidator_intelligence.py:test_extract_entities_with_claude` patches `litellm.acompletion` and calls the old `_extract_entities()` method. Update it to use the new batch API:
- Change the mock response to return `list[list[str]]` format (array of arrays) instead of flat `list[str]`
- Change the test to call `_extract_entities_batch()` instead of `_extract_entities()`
- Or delete the test entirely if the new `test_batch_extraction.py` covers the same scenarios

- [ ] **Step 4: Add MAXLEN to EpisodicStore.write()**

In `core/memory/episodic/store.py`, line 53, change:

```python
        await self._redis.xadd(EPISODIC_STREAM, data)
```

to:

```python
        await self._redis.xadd(EPISODIC_STREAM, data, maxlen=10000, approximate=True)
```

This caps the hot stream at ~10,000 entries (approximate uses `~` for efficiency).

- [ ] **Step 5: Run all librarian + episodic tests**

Run: `uv run python -m pytest tests/core/librarian/ tests/core/memory/ -v`
Expected: All PASS

- [ ] **Step 6: Run mypy**

Run: `uv run mypy --strict core/librarian/consolidator.py core/memory/episodic/store.py`
Expected: Clean

- [ ] **Step 7: Commit**

```bash
git add core/librarian/consolidator.py core/memory/episodic/store.py tests/core/librarian/test_batch_extraction.py
git commit -m "fix(librarian): batch entity extraction into single LLM call, cap episodic stream at ~10k"
```

---

## Task 7: Consolidate MemoryReader (I7)

**Files:**
- Move: `core/conscious/memory_reader.py` → `core/memory/reader.py`
- Delete: `core/reflex/memory_reader.py`
- Create: `core/memory/reader.py`
- Create: `tests/core/memory/test_reader.py`
- Modify: `core/conscious/engine.py` (update import)
- Modify: `core/conscious/__main__.py` (update import)
- Modify: `core/reflex/engine.py` (replace `read_preferences()` function with MemoryReader)
- Modify: `evals/pipeline.py:12` (update import — currently imports `read_preferences` from reflex)
- Delete: `core/reflex/tests/test_memory_reader.py` (covered by new tests)
- Modify: `tests/core/conscious/test_memory_reader.py` (update import or delete if duplicated by new tests)
- Modify: `tests/integration/test_conscious_pipeline.py:18` (update import from old location)

- [ ] **Step 1: Create core/memory/reader.py**

Copy `core/conscious/memory_reader.py` to `core/memory/reader.py` with no changes to the class. This is the canonical location since it reads from `core/memory/`.

- [ ] **Step 2: Create tests/core/memory/test_reader.py**

Copy tests from `tests/core/conscious/test_memory_reader.py`, updating the import:

```python
from core.memory.reader import MemoryReader
```

- [ ] **Step 3: Run new tests**

Run: `uv run python -m pytest tests/core/memory/test_reader.py -v`
Expected: PASS

- [ ] **Step 4: Add backward-compatible function for evals only**

Add a standalone function at the bottom of `core/memory/reader.py` for the evals pipeline (which doesn't own a long-lived reader):

```python
def read_preferences(preferences_dir: str) -> str:
    """Backward-compatible function for evals pipeline.

    WARNING: Creates a new MemoryReader per call — no caching benefit.
    For long-lived processes, construct and reuse a MemoryReader instance instead.
    """
    reader = MemoryReader(
        preferences_dir=Path(preferences_dir),
        profile_dir=Path(preferences_dir).parent / "profile",
    )
    return reader.get_preferences()
```

- [ ] **Step 5: Update all consumers**

In `core/conscious/engine.py`, update import:
```python
from core.memory.reader import MemoryReader
```

In `core/conscious/__main__.py`, update import:
```python
from core.memory.reader import MemoryReader
```

In `core/reflex/engine.py`, **replace the `read_preferences` function call** with a `MemoryReader` instance. In the `ReflexEngine.__init__()`, accept an optional `memory_reader: MemoryReader | None = None` parameter and store it. In `_get_preferences()`, use `self._memory_reader.get_preferences()` if available, falling back to the old behavior. This ensures Task 8's TTL cache is effective for the hot reflex path.

In `core/reflex/__main__.py`, construct a `MemoryReader` and pass it when creating `ReflexEngine`:
```python
from core.memory.reader import MemoryReader
memory_reader = MemoryReader(
    preferences_dir=Path(preferences_dir),
    profile_dir=Path(preferences_dir).parent / "profile",
)
engine = ReflexEngine(..., memory_reader=memory_reader)
```

In `evals/pipeline.py:12`, update import:
```python
from core.memory.reader import read_preferences
```

In `tests/integration/test_conscious_pipeline.py:18`, update import:
```python
from core.memory.reader import MemoryReader
```

- [ ] **Step 6: Delete old files**

Delete `core/conscious/memory_reader.py`, `core/reflex/memory_reader.py`, and `core/reflex/tests/test_memory_reader.py`.

- [ ] **Step 7: Run full test suite**

Run: `uv run python -m pytest -x -q`
Expected: All PASS

- [ ] **Step 8: Run mypy on affected files**

Run: `uv run mypy --strict core/memory/reader.py core/conscious/engine.py core/reflex/`
Expected: Clean

- [ ] **Step 9: Commit**

```bash
git add core/memory/reader.py tests/core/memory/test_reader.py
git rm core/conscious/memory_reader.py core/reflex/memory_reader.py
git add core/conscious/engine.py core/conscious/__main__.py core/reflex/ evals/pipeline.py tests/integration/
git commit -m "refactor: consolidate MemoryReader to core/memory/reader.py (single implementation)"
```

---

## Task 8: MemoryReader TTL Caching (I2)

**Files:**
- Modify: `core/memory/reader.py` (after Task 7 relocates it)
- Modify: `tests/core/memory/test_reader.py`

- [ ] **Step 1: Write failing test for caching**

Add to `tests/core/memory/test_reader.py`:

```python
import time


def test_preferences_cached_within_ttl(memory_dirs: tuple[Path, Path]) -> None:
    """Repeated calls within TTL should return cached content without re-reading files."""
    prefs, profile = memory_dirs
    (prefs / "personal.md").write_text("---\n---\n\n# Personal\n\n- Wake: 07:30\n")
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile, cache_ttl_seconds=60)

    result1 = reader.get_preferences()
    # Modify file — but cache should still return old value
    (prefs / "personal.md").write_text("---\n---\n\n# Personal\n\n- Wake: 08:00\n")
    result2 = reader.get_preferences()
    assert result1 == result2  # cached
    assert "07:30" in result2


def test_preferences_refreshed_after_ttl(memory_dirs: tuple[Path, Path]) -> None:
    """After TTL expires, the reader should re-read files."""
    prefs, profile = memory_dirs
    (prefs / "personal.md").write_text("---\n---\n\n# Personal\n\n- Wake: 07:30\n")
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile, cache_ttl_seconds=0)

    result1 = reader.get_preferences()
    (prefs / "personal.md").write_text("---\n---\n\n# Personal\n\n- Wake: 08:00\n")
    time.sleep(0.01)  # TTL=0 means always-expired; sleep ensures monotonic() advances
    result2 = reader.get_preferences()
    assert "08:00" in result2  # refreshed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/core/memory/test_reader.py::test_preferences_cached_within_ttl -v`
Expected: FAIL — no `cache_ttl_seconds` parameter

- [ ] **Step 3: Add TTL caching to MemoryReader**

Update `core/memory/reader.py`:

```python
import time

class MemoryReader:
    def __init__(
        self,
        preferences_dir: Path,
        profile_dir: Path,
        default_proactivity: str = "opinionated",
        cache_ttl_seconds: float = 60.0,
    ) -> None:
        self._preferences_dir = Path(preferences_dir)
        self._profile_dir = Path(profile_dir)
        self._default_proactivity = default_proactivity
        self._cache_ttl = cache_ttl_seconds
        self._cached_proactivity: str | None = None
        self._cached_preferences: str | None = None
        self._cached_profile: str | None = None
        self._prefs_cached_at: float = 0.0
        self._profile_cached_at: float = 0.0

    def _is_expired(self, cached_at: float) -> bool:
        return (time.monotonic() - cached_at) >= self._cache_ttl

    def get_preferences(self) -> str:
        if self._cached_preferences is not None and not self._is_expired(self._prefs_cached_at):
            return self._cached_preferences
        self._cached_preferences = self._read_all_md(self._preferences_dir)
        self._prefs_cached_at = time.monotonic()
        return self._cached_preferences

    def get_profile(self) -> str:
        if self._cached_profile is not None and not self._is_expired(self._profile_cached_at):
            return self._cached_profile
        self._cached_profile = self._read_all_md(self._profile_dir)
        self._profile_cached_at = time.monotonic()
        return self._cached_profile
```

Keep `_read_all_md`, `_read_md_body`, and `get_proactivity_level` unchanged (proactivity is already cached via `_cached_proactivity`).

- [ ] **Step 4: Run all reader tests**

Run: `uv run python -m pytest tests/core/memory/test_reader.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/memory/reader.py tests/core/memory/test_reader.py
git commit -m "feat(memory): add TTL caching to MemoryReader (60s default)"
```

---

## Task 9: RoutineStore In-Memory Index (I8)

**Files:**
- Modify: `core/memory/routines/store.py`
- Modify: `tests/core/memory/` (add routine cache tests if needed)

- [ ] **Step 1: Write failing test**

```python
"""Tests for RoutineStore in-memory caching."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.memory.routines.store import RoutineStore
from core.memory.schemas import RoutineSpec, RoutineStep


def _make_routine(name: str, state: str = "active") -> RoutineSpec:
    """Helper to build a valid RoutineSpec."""
    return RoutineSpec(
        name=name,
        trigger_pattern="weekdays 07:30",
        steps=[RoutineStep(description="Turn on bedroom lights", action=None)],
        confidence=0.8,
        learned_from=["episode-1"],
        state=state,
    )


@pytest.fixture()
def store(tmp_path: Path) -> RoutineStore:
    return RoutineStore(routines_dir=str(tmp_path))


def test_list_all_caches_after_first_call(store: RoutineStore, tmp_path: Path) -> None:
    """Second list_all() should return cached result without re-globbing."""
    store.save(_make_routine("morning_lights"))

    result1 = store.list_all()
    # Delete file — but cache should persist
    (tmp_path / "morning_lights.yaml").unlink()
    result2 = store.list_all()
    assert len(result2) == 1  # still cached
    assert result2[0].name == "morning_lights"


def test_save_invalidates_cache(store: RoutineStore) -> None:
    """Saving a new routine should invalidate the cache."""
    store.save(_make_routine("r1"))
    assert len(store.list_all()) == 1

    store.save(_make_routine("r2"))
    assert len(store.list_all()) == 2  # cache invalidated, re-read
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/core/memory/routines/test_routine_cache.py -v`
Expected: FAIL — `list_all()` re-reads from disk, second call returns 0

- [ ] **Step 3: Implement in-memory index**

Update `core/memory/routines/store.py`:

```python
class RoutineStore:
    def __init__(self, routines_dir: str = _DEFAULT_ROUTINES_DIR) -> None:
        self._dir = Path(routines_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: list[RoutineSpec] | None = None

    def _invalidate(self) -> None:
        self._cache = None

    def save(self, routine: RoutineSpec) -> None:
        path = self._path(routine.name)
        data = routine.model_dump(mode="json")
        atomic_write(path, yaml.dump(data, default_flow_style=False, sort_keys=False))
        self._invalidate()
        logger.debug("Saved routine '%s'", routine.name)

    def delete(self, name: str) -> None:
        path = self._path(name)
        if path.exists():
            path.unlink()
            self._invalidate()
            logger.debug("Deleted routine '%s'", name)

    def list_all(self) -> list[RoutineSpec]:
        if self._cache is not None:
            return list(self._cache)
        routines: list[RoutineSpec] = []
        for path in self._dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(path.read_text())
                routines.append(RoutineSpec.model_validate(data))
            except Exception as e:
                logger.warning("Failed to load routine from %s: %s", path, e)
        self._cache = routines
        return list(routines)
```

- [ ] **Step 4: Run all routine tests**

Run: `uv run python -m pytest tests/core/memory/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/memory/routines/store.py tests/core/memory/routines/test_routine_cache.py
git commit -m "feat(routines): add in-memory index to RoutineStore, invalidate on write/delete"
```

---

## Task 10: Signal Bridge _send_signal() Implementation (C2)

**Files:**
- Modify: `core/channels/signal_bridge/bridge.py:42-45`
- Create: `tests/core/channels/test_signal_send.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for signal-cli subprocess integration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from core.channels.signal_bridge.bridge import SignalBridge


@pytest.mark.asyncio
async def test_send_signal_calls_subprocess() -> None:
    """_send_signal should invoke signal-cli send via subprocess."""
    bridge = SignalBridge(redis=AsyncMock(), phone_number="+1234567890")

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
        await bridge._send_signal("+1234567890", "Test message")

    mock_exec.assert_called_once()
    args = mock_exec.call_args[0]
    assert "signal-cli" in args
    assert "send" in args
    assert "+1234567890" in args
    assert "Test message" in args


@pytest.mark.asyncio
async def test_send_signal_handles_failure() -> None:
    """_send_signal should log warning on subprocess failure, not raise."""
    bridge = SignalBridge(redis=AsyncMock(), phone_number="+1234567890")

    mock_proc = AsyncMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        # Should not raise
        await bridge._send_signal("+1234567890", "Test")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/core/channels/test_signal_send.py -v`
Expected: FAIL — current stub doesn't call subprocess

- [ ] **Step 3: Implement _send_signal()**

Replace the stub in `core/channels/signal_bridge/bridge.py`:

```python
    async def _send_signal(self, recipient: str, message: str) -> None:
        """Send a message via signal-cli subprocess."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "signal-cli",
                "-u",
                self._phone,
                "send",
                "-m",
                message,
                recipient,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(
                    "signal-cli send failed (code %d): %s",
                    proc.returncode,
                    stderr.decode(errors="replace")[:200],
                )
            else:
                logger.info("Sent Signal message to %s", recipient[:6])
        except FileNotFoundError:
            logger.error("signal-cli not found — install it to enable Signal delivery")
        except Exception as exc:
            logger.warning("Failed to send Signal message: %s", exc)
```

Add `import asyncio` to the imports at the top of the file if not already present.

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/core/channels/test_signal_send.py -v`
Expected: PASS

- [ ] **Step 5: Run mypy**

Run: `uv run mypy --strict core/channels/signal_bridge/bridge.py`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add core/channels/signal_bridge/bridge.py tests/core/channels/test_signal_send.py
git commit -m "feat(signal): implement signal-cli subprocess for outbound messages"
```

---

## Task 11: Librarian Periodic Scheduling (C1)

**Files:**
- Create: `core/librarian/scheduler.py`
- Create: `tests/core/librarian/test_scheduler.py`
- Modify: `core/conscious/__main__.py` (wire scheduler as background task)

The Librarian runs as a periodic task inside the conscious process (since it shares Redis and config), NOT as a separate supervised service. It runs once per hour by default (configurable), calling `Librarian.consolidate()`.

> **Note:** The backlog (C1) originally listed `runner/__main__.py` as the fix location. We place it in `core/conscious/__main__.py` instead because: (a) the Librarian depends on `EpisodicStore`, `RoutineStore`, and `MemoryReader` — all already constructed in the conscious process, (b) running it as a separate supervised process would duplicate all these dependency constructions. Update the backlog C1 fix-location to `core/conscious/__main__.py` in Task 12.

- [ ] **Step 1: Write failing test**

```python
"""Tests for Librarian periodic scheduling."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from core.librarian.scheduler import LibrarianScheduler


@pytest.mark.asyncio
async def test_scheduler_calls_consolidate() -> None:
    """Scheduler should call consolidate() on the interval."""
    mock_librarian = AsyncMock()
    mock_librarian.consolidate = AsyncMock(return_value={"entries_processed": 0})

    scheduler = LibrarianScheduler(librarian=mock_librarian, interval_seconds=0.01)

    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert mock_librarian.consolidate.call_count >= 1


@pytest.mark.asyncio
async def test_scheduler_survives_consolidation_error() -> None:
    """Scheduler should keep running if consolidate() raises."""
    mock_librarian = AsyncMock()
    call_count = 0

    async def failing_then_ok() -> dict[str, int]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("LLM unavailable")
        return {"entries_processed": 0}

    mock_librarian.consolidate = failing_then_ok

    scheduler = LibrarianScheduler(librarian=mock_librarian, interval_seconds=0.01)

    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Should have been called at least twice (first fails, second succeeds)
    assert call_count >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/core/librarian/test_scheduler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.librarian.scheduler'`

- [ ] **Step 3: Create core/librarian/scheduler.py**

```python
"""Periodic scheduler for Librarian consolidation cycles."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.librarian.consolidator import Librarian

logger = logging.getLogger(__name__)


class LibrarianScheduler:
    """Runs Librarian.consolidate() on a periodic interval.

    Designed to run as a background asyncio task inside the conscious process.
    Errors in consolidation are logged but never crash the scheduler.
    """

    def __init__(
        self,
        librarian: Librarian,
        interval_seconds: float = 3600.0,  # 1 hour default
    ) -> None:
        self._librarian = librarian
        self._interval = interval_seconds

    async def run(self) -> None:
        """Run consolidation cycles forever until cancelled."""
        logger.info(
            "Librarian scheduler started (interval=%ds)", int(self._interval)
        )
        while True:
            try:
                result = await self._librarian.consolidate()
                logger.info("Librarian cycle complete: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Librarian consolidation failed: %s", exc)

            await asyncio.sleep(self._interval)
```

- [ ] **Step 4: Run tests**

Run: `uv run python -m pytest tests/core/librarian/test_scheduler.py -v`
Expected: PASS

- [ ] **Step 5: Wire into core/conscious/__main__.py**

In `core/conscious/__main__.py`, after the ScratchpadWriter task creation, add:

```python
    from core.librarian.scheduler import LibrarianScheduler

    librarian = Librarian(
        redis=r,
        episodic_store=episodic_store,
        routine_store=routine_store,
        preferences_dir=str(memory_dir / "preferences"),
        profile_dir=str(memory_dir / "profile"),
        claude_api_key=config.claude_api_key,
        claude_model=config.claude_model,
    )
    librarian_scheduler = LibrarianScheduler(
        librarian=librarian,
        interval_seconds=float(os.getenv("LIBRARIAN_INTERVAL_SECONDS", "3600")),
    )
    librarian_task = asyncio.create_task(librarian_scheduler.run())
```

And in the shutdown section, cancel it:

```python
    librarian_task.cancel()
```

- [ ] **Step 6: Add LIBRARIAN_INTERVAL_SECONDS to config docs**

In `shared/config.py`, add a comment in the Phase 3: Memory section:

```python
    # Librarian interval: LIBRARIAN_INTERVAL_SECONDS env var (default: 3600s = 1hr)
    # Not in AlfredConfig since it's only used by the conscious process scheduler.
```

- [ ] **Step 7: Run full test suite**

Run: `uv run python -m pytest -x -q`
Expected: All PASS

- [ ] **Step 8: Run ruff + mypy**

Run: `uv run ruff check . --fix && uv run ruff format . && uv run mypy --strict core/librarian/scheduler.py`
Expected: Clean

- [ ] **Step 9: Commit**

```bash
git add core/librarian/scheduler.py tests/core/librarian/test_scheduler.py
git add core/conscious/__main__.py shared/config.py
git commit -m "feat(librarian): add periodic scheduler, wire into conscious process (1hr default)"
```

---

## Task 12: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `uv run python -m pytest -x -q`
Expected: All tests PASS

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: Clean

- [ ] **Step 3: Run mypy on all modified packages**

Run: `uv run mypy --strict shared/ core/ runner/ bus/ tests/`
Expected: Clean

- [ ] **Step 4: Update backlog**

Mark C1-C4 and I1-I8 as DONE in `docs/backlog/remaining-work.md` by adding `~~strikethrough~~` or a DONE annotation to each item.

- [ ] **Step 5: Commit**

```bash
git add docs/backlog/remaining-work.md
git commit -m "docs: mark Phase 5 Critical + Important items as complete"
```

---

## Summary of Gaps Addressed

| ID | Gap | Task | Fix |
|----|-----|------|-----|
| C1 | Librarian never scheduled | Task 11 | Periodic scheduler in conscious process |
| C2 | Signal _send_signal stub | Task 10 | signal-cli subprocess |
| C3 | Episodic stream unbounded | Task 6 | MAXLEN ~10000 on xadd |
| C4 | Librarian N+1 LLM calls | Task 6 | Batched extraction prompt |
| I1 | AioRedis wrong location | Task 1 | Moved to shared/types.py |
| I2 | MemoryReader no caching | Task 8 | TTL-based cache (60s) |
| I3 | max_tokens hardcoded | Task 3 | CLAUDE_MAX_TOKENS env var |
| I4 | Identity magic strings | Task 2 | IDENTITY_SIR / IDENTITY_GUEST constants |
| I5 | Librarian deferred imports | Task 5 | Module-level json, structured litellm |
| I6 | Onboarding test weak | Task 4 | Assert file contents + frontmatter |
| I7 | Dual MemoryReader | Task 7 | Single reader at core/memory/reader.py |
| I8 | RoutineStore no index | Task 9 | In-memory cache, invalidate on write |

## Parallelism Guide

Tasks that touch separate files can be run by parallel subagents:

| Parallel Group | Tasks | Reason |
|---------------|-------|--------|
| Group 1 | 1, 2, 3, 4 | All touch different files, no overlap |
| Group 2 | 5, then 6 | Both modify consolidator.py (sequential) |
| Group 3 | 7, then 8 | Both modify MemoryReader (sequential) |
| Group 4 | 9, 10, 11 | All touch different files, no overlap. **Note:** Task 11 modifies `core/conscious/__main__.py` which Task 3 also touches — ensure Group 1 completes before starting Group 4 |
| Final | 12 | Depends on all above |
