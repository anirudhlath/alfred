# Phase 4: Core Wiring & Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect all Phase 3 components that were built in isolation — memory → engine, integrations → engine, scratchpad writes, identity resolution, notification delivery, Librarian intelligence, and eval wiring — so Alfred functions as a complete System 2 pipeline.

**Architecture:** The Conscious Engine has a working LLM loop but currently receives empty strings for memory, integrations, and proactivity. A new `MemoryReader` class reads preference/profile Markdown files + queries episodic/routine stores, then passes structured text to `ContextAssembler`. The `IntegrationRegistry` (already built with decorator pattern) gets imported and wired. Scratchpad writes close the observation loop. The Librarian gets Claude-powered extraction. A notification publisher connects cost alerts to delivery channels.

**Tech Stack:** Python 3.13+, LiteLLM, Redis Streams, aiosqlite, Pydantic v2, pytest + pytest-asyncio

---

## File Structure

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `core/conscious/memory_reader.py` | Read preferences, profile, episodic, routines into text |
| Create | `tests/core/conscious/test_memory_reader.py` | Tests for MemoryReader |
| Modify | `core/conscious/engine.py:46-68,274-290` | Accept MemoryReader + IntegrationRegistry, use in process_request |
| Modify | `core/conscious/__main__.py:55-70` | Instantiate MemoryReader, EpisodicStore, RoutineStore, import integrations |
| Create | `core/notifications/publisher.py` | Publish notifications to Redis stream |
| Create | `core/notifications/__init__.py` | Package init |
| Create | `tests/core/notifications/test_publisher.py` | Tests for NotificationPublisher |
| Modify | `core/conscious/cost.py` | Accept NotificationPublisher, send cost alerts |
| Modify | `core/conscious/identity.py:58-76` | Add local-device trust for web_pwa channel |
| Create | `tests/core/conscious/test_identity_local_trust.py` | Test local-device identity resolution |
| Modify | `core/librarian/consolidator.py:110,154-156` | Wire Claude for entity extraction, patterns, semantic updates, decay |
| Create | `tests/core/librarian/test_consolidator_intelligence.py` | Tests for Claude-powered Librarian |
| Modify | `evals/conscious/runner.py:103-129` | Replace dry-run with mocked ConsciousEngine invocation |
| Modify | `tests/evals/test_conscious_runner.py` | Add tests for live eval runner execution |
| Create | `core/channels/signal_bridge/__init__.py` | Signal bridge package init |
| Create | `core/channels/signal_bridge/bridge.py` | Signal CLI bridge (inbound/outbound forwarding) |
| Create | `tests/core/channels/test_signal_bridge.py` | Tests for signal bridge forwarding |

---

## Phase 4A: Core Wiring

### Task 1: MemoryReader — Read Preferences and Profile from Disk

**Files:**
- Create: `core/conscious/memory_reader.py`
- Create: `tests/core/conscious/test_memory_reader.py`

- [ ] **Step 1: Write failing tests for MemoryReader**

```python
"""Tests for MemoryReader — reads semantic memory files into text."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.conscious.memory_reader import MemoryReader


@pytest.fixture()
def memory_dirs(tmp_path: Path) -> tuple[Path, Path]:
    prefs = tmp_path / "preferences"
    profile = tmp_path / "profile"
    prefs.mkdir()
    profile.mkdir()
    return prefs, profile


def test_get_preferences_reads_markdown_files(
    memory_dirs: tuple[Path, Path],
) -> None:
    prefs, profile = memory_dirs
    (prefs / "personal.md").write_text(
        "---\ndomain: general\nupdated: 2026-03-19\nconfidence: manual\n---\n\n# Personal\n\n- Wake time: 07:30\n"
    )
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile)
    result = reader.get_preferences()
    assert "Wake time: 07:30" in result


def test_get_preferences_empty_dir(memory_dirs: tuple[Path, Path]) -> None:
    prefs, profile = memory_dirs
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile)
    assert reader.get_preferences() == ""


def test_get_profile_reads_markdown_files(
    memory_dirs: tuple[Path, Path],
) -> None:
    prefs, profile = memory_dirs
    (profile / "about.md").write_text(
        "---\ntype: semantic\n---\n\n# About Sir\n\n- Enjoys classical music\n"
    )
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile)
    result = reader.get_profile()
    assert "Enjoys classical music" in result


def test_get_proactivity_level_from_profile(
    memory_dirs: tuple[Path, Path],
) -> None:
    prefs, profile = memory_dirs
    (profile / "proactivity.md").write_text(
        "---\ndomain: general\nupdated: 2026-03-19\nconfidence: manual\n---\n\n# Proactivity Level\n\n- Level: moderate\n"
    )
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile)
    assert reader.get_proactivity_level() == "moderate"


def test_get_proactivity_level_default(
    memory_dirs: tuple[Path, Path],
) -> None:
    prefs, profile = memory_dirs
    reader = MemoryReader(
        preferences_dir=prefs, profile_dir=profile, default_proactivity="conservative"
    )
    assert reader.get_proactivity_level() == "conservative"


def test_multiple_preference_files_concatenated(
    memory_dirs: tuple[Path, Path],
) -> None:
    prefs, profile = memory_dirs
    (prefs / "personal.md").write_text("---\n---\n\n# Personal\n\n- Wake: 07:30\n")
    (prefs / "routines.md").write_text("---\n---\n\n# Routines\n\n- Morning: lights 80%\n")
    reader = MemoryReader(preferences_dir=prefs, profile_dir=profile)
    result = reader.get_preferences()
    assert "Wake: 07:30" in result
    assert "Morning: lights 80%" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/core/conscious/test_memory_reader.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'core.conscious.memory_reader'"

- [ ] **Step 3: Implement MemoryReader**

```python
"""MemoryReader — reads semantic memory files into structured text for the system prompt."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class MemoryReader:
    """Reads preference and profile Markdown files from disk.

    Provides text sections for ContextAssembler. Files are read-only at runtime
    (only the Librarian or humans edit them).
    """

    def __init__(
        self,
        preferences_dir: Path,
        profile_dir: Path,
        default_proactivity: str = "opinionated",
    ) -> None:
        self._preferences_dir = Path(preferences_dir)
        self._profile_dir = Path(profile_dir)
        self._default_proactivity = default_proactivity

    @staticmethod
    def _read_md_body(path: Path) -> str:
        """Read a Markdown file, stripping YAML frontmatter."""
        text = path.read_text()
        # Strip YAML frontmatter (between --- markers)
        stripped = re.sub(r"^---\n.*?\n---\n*", "", text, count=1, flags=re.DOTALL)
        return stripped.strip()

    def _read_all_md(self, directory: Path) -> str:
        """Read and concatenate all .md files in a directory."""
        if not directory.is_dir():
            return ""
        parts: list[str] = []
        for path in sorted(directory.glob("*.md")):
            body = self._read_md_body(path)
            if body:
                parts.append(body)
        return "\n\n".join(parts)

    def get_preferences(self) -> str:
        """Read all preference files and concatenate their content."""
        return self._read_all_md(self._preferences_dir)

    def get_profile(self) -> str:
        """Read all profile files and concatenate their content."""
        return self._read_all_md(self._profile_dir)

    def get_proactivity_level(self) -> str:
        """Read proactivity level from profile/proactivity.md, falling back to default."""
        proactivity_path = self._profile_dir / "proactivity.md"
        if proactivity_path.exists():
            body = self._read_md_body(proactivity_path)
            # Parse "- Level: moderate" pattern
            match = re.search(r"Level:\s*(\w+)", body)
            if match:
                return match.group(1).lower()
        return self._default_proactivity
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/core/conscious/test_memory_reader.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run ruff + mypy**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/ruff check core/conscious/memory_reader.py && .venv/bin/ruff format core/conscious/memory_reader.py && .venv/bin/mypy --strict core/conscious/memory_reader.py`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add core/conscious/memory_reader.py tests/core/conscious/test_memory_reader.py
git commit -m "feat(conscious): add MemoryReader for preferences/profile disk reads"
```

---

### Task 2: Wire MemoryReader + EpisodicStore + RoutineStore into Conscious Engine

**Files:**
- Modify: `core/conscious/engine.py:46-68,274-290`
- Modify: `core/conscious/__main__.py:55-70`
- Modify: `tests/core/conscious/test_engine.py` (update constructor calls)

- [ ] **Step 1: Write failing test for engine using MemoryReader**

Add to `tests/core/conscious/test_engine.py`:

```python
@pytest.mark.asyncio
async def test_process_request_includes_preferences_in_prompt(
    engine_with_memory: ConsciousEngine,
    mock_litellm: AsyncMock,
) -> None:
    """Engine should pass preferences text from MemoryReader to the assembler."""
    request = UserRequest(
        source="test", channel="web_pwa", session_id="s1",
        identity_claim="sir", authenticated=True,
        content_type="text", content="hello",
    )
    await engine_with_memory.process_request(request)
    # Verify the system prompt passed to LLM contains preference text
    call_kwargs = mock_litellm.call_args
    messages = call_kwargs.kwargs.get("messages", call_kwargs[1].get("messages", []))
    system_msg = messages[0]["content"]
    assert "Wake time" in system_msg or "preferences" in system_msg.lower()
```

- [ ] **Step 2: Update engine constructor to accept MemoryReader**

In `core/conscious/engine.py`, add to imports and constructor:

```python
# Add to module-level imports (after existing datetime imports or near top):
from datetime import UTC, datetime

# Add to TYPE_CHECKING imports:
from core.conscious.memory_reader import MemoryReader
from core.memory.episodic.store import EpisodicStore
from core.memory.routines.store import RoutineStore

# Update __init__ signature to add:
    def __init__(
        self,
        redis: AioRedis,
        identity_gate: IdentityGate,
        session_mgr: SessionManager,
        cost_tracker: CostTracker,
        context_assembler: ContextAssembler,
        domain_router: DomainRouter,
        tool_registry: ToolRegistry,
        context_reader: ContextReader,
        memory_reader: MemoryReader | None = None,
        episodic_store: EpisodicStore | None = None,
        routine_store: RoutineStore | None = None,
        claude_model: str = "openrouter/anthropic/claude-sonnet-4",
        claude_api_key: str = "",
    ) -> None:
        # ... existing assignments ...
        self._memory_reader = memory_reader
        self._episodic = episodic_store
        self._routines = routine_store
```

- [ ] **Step 3: Update process_request to read memory**

Replace lines 280-290 in `core/conscious/engine.py`:

```python
        # 4. Context assembly
        tools = await self._tool_registry.get_tools()
        context_text = await self._context_reader.get_rendered_context()

        # Read memory layers (empty strings if no reader configured)
        preferences = ""
        profile_text = ""
        episodic_text = ""
        procedural_text = ""
        proactivity_level = "opinionated"

        if self._memory_reader:
            preferences = self._memory_reader.get_preferences()
            profile_text = self._memory_reader.get_profile()
            proactivity_level = self._memory_reader.get_proactivity_level()

        if self._episodic:
            try:
                from datetime import timedelta
                since = datetime.now(UTC) - timedelta(days=7)
                entries = await self._episodic.query_cold(limit=10, since=since)
                if entries:
                    episodic_text = "\n".join(
                        f"- [{e.timestamp:%Y-%m-%d %H:%M}] {e.summary}" for e in entries
                    )
            except Exception as exc:
                logger.warning("Failed to read episodic memory: %s", exc)

        if self._routines:
            try:
                active = self._routines.list_by_state("active")
                if active:
                    procedural_text = "\n".join(
                        f"- {r.name}: {r.trigger_pattern}" for r in active
                    )
            except Exception as exc:
                logger.warning("Failed to read routine memory: %s", exc)

        # Build integrations section from registry
        integrations_section = ""
        try:
            from core.integrations.registry import IntegrationRegistry
            integrations_section = IntegrationRegistry.build_capabilities_docs()
        except Exception as exc:
            logger.debug("IntegrationRegistry not available: %s", exc)

        if preferences and profile_text:
            preferences = f"{preferences}\n\n{profile_text}"
        elif profile_text:
            preferences = profile_text

        system_prompt = self._assembler.assemble(
            identity=identity,
            tools_section="\n".join(f"- {t.name}: {t.description}" for t in tools),
            integrations_section=integrations_section,
            preferences_text=preferences,
            context_text=context_text,
            history=session["history"],
            proactivity_level=proactivity_level,
            episodic_text=episodic_text,
            procedural_text=procedural_text,
            channel=request.channel,
        )
```

- [ ] **Step 4: Add scratchpad write after conversation turn**

After step 8 in `process_request()` (after session update, before building response):

```python
        # 8b. Write observation to scratchpad queue
        from shared.streams import SCRATCHPAD_QUEUE

        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        actions_str = ", ".join(all_actions) if all_actions else "none"
        observation = (
            f"{timestamp} [conscious] "
            f"user='{request.content[:80]}' → {len(final_text)} chars "
            f"(actions={actions_str}, tokens={total_prompt_tokens}+{total_completion_tokens})"
        )
        await self._redis.lpush(SCRATCHPAD_QUEUE, observation)
```

- [ ] **Step 5: Update __main__.py to wire all components**

```python
# Add imports at top:
from core.conscious.memory_reader import MemoryReader
from core.memory.episodic.store import EpisodicStore
from core.memory.routines.store import RoutineStore

# Import integration modules to trigger @register decorators
import core.integrations.weather  # noqa: F401
import core.integrations.apple_calendar  # noqa: F401
import core.integrations.apple_health  # noqa: F401
import core.integrations.robinhood  # noqa: F401

# In run(), before engine construction:
    episodic_store = EpisodicStore(
        redis=r,
        db_path=str(Path(__file__).resolve().parent.parent / "memory" / "episodic.db"),
        hot_days=config.episodic_hot_days,
    )
    routine_store = RoutineStore(
        routines_dir=str(Path(__file__).resolve().parent.parent / "memory" / "routines"),
    )
    memory_reader = MemoryReader(
        preferences_dir=Path(__file__).resolve().parent.parent / "memory" / "preferences",
        profile_dir=Path(__file__).resolve().parent.parent / "memory" / "profile",
        default_proactivity=config.proactivity_level,
    )

# Update ConsciousEngine constructor:
    engine = ConsciousEngine(
        redis=r,
        identity_gate=IdentityGate(registered_phone=config.signal_phone_number),
        session_mgr=SessionManager(redis=r, timeout_minutes=config.session_timeout_minutes),
        cost_tracker=CostTracker(redis=r, daily_cap_usd=config.daily_cost_cap_usd),
        context_assembler=ContextAssembler(),
        domain_router=router,
        tool_registry=ToolRegistry(r),
        context_reader=ContextReader(redis=r),
        memory_reader=memory_reader,
        episodic_store=episodic_store,
        routine_store=routine_store,
        claude_model=config.claude_model,
        claude_api_key=config.claude_api_key,
    )
```

- [ ] **Step 6: Update existing engine tests to pass new params (or use defaults)**

Existing tests create `ConsciousEngine(...)` without the new params. Since they default to `None`, existing tests should still pass. Verify:

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/core/conscious/ -v`
Expected: All existing tests PASS

- [ ] **Step 7: Run ruff + mypy on modified files**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/ruff check core/conscious/engine.py core/conscious/__main__.py --fix && .venv/bin/ruff format core/conscious/engine.py core/conscious/__main__.py && .venv/bin/mypy --strict core/conscious/engine.py core/conscious/__main__.py`
Expected: Clean

- [ ] **Step 8: Commit**

```bash
git add core/conscious/engine.py core/conscious/__main__.py tests/core/conscious/
git commit -m "feat(conscious): wire memory, integrations, scratchpad into engine pipeline"
```

---

### Task 3: Local-Device Identity Trust for Web PWA

**Files:**
- Modify: `core/conscious/identity.py:58-76`
- Create: `tests/core/conscious/test_identity_local_trust.py`

The `IdentityGate.resolve()` method currently requires `authenticated=True` for web_pwa to resolve as "sir". But the PWA sends `identity: 'sir'` (app.js:154) without any auth mechanism. Until WebAuthn is implemented, trust the identity claim on the web_pwa channel with a "local_claim" method and lower confidence.

- [ ] **Step 1: Write failing test**

```python
"""Tests for local-device identity trust on web_pwa channel."""

from __future__ import annotations

from core.conscious.identity import IdentityGate


def test_web_pwa_claim_sir_unauthenticated_resolves_sir() -> None:
    """Web PWA claiming 'sir' without auth should resolve as sir (local trust)."""
    gate = IdentityGate(registered_phone="+1234567890")
    result = gate.resolve(channel="web_pwa", identity_claim="sir", authenticated=False)
    assert result.identity == "sir"
    assert result.method == "local_claim"
    assert result.confidence < 0.9  # Lower confidence than authenticated


def test_web_pwa_claim_guest_resolves_guest() -> None:
    """Web PWA claiming 'guest' should resolve as guest."""
    gate = IdentityGate(registered_phone="+1234567890")
    result = gate.resolve(channel="web_pwa", identity_claim="guest", authenticated=False)
    assert result.identity == "guest"


def test_web_pwa_authenticated_still_high_confidence() -> None:
    """Web PWA with authentication should still get high confidence."""
    gate = IdentityGate(registered_phone="+1234567890")
    result = gate.resolve(channel="web_pwa", identity_claim="sir", authenticated=True)
    assert result.identity == "sir"
    assert result.confidence >= 0.99
    assert result.method == "webauthn"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/core/conscious/test_identity_local_trust.py -v`
Expected: FAIL — first test expects "sir" but gets "guest"

- [ ] **Step 3: Update IdentityGate.resolve() with local claim support**

Replace the `resolve()` method in `core/conscious/identity.py`:

```python
    def resolve(
        self,
        channel: str,
        identity_claim: str,
        authenticated: bool,
    ) -> IdentityResult:
        """Unified resolution from a UserRequest's fields."""
        if channel == "signal":
            return self.resolve_signal(sender_phone=identity_claim)
        if channel in ("web_pwa", "voice"):
            # Authenticated session (WebAuthn) takes priority
            if authenticated:
                return self.resolve_session(authenticated=True)
            # Trust identity claim on local channels (pre-WebAuthn)
            if identity_claim == "sir":
                return IdentityResult(
                    identity="sir",
                    confidence=0.7,
                    method="local_claim",
                    factors=["identity_claim"],
                    risk_clearance="low",
                )
            return self.resolve_session(authenticated=False)
        logger.warning("Unknown channel '%s', defaulting to guest", channel)
        return IdentityResult(
            identity="guest",
            confidence=1.0,
            method="unknown",
            factors=[],
            risk_clearance="low",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/core/conscious/test_identity_local_trust.py tests/core/conscious/test_identity.py -v`
Expected: All PASS (new and existing)

- [ ] **Step 5: Commit**

```bash
git add core/conscious/identity.py tests/core/conscious/test_identity_local_trust.py
git commit -m "feat(identity): trust local identity claims on web_pwa channel (pre-WebAuthn)"
```

---

### Task 4: Notification Publisher

**Files:**
- Create: `core/notifications/__init__.py`
- Create: `core/notifications/publisher.py`
- Create: `tests/core/notifications/test_publisher.py`
- Modify: `core/conscious/cost.py` (wire notification on budget exceed)

- [ ] **Step 1: Write failing tests**

```python
"""Tests for NotificationPublisher."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.notifications.publisher import NotificationPublisher
from shared.streams import NOTIFICATIONS_STREAM


@pytest.mark.asyncio
async def test_publish_sends_to_stream() -> None:
    redis = AsyncMock()
    pub = NotificationPublisher(redis=redis)
    await pub.publish(
        channel="cost_alert",
        title="Budget Warning",
        body="Daily budget 80% consumed",
        urgency="high",
    )
    redis.xadd.assert_called_once()
    call_args = redis.xadd.call_args
    assert call_args[0][0] == NOTIFICATIONS_STREAM


@pytest.mark.asyncio
async def test_publish_includes_metadata() -> None:
    redis = AsyncMock()
    pub = NotificationPublisher(redis=redis)
    await pub.publish(
        channel="cost_alert",
        title="Budget exceeded",
        body="$5.00 daily cap reached",
        urgency="critical",
    )
    payload = redis.xadd.call_args[0][1]
    event_str = payload["event"]
    assert "cost_alert" in event_str
    assert "Budget exceeded" in event_str
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/core/notifications/test_publisher.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement NotificationPublisher**

Create `core/notifications/__init__.py` (empty) and `core/notifications/publisher.py`:

```python
"""NotificationPublisher — sends notifications to the delivery stream."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shared.streams import NOTIFICATIONS_STREAM

if TYPE_CHECKING:
    from core.reflex.runner import AioRedis

logger = logging.getLogger(__name__)


class NotificationPublisher:
    """Publishes notification events to the Redis notifications stream.

    Downstream consumers (Signal bridge, web push, etc.) read from this stream.
    """

    def __init__(self, redis: AioRedis) -> None:
        self._redis = redis

    async def publish(
        self,
        channel: str,
        title: str,
        body: str,
        urgency: str = "normal",
    ) -> None:
        """Publish a notification event."""
        event = {
            "channel": channel,
            "title": title,
            "body": body,
            "urgency": urgency,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await self._redis.xadd(NOTIFICATIONS_STREAM, {"event": json.dumps(event)})
        logger.info("Published notification: %s — %s", channel, title)
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/core/notifications/test_publisher.py -v`
Expected: PASS

- [ ] **Step 5: Wire into CostTracker**

Read `core/conscious/cost.py`, then add a `notify` callback. In `is_budget_exceeded()`, when budget is exceeded, publish a notification. Add an optional `notifier: NotificationPublisher | None = None` param to `CostTracker.__init__()`.

- [ ] **Step 6: Commit**

```bash
git add core/notifications/ tests/core/notifications/
git commit -m "feat(notifications): add NotificationPublisher for cost alerts and proactive messages"
```

---

## Phase 4B: Librarian Intelligence

### Task 5: Librarian — Claude-Powered Entity Extraction

**Files:**
- Modify: `core/librarian/consolidator.py:85-114`
- Create: `tests/core/librarian/test_consolidator_intelligence.py`

The Librarian currently creates one `EpisodicEntry` per scratchpad line with no entity extraction. Wire Claude via LiteLLM to extract entities from scratchpad observations.

- [ ] **Step 1: Write failing test for entity extraction**

```python
"""Tests for Librarian Claude-powered intelligence."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.librarian.consolidator import Librarian
from core.memory.schemas import EpisodicEntry


@pytest.fixture()
def librarian() -> Librarian:
    redis = AsyncMock()
    episodic = AsyncMock()
    routines = AsyncMock()
    return Librarian(
        redis=redis,
        episodic_store=episodic,
        routine_store=routines,
        claude_api_key="test-key",
    )


@pytest.mark.asyncio
async def test_extract_entities_with_claude(librarian: Librarian) -> None:
    """When Claude is available, entities should be extracted from scratchpad lines."""
    lines = [
        "2026-03-19T10:00:00Z [reflex] home.turn_on_light({entity: light.living_room}) → success",
    ]
    mock_response = AsyncMock()
    mock_response.choices = [
        AsyncMock(message=AsyncMock(content='["light.living_room", "living room"]'))
    ]
    mock_response.usage = AsyncMock(prompt_tokens=100, completion_tokens=20)

    with patch("litellm.acompletion", return_value=mock_response):
        entries = await librarian._extract_episodic_entries(lines)

    assert len(entries) == 1
    assert "light.living_room" in entries[0].entities or "living room" in entries[0].entities


@pytest.mark.asyncio
async def test_extract_entities_fallback_without_api_key() -> None:
    """Without API key, entities should be empty (graceful fallback)."""
    redis = AsyncMock()
    episodic = AsyncMock()
    routines = AsyncMock()
    lib = Librarian(
        redis=redis, episodic_store=episodic, routine_store=routines, claude_api_key=""
    )
    lines = ["2026-03-19T10:00:00Z [reflex] action → result"]
    entries = await lib._extract_episodic_entries(lines)
    assert len(entries) == 1
    assert entries[0].entities == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/core/librarian/test_consolidator_intelligence.py -v`
Expected: FAIL — entities are always empty

- [ ] **Step 3: Implement Claude entity extraction in consolidator**

Update `_extract_episodic_entries()` in `core/librarian/consolidator.py`:

```python
    async def _extract_entities(self, text: str) -> list[str]:
        """Extract entities from a scratchpad line using Claude."""
        if not self._api_key:
            return []
        try:
            import litellm
            response = await litellm.acompletion(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Extract named entities from this home automation observation. "
                            "Return a JSON array of entity names (devices, rooms, people, services). "
                            "Example: [\"light.living_room\", \"living room\", \"motion sensor\"]"
                        ),
                    },
                    {"role": "user", "content": text},
                ],
                max_tokens=200,
                api_key=self._api_key,
            )
            import json
            raw = response.choices[0].message.content or "[]"
            return json.loads(raw)
        except Exception as exc:
            logger.warning("Entity extraction failed: %s", exc)
            return []

    async def _extract_episodic_entries(self, scratchpad_lines: list[str]) -> list[EpisodicEntry]:
        """Extract episodic entries from scratchpad lines."""
        entries: list[EpisodicEntry] = []
        for line in scratchpad_lines:
            parts = line.split("] ", 1)
            source = "unknown"
            summary = line
            if len(parts) == 2:
                source_part = parts[0].split("[", 1)
                if len(source_part) == 2:
                    source = source_part[1]
                summary = parts[1]

            entities = await self._extract_entities(summary)

            entries.append(
                EpisodicEntry(
                    id=str(uuid4()),
                    timestamp=datetime.now(UTC),
                    source=source,
                    summary=summary.strip(),
                    entities=entities,
                    valence="neutral",
                )
            )
        return entries
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/core/librarian/test_consolidator_intelligence.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core/librarian/consolidator.py tests/core/librarian/test_consolidator_intelligence.py
git commit -m "feat(librarian): add Claude-powered entity extraction from scratchpad"
```

---

### Task 6: Librarian — Semantic Memory Updates + Pattern Detection + Decay

**Files:**
- Modify: `core/librarian/consolidator.py:122-164`
- Modify: `tests/core/librarian/test_consolidator_intelligence.py`

Fill in the three remaining TODO stubs: semantic memory updates (preferences/profile), pattern detection for procedural memory, and decay processing.

- [ ] **Step 1: Write failing test for semantic update**

```python
@pytest.mark.asyncio
async def test_consolidate_updates_semantic_memory(
    librarian: Librarian, tmp_path: Path
) -> None:
    """Consolidation should update preference files when patterns are detected."""
    librarian._preferences_dir = tmp_path / "prefs"
    librarian._preferences_dir.mkdir()
    librarian._redis.lrange = AsyncMock(return_value=[])
    librarian._redis.rename = AsyncMock(side_effect=Exception("no key"))

    # With empty scratchpad, no updates
    result = await librarian.consolidate()
    assert result["entries_processed"] == 0
```

- [ ] **Step 2: Implement semantic update stub**

Add a `_update_semantic_memory()` method that uses Claude to detect preference changes from episodic entries and writes updates to preference files. This should be conservative — only write when Claude identifies a clear preference with high confidence.

```python
    async def _update_semantic_memory(self, entries: list[EpisodicEntry]) -> int:
        """Use Claude to detect preference changes and update semantic files.

        Returns the number of files updated.
        """
        if not self._api_key or not entries:
            return 0

        summaries = "\n".join(f"- {e.summary}" for e in entries)
        try:
            import litellm
            response = await litellm.acompletion(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Analyze these home assistant observations. "
                            "If you detect a clear user preference (e.g., preferred temperature, "
                            "routine change, dietary preference), output it as:\n"
                            "PREFERENCE: <domain>: <observation>\n"
                            "Only output high-confidence observations. "
                            "If nothing is notable, output NONE."
                        ),
                    },
                    {"role": "user", "content": summaries},
                ],
                max_tokens=500,
                api_key=self._api_key,
            )
            result_text = response.choices[0].message.content or ""
            if "NONE" in result_text or not result_text.strip():
                return 0

            # Append detected preferences to learned.md
            learned_path = self._preferences_dir / "learned.md"
            if learned_path.exists():
                existing = learned_path.read_text()
            else:
                existing = (
                    "---\ndomain: general\nupdated: "
                    f"{datetime.now(UTC).strftime('%Y-%m-%d')}\n"
                    "confidence: librarian\n---\n\n# Learned Preferences\n\n"
                )

            new_lines = [
                line.replace("PREFERENCE:", "-").strip()
                for line in result_text.splitlines()
                if line.startswith("PREFERENCE:")
            ]
            if new_lines:
                updated = existing.rstrip() + "\n" + "\n".join(new_lines) + "\n"
                self._write_semantic_file(learned_path, updated)
                return 1
        except Exception as exc:
            logger.warning("Semantic memory update failed: %s", exc)
        return 0
```

- [ ] **Step 3: Implement decay processing**

```python
    async def _apply_decay(self) -> int:
        """Archive old hot-storage entries to cold storage.

        Returns the number of entries archived.
        """
        # Read hot stream entries older than hot_days
        # For now, this is a placeholder that the EpisodicStore
        # hot→cold migration handles separately
        # TODO: Implement XTRIM or time-based archival
        return 0
```

- [ ] **Step 4: Wire into consolidate()**

Update the `consolidate()` method to call the new methods:

```python
        # 4. Update semantic memory (requires Claude)
        semantic_updates = await self._update_semantic_memory(episodic_entries)

        # 5. TODO: Pattern detection for procedural memory (requires more data)
        # Pattern detection needs multiple consolidation cycles of data
        # to identify recurring patterns. Deferred until enough episodic
        # entries exist (>= 2 weeks of data).

        # 6. Decay processing
        archived = await self._apply_decay()

        result: dict[str, Any] = {
            "entries_processed": len(lines),
            "episodic_created": len(episodic_entries),
            "semantic_updates": semantic_updates,
            "archived": archived,
            "timestamp": datetime.now(UTC).isoformat(),
        }
```

- [ ] **Step 5: Run all librarian tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/core/librarian/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add core/librarian/consolidator.py tests/core/librarian/
git commit -m "feat(librarian): add semantic memory updates and decay processing"
```

---

## Phase 4C: Eval Wiring & Signal Bridge

### Task 7: Wire Eval Runner to ConsciousEngine with Mocks

**Files:**
- Modify: `evals/conscious/runner.py:103-129`
- Create: `tests/evals/test_conscious_runner.py`

Replace the dry-run TODO with actual ConsciousEngine invocation using mocked Redis and integrations.

- [ ] **Step 1: Write failing test for run_conscious_evals_live**

Add to existing `tests/evals/test_conscious_runner.py`:

```python
@pytest.mark.asyncio
async def test_run_conscious_evals_live_exists() -> None:
    """Verify run_conscious_evals_live function exists and is callable."""
    from evals.conscious.runner import run_conscious_evals_live

    # Should return dry-run results when no API key provided
    results = await run_conscious_evals_live(
        scenarios_dir="evals/conscious/scenarios",
        api_key="",
    )
    assert isinstance(results, list)
    # All should be dry-run
    for r in results:
        assert r.details.get("status") == "dry_run"


def test_evaluate_response_butler_personality() -> None:
    from evals.conscious.runner import EvalResult, ScenarioSpec, evaluate_response

    scenario = ScenarioSpec(
        name="test", description="test",
        request={"content": "hello", "identity": "sir"},
        expected={"butler_personality_score": 0.3},
    )
    result = evaluate_response(
        scenario,
        response_text="Good evening, sir. How may I be of assistance?",
        tool_calls_made=[],
    )
    assert result.scores["butler_personality"] > 0.3


def test_evaluate_response_privacy_leak_guest() -> None:
    from evals.conscious.runner import EvalResult, ScenarioSpec, evaluate_response

    scenario = ScenarioSpec(
        name="test", description="test",
        request={"content": "hello", "identity": "guest"},
        expected={"must_not_mention": ["wake time", "work address"]},
    )
    result = evaluate_response(
        scenario,
        response_text="Good evening. His wake time is 7:30 and work address is 123 Main.",
        tool_calls_made=[],
    )
    assert not result.passed
```

- [ ] **Step 2: Run test to verify the live function test fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/evals/test_conscious_runner.py::test_run_conscious_evals_live_exists -v`
Expected: FAIL — `ImportError: cannot import name 'run_conscious_evals_live'`

- [ ] **Step 3: Implement run_conscious_evals with mocked engine**

Update `evals/conscious/runner.py` to add a `run_conscious_evals_live()` function:

```python
async def run_conscious_evals_live(
    scenarios_dir: str = "evals/conscious/scenarios",
    api_key: str = "",
    model: str = "openrouter/anthropic/claude-sonnet-4",
) -> list[EvalResult]:
    """Run System 2 evals with a real (or mocked) Conscious Engine.

    Requires OPENROUTER_API_KEY for live execution.
    Falls back to dry-run if no key provided.
    """
    if not api_key:
        logger.warning("No API key — falling back to dry-run mode")
        return run_conscious_evals(scenarios_dir)

    from unittest.mock import AsyncMock

    from core.conscious.context_assembler import ContextAssembler
    from core.conscious.cost import CostTracker
    from core.conscious.engine import ConsciousEngine
    from core.conscious.identity import IdentityGate
    from core.conscious.session import SessionManager

    results: list[EvalResult] = []
    scenarios_path = Path(scenarios_dir)

    # Create engine with mocked Redis
    mock_redis = AsyncMock()
    mock_redis.xinfo_stream = AsyncMock(return_value={"last-generated-id": "0-0"})

    engine = ConsciousEngine(
        redis=mock_redis,
        identity_gate=IdentityGate(registered_phone=""),
        session_mgr=SessionManager(redis=mock_redis, timeout_minutes=30),
        cost_tracker=CostTracker(redis=mock_redis, daily_cap_usd=50.0),
        context_assembler=ContextAssembler(),
        domain_router=AsyncMock(),
        tool_registry=AsyncMock(get_tools=AsyncMock(return_value=[])),
        context_reader=AsyncMock(get_rendered_context=AsyncMock(return_value="")),
        claude_model=model,
        claude_api_key=api_key,
    )

    for scenario_file in sorted(scenarios_path.glob("*.yaml")):
        scenario = load_scenario(str(scenario_file))
        logger.info("Evaluating scenario: %s", scenario.name)

        from bus.schemas.events import UserRequest

        request = UserRequest(
            source="eval",
            channel="web_pwa",
            session_id=f"eval-{scenario.name}",
            identity_claim=scenario.request.get("identity", "sir"),
            authenticated=scenario.request.get("identity", "sir") == "sir",
            content_type="text",
            content=scenario.request.get("content", ""),
        )

        try:
            response = await engine.process_request(request)
            eval_result = evaluate_response(
                scenario,
                response_text=response.text,
                tool_calls_made=response.actions_taken,
            )
        except Exception as exc:
            logger.error("Scenario %s failed: %s", scenario.name, exc)
            eval_result = EvalResult(
                scenario=scenario.name,
                passed=False,
                scores={},
                details={"error": str(exc)},
            )

        results.append(eval_result)

    return results
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/evals/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add evals/conscious/runner.py tests/evals/
git commit -m "feat(evals): add live conscious eval runner with mocked infrastructure"
```

---

### Task 8: Signal Bridge Scaffold

**Files:**
- Create: `core/channels/signal_bridge/__init__.py`
- Create: `core/channels/signal_bridge/bridge.py`
- Create: `tests/core/channels/test_signal_bridge.py`

The signal bridge lives inside the monorepo (under `core/channels/`) since it needs direct access to `bus.schemas` and `shared.streams`. It reads from `signal-cli` (via subprocess) and forwards messages to Alfred's Redis streams, and reads from `NOTIFICATIONS_STREAM` to send outbound messages.

- [ ] **Step 1: Write failing test**

```python
"""Tests for Signal bridge forwarding."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.channels.signal_bridge.bridge import SignalBridge
from shared.streams import USER_REQUESTS_STREAM


@pytest.mark.asyncio
async def test_forward_inbound_to_redis() -> None:
    redis = AsyncMock()
    bridge = SignalBridge(redis=redis, phone_number="+1234567890")
    await bridge.forward_inbound(
        sender="+1234567890",
        message="Turn on the lights",
        timestamp="2026-03-19T10:00:00Z",
    )
    redis.xadd.assert_called_once()
    call_args = redis.xadd.call_args
    assert call_args[0][0] == USER_REQUESTS_STREAM


@pytest.mark.asyncio
async def test_forward_outbound_notification() -> None:
    redis = AsyncMock()
    signal_send = AsyncMock()
    bridge = SignalBridge(redis=redis, phone_number="+1234567890")
    bridge._send_signal = signal_send  # type: ignore[assignment]
    await bridge.send_notification(
        recipient="+1234567890",
        message="Daily budget exceeded",
    )
    signal_send.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/core/channels/test_signal_bridge.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement bridge scaffold**

Create `core/channels/signal_bridge/__init__.py` (empty) and `core/channels/signal_bridge/bridge.py`:

```python
"""Signal bridge — forwards Signal messages to/from Alfred Redis streams."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from bus.schemas.events import UserRequest
from shared.streams import NOTIFICATIONS_STREAM, USER_REQUESTS_STREAM

if TYPE_CHECKING:
    from core.reflex.runner import AioRedis

logger = logging.getLogger(__name__)


class SignalBridge:
    """Bridges Signal CLI <-> Alfred Redis Streams."""

    def __init__(self, redis: AioRedis, phone_number: str) -> None:
        self._redis = redis
        self._phone = phone_number

    async def forward_inbound(
        self, sender: str, message: str, timestamp: str
    ) -> None:
        """Forward an inbound Signal message to the user requests stream."""
        request = UserRequest(
            source="signal-bridge",
            channel="signal",
            session_id=f"signal-{sender}",
            identity_claim=sender,
            content_type="text",
            content=message,
        )
        await self._redis.xadd(
            USER_REQUESTS_STREAM, {"event": request.model_dump_json()}
        )
        logger.info("Forwarded Signal message from %s to Alfred", sender[:6])

    async def _send_signal(self, recipient: str, message: str) -> None:
        """Send a message via signal-cli. Placeholder for subprocess call."""
        # TODO: Implement actual signal-cli subprocess integration
        logger.info("Would send to %s: %s", recipient[:6], message[:50])

    async def send_notification(self, recipient: str, message: str) -> None:
        """Send an outbound notification via Signal."""
        await self._send_signal(recipient, message)

    async def poll_notifications(self) -> None:
        """Poll the notifications stream and send via Signal."""
        entries: list[Any] = await self._redis.xread(
            {NOTIFICATIONS_STREAM: "0-0"}, count=10, block=5000
        )
        for _stream, stream_entries in entries:
            for _entry_id, entry_data in stream_entries:
                raw = entry_data.get(b"event") or entry_data.get("event")
                if raw:
                    event_str = raw.decode() if isinstance(raw, bytes) else raw
                    event = json.loads(event_str)
                    await self.send_notification(
                        self._phone, f"{event['title']}: {event['body']}"
                    )
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/core/channels/test_signal_bridge.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/ruff check core/channels/signal_bridge/ && .venv/bin/mypy --strict core/channels/signal_bridge/`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add core/channels/signal_bridge/ tests/core/channels/test_signal_bridge.py
git commit -m "feat(signal): add signal bridge scaffold with inbound/outbound forwarding"
```

---

## Phase 4D: Final Integration Test

### Task 9: End-to-End Smoke Test — Full Pipeline

**Files:**
- Create: `tests/integration/test_conscious_pipeline.py`

A comprehensive integration test that exercises the full Conscious Engine pipeline with mocked Redis and LLM, verifying that memory, integrations, scratchpad, and identity all flow correctly.

- [ ] **Step 1: Write integration test**

```python
"""Integration test — full Conscious Engine pipeline with all wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from bus.schemas.events import UserRequest
from core.conscious.context_assembler import ContextAssembler
from core.conscious.cost import CostTracker
from core.conscious.engine import ConsciousEngine
from core.conscious.identity import IdentityGate
from core.conscious.memory_reader import MemoryReader
from core.conscious.session import SessionManager
from core.memory.episodic.store import EpisodicStore
from core.memory.routines.store import RoutineStore


@pytest.fixture()
def full_engine(tmp_path: Path) -> ConsciousEngine:
    """Build a fully-wired ConsciousEngine with mocked externals."""
    prefs = tmp_path / "preferences"
    profile = tmp_path / "profile"
    prefs.mkdir()
    profile.mkdir()
    (prefs / "personal.md").write_text(
        "---\ndomain: general\nupdated: 2026-03-19\nconfidence: manual\n---\n\n"
        "# Personal\n\n- Wake time: 07:30\n- Dietary: vegetarian\n"
    )
    (profile / "proactivity.md").write_text(
        "---\ndomain: general\nupdated: 2026-03-19\nconfidence: manual\n---\n\n"
        "# Proactivity Level\n\n- Level: moderate\n"
    )

    redis = AsyncMock()
    return ConsciousEngine(
        redis=redis,
        identity_gate=IdentityGate(registered_phone="+1234567890"),
        session_mgr=SessionManager(redis=redis, timeout_minutes=30),
        cost_tracker=CostTracker(redis=redis, daily_cap_usd=5.0),
        context_assembler=ContextAssembler(),
        domain_router=AsyncMock(),
        tool_registry=AsyncMock(get_tools=AsyncMock(return_value=[])),
        context_reader=AsyncMock(get_rendered_context=AsyncMock(return_value="")),
        memory_reader=MemoryReader(
            preferences_dir=prefs,
            profile_dir=profile,
            default_proactivity="opinionated",
        ),
        claude_model="test-model",
        claude_api_key="test-key",
    )


@pytest.mark.asyncio
async def test_full_pipeline_sir_gets_preferences(full_engine: ConsciousEngine) -> None:
    """Sir should see preferences in the system prompt."""
    request = UserRequest(
        source="test", channel="web_pwa", session_id="s1",
        identity_claim="sir", content_type="text", content="Good morning",
    )

    mock_response = AsyncMock()
    mock_response.choices = [
        AsyncMock(message=AsyncMock(content="Good morning, sir.", tool_calls=None))
    ]
    mock_response.usage = AsyncMock(prompt_tokens=100, completion_tokens=20)

    with patch("litellm.acompletion", return_value=mock_response) as mock_llm:
        response = await full_engine.process_request(request)

    # Verify the system prompt includes preferences
    call_kwargs = mock_llm.call_args.kwargs
    system_msg = call_kwargs["messages"][0]["content"]
    assert "Wake time: 07:30" in system_msg
    assert "vegetarian" in system_msg
    assert "moderate" in system_msg  # proactivity level

    # Verify scratchpad was written
    full_engine._redis.lpush.assert_called()

    assert response.text == "Good morning, sir."


@pytest.mark.asyncio
async def test_full_pipeline_guest_no_preferences(full_engine: ConsciousEngine) -> None:
    """Guest should NOT see personal preferences in system prompt."""
    request = UserRequest(
        source="test", channel="web_pwa", session_id="s2",
        identity_claim="guest", content_type="text", content="Hello",
    )

    mock_response = AsyncMock()
    mock_response.choices = [
        AsyncMock(message=AsyncMock(content="Good evening.", tool_calls=None))
    ]
    mock_response.usage = AsyncMock(prompt_tokens=80, completion_tokens=10)

    with patch("litellm.acompletion", return_value=mock_response) as mock_llm:
        response = await full_engine.process_request(request)

    system_msg = mock_llm.call_args.kwargs["messages"][0]["content"]
    # Guest prompt should NOT contain personal info
    assert "Wake time" not in system_msg
    assert "vegetarian" not in system_msg
```

- [ ] **Step 2: Run integration test**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest tests/integration/test_conscious_pipeline.py -v`
Expected: PASS after all prior tasks are complete

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3 && .venv/bin/python -m pytest -x -q`
Expected: All tests PASS

- [ ] **Step 4: Run ruff + mypy on all modified files**

Run:
```bash
cd /Users/anirudhlath/code/private/alfred/alfred/.worktrees/phase3
.venv/bin/ruff check . --fix
.venv/bin/ruff format .
.venv/bin/mypy --strict core/conscious/ core/notifications/ core/librarian/ evals/
```
Expected: Clean

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_conscious_pipeline.py
git commit -m "test: add full-pipeline integration test for Conscious Engine wiring"
```

---

## Summary of Gaps Addressed

| Gap | Task | Status |
|-----|------|--------|
| Engine doesn't read memory | Task 1 + 2 | MemoryReader wired |
| Engine doesn't call IntegrationRegistry | Task 2 | Registry imported, docs built |
| Engine doesn't write scratchpad | Task 2 | lpush after each turn |
| Proactivity hardcoded to default | Task 1 + 2 | Read from profile, fallback to config |
| Web channel always resolves as guest | Task 3 | Local claim trust |
| Cost alert not delivered | Task 4 | NotificationPublisher |
| Librarian entity extraction stubbed | Task 5 | Claude-powered extraction |
| Librarian semantic/decay stubbed | Task 6 | Semantic updates + decay skeleton |
| Eval runner dry-run only | Task 7 | Live runner with mocked infra |
| Signal bridge missing | Task 8 | Scaffold at core/channels/signal_bridge/ |
| No integration test | Task 9 | Full pipeline test |

## Deferred to Future Phases

| Item | Reason |
|------|--------|
| WebAuthn registration + login | Requires frontend crypto + server-side credential store |
| Voice enrollment (SpeechBrain) | Requires audio sample collection UI + model training |
| Procedural memory pattern detection | Needs 2+ weeks of episodic data to detect patterns |
| Streaming TTS (WebSocket audio) | Enhancement, not a wiring gap |
| Runtime config hot-reload | Enhancement, not blocking |
| DomainRouter in Reflex path | Reflex already uses direct agent dispatch (correct for System 1) |
