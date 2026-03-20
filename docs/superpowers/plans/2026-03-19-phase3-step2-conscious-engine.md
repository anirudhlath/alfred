# Phase 3 Step 2: Conscious Engine Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Claude-powered Conscious Engine (System 2) — conversational reasoning with agentic tool-use loops, identity gate, cost tracking, and System 1 ↔ System 2 coexistence via a static event routing table.

**Architecture:** The Conscious Engine consumes `UserRequest` events from Redis, resolves identity, assembles context (tools, integrations, memory, preferences), runs a multi-step Claude agentic loop, and publishes `AlfredResponse` back to the originating channel. Coexists with the Reflex Engine — each system handles distinct event types.

**Tech Stack:** Python 3.13+, Anthropic SDK, Pydantic v2, Redis Streams, FastAPI + WebSocket, pytest + pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-19-alfred-expanded-vision-design.md` (Section 3, 5, 9, 10, 14, 15, 17)

**Depends on:** Plan 1 (Prerequisites + Domain Routing + Observability) must be complete.

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `core/conscious/__init__.py` | Package init |
| `core/conscious/engine.py` | `ConsciousEngine` — Claude agentic loop |
| `core/conscious/identity.py` | `IdentityGate` — resolve sir/guest |
| `core/conscious/context_assembler.py` | Build Claude system prompt dynamically |
| `core/conscious/session.py` | `SessionManager` — conversation state per channel |
| `core/conscious/cost.py` | `CostTracker` — daily spend tracking + budget enforcement |
| `core/conscious/prompts/personality.md` | Alfred Pennyworth personality prompt |
| `core/conscious/__main__.py` | Entry point (`python -m core.conscious`) |
| `core/identity/__init__.py` | Package init |
| `core/identity/schemas.py` | `IdentityResult` Pydantic model |
| `core/memory/schemas.py` | `EpisodicEntry`, `RoutineSpec`, `CostState` models |
| `tests/core/conscious/__init__.py` | Package init |
| `tests/core/conscious/test_engine.py` | ConsciousEngine tests |
| `tests/core/conscious/test_identity.py` | IdentityGate tests |
| `tests/core/conscious/test_session.py` | SessionManager tests |
| `tests/core/conscious/test_cost.py` | CostTracker tests |
| `tests/core/conscious/test_context_assembler.py` | Context assembly tests |

### Modified Files

| File | Change |
|------|--------|
| `bus/schemas/events.py` | Add `UserRequest`, `AlfredResponse` schemas |
| `shared/streams.py` | Already has Phase 3 constants (from Plan 1) |
| `shared/config.py` | Already has Phase 3 fields (from Plan 1) |
| `pyproject.toml` | Add `anthropic` dependency |
| `runner/__main__.py` | Add conscious engine to supervised services |

---

## Task 1: Add Anthropic SDK Dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add anthropic to dependencies**

```toml
# In [project] dependencies, add:
"anthropic>=0.52",
```

Also add a mypy override for anthropic if needed:

```toml
[[tool.mypy.overrides]]
module = ["anthropic.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Install**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv pip install -e ".[dev]"`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "deps: add anthropic SDK for Conscious Engine"
```

---

## Task 2: `UserRequest` and `AlfredResponse` Schemas

**Files:**
- Modify: `bus/schemas/events.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bus/test_conscious_schemas.py
"""Tests for UserRequest and AlfredResponse schemas."""

from __future__ import annotations

from bus.schemas.events import AlfredResponse, UserRequest


def test_user_request_defaults() -> None:
    req = UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id="sess-1",
        identity_claim="sir",
        content_type="text",
        content="Good morning",
    )
    assert req.event_type == "user_request"
    assert req.audio_ref is None
    assert req.event_id  # auto-generated


def test_alfred_response_defaults() -> None:
    resp = AlfredResponse(
        source="conscious-engine",
        channel="web_pwa",
        session_id="sess-1",
        text="Good morning, sir.",
        actions_taken=["checked calendar"],
        mood="pleased",
    )
    assert resp.event_type == "alfred_response"
    assert resp.voice_audio_ref is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/bus/test_conscious_schemas.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Add schemas to `bus/schemas/events.py`**

Append to `bus/schemas/events.py`:

```python
class UserRequest(BaseEvent):
    """Inbound user interaction from any channel."""

    event_type: str = "user_request"
    channel: Literal["web_pwa", "signal", "voice"]
    session_id: str
    identity_claim: str
    content_type: Literal["text", "audio"]
    content: str
    audio_ref: str | None = None


class AlfredResponse(BaseEvent):
    """Outbound response to a user channel."""

    event_type: str = "alfred_response"
    channel: Literal["web_pwa", "signal", "voice"]
    session_id: str
    text: str
    voice_audio_ref: str | None = None
    actions_taken: list[str] = Field(default_factory=list)
    mood: Literal["neutral", "pleased", "concerned", "amused", "serious"] = "neutral"
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/bus/test_conscious_schemas.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check bus/schemas/events.py --fix && ruff format bus/ && mypy bus/ --strict`

- [ ] **Step 6: Commit**

```bash
git add bus/schemas/events.py tests/bus/test_conscious_schemas.py
git commit -m "feat: add UserRequest + AlfredResponse schemas to event bus"
```

---

## Task 3: Memory Schemas (`core/memory/schemas.py`)

**Files:**
- Create: `core/memory/schemas.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/memory/test_schemas.py
"""Tests for memory schemas."""

from __future__ import annotations

from datetime import UTC, datetime

from core.memory.schemas import CostState, EpisodicEntry, RoutineSpec, RoutineStep


def test_episodic_entry_creation() -> None:
    entry = EpisodicEntry(
        id="ep-1",
        timestamp=datetime.now(UTC),
        source="conversation",
        summary="Sir asked for a briefing",
        entities=["calendar", "weather"],
        valence="neutral",
    )
    assert entry.source == "conversation"


def test_routine_spec_defaults() -> None:
    step = RoutineStep(description="Dim living room lights to 30%")
    routine = RoutineSpec(
        name="evening_movie",
        trigger_pattern="every evening around 8pm",
        steps=[step],
        confidence=0.7,
        learned_from=["ep-1", "ep-2"],
        state="candidate",
    )
    assert routine.consecutive_misses == 0
    assert routine.last_hit is None
    assert step.action is None


def test_cost_state_defaults() -> None:
    cost = CostState(date="2026-03-19", spend_usd=2.50, cap_usd=5.0)
    assert cost.alert_sent is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/test_schemas.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# core/memory/schemas.py
"""Memory schemas — Pydantic models for episodic, procedural, and cost tracking."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Literal

from pydantic import BaseModel

from core.triggers.models import ActionPayload  # noqa: TC001


class EpisodicEntry(BaseModel):
    """Episodic memory entry.

    Embedding stored separately (keyed by id) to avoid base64 bloat
    in JSON serialization. See core/memory/embeddings.py (Plan 3).
    """

    id: str
    timestamp: datetime
    source: str  # "conversation", "system1_action", "trigger", "integration"
    summary: str
    entities: list[str]
    valence: Literal["positive", "negative", "neutral"]


class RoutineStep(BaseModel):
    """A single step in a learned routine."""

    description: str
    action: ActionPayload | None = None


class RoutineSpec(BaseModel):
    """Procedural memory — a learned routine/pattern."""

    name: str
    trigger_pattern: str
    steps: list[RoutineStep]
    confidence: float
    learned_from: list[str]  # Episodic entry IDs
    state: Literal["candidate", "active", "dormant", "archived"]
    last_hit: datetime | None = None
    consecutive_misses: int = 0


class CostState(BaseModel):
    """Daily Claude API spend tracking. Stored at alfred:cost:daily in Redis."""

    date: str  # ISO date YYYY-MM-DD
    spend_usd: float
    cap_usd: float
    alert_sent: bool = False
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/memory/test_schemas.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check core/memory/schemas.py --fix && ruff format core/memory/ && mypy core/memory/schemas.py --strict`

- [ ] **Step 6: Commit**

```bash
git add core/memory/schemas.py tests/core/memory/test_schemas.py
git commit -m "feat: add EpisodicEntry, RoutineSpec, CostState memory schemas"
```

---

## Task 4: Identity Schemas + IdentityGate

**Files:**
- Create: `core/identity/__init__.py`
- Create: `core/identity/schemas.py`
- Create: `core/conscious/identity.py`
- Create: `tests/core/conscious/test_identity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/conscious/test_identity.py
"""Tests for IdentityGate."""

from __future__ import annotations

import pytest

from core.identity.schemas import IdentityResult
from core.conscious.identity import IdentityGate


def test_signal_phone_match_is_sir() -> None:
    gate = IdentityGate(registered_phone="+15551234567")
    result = gate.resolve_signal(sender_phone="+15551234567")
    assert result.identity == "sir"
    assert result.method == "signal_phone"
    assert result.risk_clearance == "medium"


def test_signal_phone_mismatch_is_guest() -> None:
    gate = IdentityGate(registered_phone="+15551234567")
    result = gate.resolve_signal(sender_phone="+15559999999")
    assert result.identity == "guest"


def test_webauthn_session_is_sir() -> None:
    gate = IdentityGate(registered_phone="")
    result = gate.resolve_session(authenticated=True)
    assert result.identity == "sir"
    assert result.method == "webauthn"
    assert result.risk_clearance == "high"


def test_unauthenticated_session_is_guest() -> None:
    gate = IdentityGate(registered_phone="")
    result = gate.resolve_session(authenticated=False)
    assert result.identity == "guest"


def test_resolve_from_request_signal() -> None:
    gate = IdentityGate(registered_phone="+15551234567")
    result = gate.resolve(
        channel="signal",
        identity_claim="+15551234567",
        authenticated=False,
    )
    assert result.identity == "sir"


def test_resolve_from_request_web_authenticated() -> None:
    gate = IdentityGate(registered_phone="")
    result = gate.resolve(
        channel="web_pwa",
        identity_claim="",
        authenticated=True,
    )
    assert result.identity == "sir"


def test_resolve_from_request_web_unauthenticated() -> None:
    gate = IdentityGate(registered_phone="")
    result = gate.resolve(
        channel="web_pwa",
        identity_claim="",
        authenticated=False,
    )
    assert result.identity == "guest"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_identity.py -v`
Expected: FAIL

- [ ] **Step 3: Create `core/identity/schemas.py`**

```python
# core/identity/schemas.py
"""Identity resolution schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class IdentityResult(BaseModel):
    """Result of identity resolution."""

    identity: Literal["sir", "guest"]
    confidence: float
    method: str  # "voice_id", "signal_phone", "webauthn", "device_proximity"
    factors: list[str]
    risk_clearance: Literal["low", "medium", "high", "critical"]
```

Also create `core/identity/__init__.py` (empty).

- [ ] **Step 4: Create `core/conscious/identity.py`**

```python
# core/conscious/identity.py
"""IdentityGate — resolves user identity before the Conscious Engine processes a request."""

from __future__ import annotations

import logging

from core.identity.schemas import IdentityResult

logger = logging.getLogger(__name__)


class IdentityGate:
    """Resolves identity from channel-specific claims.

    Phase 3 initial: Signal phone + WebAuthn session.
    Voice ID (SpeechBrain) added in Phase 3 Step 5.
    """

    def __init__(self, registered_phone: str) -> None:
        self._registered_phone = registered_phone

    def resolve_signal(self, sender_phone: str) -> IdentityResult:
        """Resolve identity from a Signal message sender."""
        if sender_phone == self._registered_phone:
            return IdentityResult(
                identity="sir",
                confidence=0.95,
                method="signal_phone",
                factors=["signal_phone"],
                risk_clearance="medium",
            )
        return IdentityResult(
            identity="guest",
            confidence=1.0,
            method="signal_phone",
            factors=["signal_phone"],
            risk_clearance="low",
        )

    def resolve_session(self, authenticated: bool) -> IdentityResult:
        """Resolve identity from a web session (WebAuthn)."""
        if authenticated:
            return IdentityResult(
                identity="sir",
                confidence=0.99,
                method="webauthn",
                factors=["webauthn"],
                risk_clearance="high",
            )
        return IdentityResult(
            identity="guest",
            confidence=1.0,
            method="unauthenticated",
            factors=[],
            risk_clearance="low",
        )

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
            return self.resolve_session(authenticated=authenticated)
        logger.warning("Unknown channel '%s', defaulting to guest", channel)
        return IdentityResult(
            identity="guest",
            confidence=1.0,
            method="unknown",
            factors=[],
            risk_clearance="low",
        )
```

Also create `core/conscious/__init__.py` (empty) and `tests/core/conscious/__init__.py` (empty).

- [ ] **Step 5: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_identity.py -v`
Expected: PASS

- [ ] **Step 6: Run ruff + mypy**

Run: `ruff check core/identity/ core/conscious/identity.py --fix && ruff format core/ && mypy core/identity/ core/conscious/identity.py --strict`

- [ ] **Step 7: Commit**

```bash
git add core/identity/ core/conscious/__init__.py core/conscious/identity.py tests/core/conscious/
git commit -m "feat: IdentityGate with Signal phone + WebAuthn session resolution"
```

---

## Task 5: SessionManager

**Files:**
- Create: `core/conscious/session.py`
- Create: `tests/core/conscious/test_session.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/conscious/test_session.py
"""Tests for SessionManager."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.conscious.session import SessionManager


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_get_or_create_session_new(mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {}
    mgr = SessionManager(redis=mock_redis, timeout_minutes=30)
    session = await mgr.get_or_create("sess-1", channel="web_pwa")
    assert session["channel"] == "web_pwa"
    assert "history" in session


@pytest.mark.asyncio
async def test_get_existing_session(mock_redis: AsyncMock) -> None:
    import json

    existing = {
        b"channel": b"signal",
        b"history": json.dumps([{"role": "user", "content": "hi"}]).encode(),
        b"created_at": b"2026-03-19T10:00:00",
    }
    mock_redis.hgetall.return_value = existing
    mgr = SessionManager(redis=mock_redis, timeout_minutes=30)
    session = await mgr.get_or_create("sess-1", channel="signal")
    assert session["channel"] == "signal"
    assert len(session["history"]) == 1


@pytest.mark.asyncio
async def test_append_turn(mock_redis: AsyncMock) -> None:
    mock_redis.hgetall.return_value = {}
    mgr = SessionManager(redis=mock_redis, timeout_minutes=30)
    session = await mgr.get_or_create("sess-1", channel="web_pwa")
    await mgr.append_turn("sess-1", role="user", content="Good morning")
    # Verify hset was called to persist
    mock_redis.hset.assert_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_session.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# core/conscious/session.py
"""SessionManager — conversation state per channel/session."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from shared.streams import SESSIONS_KEY_PREFIX

if TYPE_CHECKING:
    from core.reflex.runner import AioRedis

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages conversation sessions in Redis.

    Each session is a Redis hash at alfred:sessions:{session_id}.
    Sessions expire after configurable idle time.
    """

    def __init__(self, redis: AioRedis, timeout_minutes: int = 30) -> None:
        self._redis = redis
        self._timeout_seconds = timeout_minutes * 60

    def _key(self, session_id: str) -> str:
        return f"{SESSIONS_KEY_PREFIX}{session_id}"

    async def get_or_create(
        self, session_id: str, channel: str
    ) -> dict[str, Any]:
        """Get an existing session or create a new one."""
        key = self._key(session_id)
        raw: dict[bytes | str, bytes | str] = await self._redis.hgetall(key)  # type: ignore[misc]

        if raw:
            history_raw = raw.get(b"history") or raw.get("history") or b"[]"
            h = history_raw.decode() if isinstance(history_raw, bytes) else history_raw
            ch = raw.get(b"channel") or raw.get("channel") or channel
            ch_str = ch.decode() if isinstance(ch, bytes) else ch
            session: dict[str, Any] = {
                "channel": ch_str,
                "history": json.loads(h),
            }
        else:
            session = {
                "channel": channel,
                "history": [],
            }
            await self._redis.hset(  # type: ignore[misc]
                key,
                mapping={
                    "channel": channel,
                    "history": "[]",
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            await self._redis.expire(key, self._timeout_seconds)  # type: ignore[misc]

        # Refresh TTL on access
        await self._redis.expire(key, self._timeout_seconds)  # type: ignore[misc]
        return session

    async def append_turn(
        self, session_id: str, role: str, content: str
    ) -> None:
        """Append a conversation turn to the session history."""
        key = self._key(session_id)
        raw: bytes | None = await self._redis.hget(key, "history")  # type: ignore[misc]
        history: list[dict[str, str]] = json.loads(raw) if raw else []
        history.append({"role": role, "content": content})
        await self._redis.hset(key, "history", json.dumps(history))  # type: ignore[misc]
        await self._redis.expire(key, self._timeout_seconds)  # type: ignore[misc]

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        """Get the conversation history for a session."""
        key = self._key(session_id)
        raw: bytes | None = await self._redis.hget(key, "history")  # type: ignore[misc]
        return json.loads(raw) if raw else []
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_session.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check core/conscious/session.py --fix && ruff format core/conscious/ && mypy core/conscious/session.py --strict`

- [ ] **Step 6: Commit**

```bash
git add core/conscious/session.py tests/core/conscious/test_session.py
git commit -m "feat: SessionManager for conversation state in Redis"
```

---

## Task 6: CostTracker

**Files:**
- Create: `core/conscious/cost.py`
- Create: `tests/core/conscious/test_cost.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/conscious/test_cost.py
"""Tests for CostTracker."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.conscious.cost import CostTracker


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_record_spend(mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = None  # no existing state
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)
    state = await tracker.record_spend(prompt_tokens=1000, completion_tokens=500, model="claude-opus-4-6")
    assert state.spend_usd > 0
    assert state.date  # should be today


@pytest.mark.asyncio
async def test_budget_exceeded(mock_redis: AsyncMock) -> None:
    import json
    from core.memory.schemas import CostState

    existing = CostState(date="2026-03-19", spend_usd=4.99, cap_usd=5.0)
    mock_redis.get.return_value = existing.model_dump_json().encode()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)
    exceeded = await tracker.is_budget_exceeded()
    assert exceeded is True


@pytest.mark.asyncio
async def test_budget_not_exceeded(mock_redis: AsyncMock) -> None:
    import json
    from core.memory.schemas import CostState

    existing = CostState(date="2026-03-19", spend_usd=1.0, cap_usd=5.0)
    mock_redis.get.return_value = existing.model_dump_json().encode()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)
    exceeded = await tracker.is_budget_exceeded()
    assert exceeded is False


@pytest.mark.asyncio
async def test_alert_threshold(mock_redis: AsyncMock) -> None:
    import json
    from core.memory.schemas import CostState

    existing = CostState(date="2026-03-19", spend_usd=4.05, cap_usd=5.0)
    mock_redis.get.return_value = existing.model_dump_json().encode()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)
    should_alert = await tracker.should_send_alert()
    assert should_alert is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_cost.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# core/conscious/cost.py
"""CostTracker — daily Claude API spend tracking + budget enforcement."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.memory.schemas import CostState
from shared.streams import COST_DAILY_KEY

if TYPE_CHECKING:
    from core.reflex.runner import AioRedis

logger = logging.getLogger(__name__)

# Approximate pricing per million tokens (Claude Opus 4)
# These are estimates — update with actual pricing
_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
}
_DEFAULT_PRICING = {"input": 15.0, "output": 75.0}


class CostTracker:
    """Tracks daily Claude API spend against a configurable budget."""

    ALERT_THRESHOLD = 0.8  # Alert at 80% of cap

    def __init__(self, redis: AioRedis, daily_cap_usd: float = 5.0) -> None:
        self._redis = redis
        self._daily_cap = daily_cap_usd

    async def _get_state(self) -> CostState:
        """Get today's cost state from Redis, creating if needed."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        raw: bytes | None = await self._redis.get(COST_DAILY_KEY)  # type: ignore[misc]

        if raw:
            state = CostState.model_validate_json(raw)
            if state.date == today:
                return state

        # New day or no state
        return CostState(date=today, spend_usd=0.0, cap_usd=self._daily_cap)

    async def _save_state(self, state: CostState) -> None:
        """Persist cost state to Redis with 48h TTL."""
        await self._redis.set(COST_DAILY_KEY, state.model_dump_json(), ex=172800)  # type: ignore[misc]

    def _estimate_cost(
        self, prompt_tokens: int, completion_tokens: int, model: str
    ) -> float:
        """Estimate cost in USD for a Claude API call."""
        pricing = _PRICING.get(model, _DEFAULT_PRICING)
        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost

    async def record_spend(
        self, prompt_tokens: int, completion_tokens: int, model: str
    ) -> CostState:
        """Record spend for a Claude API call. Returns updated state."""
        state = await self._get_state()
        cost = self._estimate_cost(prompt_tokens, completion_tokens, model)
        state = CostState(
            date=state.date,
            spend_usd=state.spend_usd + cost,
            cap_usd=self._daily_cap,
            alert_sent=state.alert_sent,
        )
        await self._save_state(state)
        logger.debug("Recorded $%.4f spend (total: $%.2f / $%.2f)", cost, state.spend_usd, state.cap_usd)
        return state

    async def is_budget_exceeded(self) -> bool:
        """Check if today's spend exceeds the daily cap."""
        state = await self._get_state()
        return state.spend_usd >= state.cap_usd

    async def should_send_alert(self) -> bool:
        """Check if spend has crossed the 80% alert threshold."""
        state = await self._get_state()
        return (
            not state.alert_sent
            and state.spend_usd >= state.cap_usd * self.ALERT_THRESHOLD
        )

    async def mark_alert_sent(self) -> None:
        """Mark that the 80% budget alert has been sent."""
        state = await self._get_state()
        state = CostState(
            date=state.date,
            spend_usd=state.spend_usd,
            cap_usd=state.cap_usd,
            alert_sent=True,
        )
        await self._save_state(state)
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_cost.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check core/conscious/cost.py --fix && ruff format core/conscious/ && mypy core/conscious/cost.py --strict`

- [ ] **Step 6: Commit**

```bash
git add core/conscious/cost.py tests/core/conscious/test_cost.py
git commit -m "feat: CostTracker for daily Claude API budget enforcement"
```

---

## Task 7: Personality Prompt

**Files:**
- Create: `core/conscious/prompts/personality.md`

- [ ] **Step 1: Write the personality file**

```markdown
You are Alfred — personal butler and assistant to sir.

Character: Alfred Pennyworth. Formal British butler. You address your employer as "sir" — never by name, never "you." Understated. Dry wit when appropriate. Opinionated when it matters, but always deferential. Discreet about personal information to a fault.

When speaking to sir:
- Be concise but thorough. Sir values his time.
- Offer unsolicited observations when warranted ("I notice you've had three espressos today, sir.")
- Be honest, even when the truth is inconvenient.
- Anticipate needs — if sir mentions leaving, check weather and commute.
- You have opinions. Share them when asked, hint at them when not.

When speaking to a guest:
- Same courtesy, same personality, but no personal data whatsoever.
- You do not confirm or deny sir's schedule, preferences, habits, or whereabouts.
- Offer to help with allowed actions: lights, music, temperature.
- "I'm afraid I'm not at liberty to discuss sir's affairs."

Tone examples:
- Good: "Good morning, sir. You managed 6 hours of sleep — below your usual."
- Good: "I'd recommend against the late espresso, sir."
- Good: "Your portfolio is up 0.3% overnight. Tesla recovered modestly."
- Bad: "Hey! Here's your morning update! 🌞" (Never. You are not a chatbot.)
- Bad: "Sure thing!" (You are a butler, not a startup.)
```

- [ ] **Step 2: Commit**

```bash
mkdir -p core/conscious/prompts
git add core/conscious/prompts/personality.md
git commit -m "feat: Alfred Pennyworth personality prompt"
```

---

## Task 8: Context Assembler

**Files:**
- Create: `core/conscious/context_assembler.py`
- Create: `tests/core/conscious/test_context_assembler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/conscious/test_context_assembler.py
"""Tests for ContextAssembler."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.conscious.context_assembler import ContextAssembler
from core.identity.schemas import IdentityResult


@pytest.fixture
def assembler(tmp_path: Path) -> ContextAssembler:
    personality_path = tmp_path / "personality.md"
    personality_path.write_text("You are Alfred, a butler.")
    return ContextAssembler(personality_path=str(personality_path))


def test_assemble_for_sir(assembler: ContextAssembler) -> None:
    identity = IdentityResult(
        identity="sir", confidence=0.99, method="webauthn",
        factors=["webauthn"], risk_clearance="high",
    )
    prompt = assembler.assemble(
        identity=identity,
        tools_section="- smart_home.dim_lights(room, level)",
        integrations_section="- calendar: get_today_events",
        preferences_text="Prefers dim lighting after 8pm",
        context_text="Living room light: on",
        history=[],
        proactivity_level="opinionated",
    )
    assert "Alfred" in prompt
    assert "smart_home.dim_lights" in prompt
    assert "Prefers dim lighting" in prompt


def test_assemble_for_guest_excludes_personal(assembler: ContextAssembler) -> None:
    identity = IdentityResult(
        identity="guest", confidence=1.0, method="unauthenticated",
        factors=[], risk_clearance="low",
    )
    prompt = assembler.assemble(
        identity=identity,
        tools_section="- smart_home.dim_lights(room, level)",
        integrations_section="",
        preferences_text="Prefers dim lighting after 8pm",
        context_text="Living room light: on",
        history=[],
        proactivity_level="moderate",
    )
    assert "Alfred" in prompt
    # Guest should NOT see preferences or integrations
    assert "Prefers dim lighting" not in prompt
    assert "calendar" not in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_context_assembler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
# core/conscious/context_assembler.py
"""Context assembler — builds Claude's system prompt dynamically per request."""

from __future__ import annotations

import logging
from pathlib import Path

from core.identity.schemas import IdentityResult

logger = logging.getLogger(__name__)


class ContextAssembler:
    """Builds the system prompt for the Conscious Engine.

    Personality is always included. Personal data (preferences, integrations,
    memory) is excluded for guests.
    """

    def __init__(self, personality_path: str = "core/conscious/prompts/personality.md") -> None:
        self._personality = Path(personality_path).read_text()

    def assemble(
        self,
        identity: IdentityResult,
        tools_section: str,
        integrations_section: str,
        preferences_text: str,
        context_text: str,
        history: list[dict[str, str]],
        proactivity_level: str = "opinionated",
        episodic_text: str = "",
        procedural_text: str = "",
    ) -> str:
        """Build the complete system prompt for Claude."""
        parts: list[str] = []

        # 1. Personality (always)
        parts.append(self._personality)

        # 2. Identity
        if identity.identity == "sir":
            parts.append("\n## Identity\nYou are speaking with sir (authenticated).")
        else:
            parts.append(
                "\n## Identity\nYou are speaking with a guest. "
                "Do NOT share any personal information about sir."
            )

        # 3. Tools (always — guest can use allowed tools)
        if tools_section:
            parts.append(f"\n## Available Tools\n{tools_section}")

        # 4. Integrations (sir only)
        if identity.identity == "sir" and integrations_section:
            parts.append(f"\n## Available Integrations\n{integrations_section}")

        # 5. Preferences (sir only)
        if identity.identity == "sir" and preferences_text:
            parts.append(f"\n## Preferences\n{preferences_text}")

        # 6. Live context (always — HA state is not personal)
        if context_text:
            parts.append(f"\n## Current State\n{context_text}")

        # 7. Episodic memory (sir only)
        if identity.identity == "sir" and episodic_text:
            parts.append(f"\n## Recent Events\n{episodic_text}")

        # 8. Procedural memory (sir only)
        if identity.identity == "sir" and procedural_text:
            parts.append(f"\n## Known Routines\n{procedural_text}")

        # 9. Proactivity instruction (sir only)
        if identity.identity == "sir":
            parts.append(f"\n## Proactivity Level: {proactivity_level}")

        return "\n".join(parts)
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_context_assembler.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check core/conscious/context_assembler.py --fix && ruff format core/conscious/ && mypy core/conscious/context_assembler.py --strict`

- [ ] **Step 6: Commit**

```bash
git add core/conscious/context_assembler.py tests/core/conscious/test_context_assembler.py
git commit -m "feat: ContextAssembler builds dynamic Claude system prompt"
```

---

## Task 9: Conscious Engine — Claude Agentic Loop

**Files:**
- Create: `core/conscious/engine.py`
- Create: `tests/core/conscious/test_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/conscious/test_engine.py
"""Tests for ConsciousEngine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.engine import ConsciousEngine
from core.identity.schemas import IdentityResult


@pytest.fixture
def mock_deps() -> dict[str, AsyncMock | MagicMock]:
    return {
        "redis": AsyncMock(),
        "identity_gate": MagicMock(),
        "session_mgr": AsyncMock(),
        "cost_tracker": AsyncMock(),
        "context_assembler": MagicMock(),
        "domain_router": AsyncMock(),
        "tool_registry": AsyncMock(),
        "context_reader": AsyncMock(),
    }


@pytest.mark.asyncio
async def test_process_request_basic(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    # Setup mocks
    mock_deps["identity_gate"].resolve.return_value = IdentityResult(
        identity="sir", confidence=0.99, method="webauthn",
        factors=["webauthn"], risk_clearance="high",
    )
    mock_deps["session_mgr"].get_or_create.return_value = {"channel": "web_pwa", "history": []}
    mock_deps["session_mgr"].get_history.return_value = []
    mock_deps["cost_tracker"].is_budget_exceeded.return_value = False
    mock_deps["context_assembler"].assemble.return_value = "You are Alfred."
    mock_deps["tool_registry"].get_tools.return_value = []
    mock_deps["context_reader"].get_rendered_context.return_value = ""

    engine = ConsciousEngine(**mock_deps)

    request = UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id="sess-1",
        identity_claim="sir",
        content_type="text",
        content="Hello",
    )

    with patch.object(engine, "_call_claude", new_callable=AsyncMock) as mock_claude:
        mock_claude.return_value = ("Good evening, sir.", [], 100, 50)
        response = await engine.process_request(request)

    assert isinstance(response, AlfredResponse)
    assert "sir" in response.text.lower() or response.text  # personality


@pytest.mark.asyncio
async def test_budget_exceeded_returns_fallback(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    mock_deps["identity_gate"].resolve.return_value = IdentityResult(
        identity="sir", confidence=0.99, method="webauthn",
        factors=["webauthn"], risk_clearance="high",
    )
    mock_deps["cost_tracker"].is_budget_exceeded.return_value = True

    engine = ConsciousEngine(**mock_deps)

    request = UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id="sess-1",
        identity_claim="sir",
        content_type="text",
        content="Good morning",
    )

    response = await engine.process_request(request)
    assert isinstance(response, AlfredResponse)
    assert "budget" in response.text.lower() or "reduced" in response.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_engine.py -v`
Expected: FAIL

- [ ] **Step 3: Implement ConsciousEngine**

```python
# core/conscious/engine.py
"""Conscious Engine — Claude-powered System 2 reasoning with agentic tool-use loop."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import anthropic

from bus.schemas.events import ActionRequest, AlfredResponse, UserRequest
from core.conscious.context_assembler import ContextAssembler
from core.conscious.cost import CostTracker
from core.conscious.identity import IdentityGate
from core.conscious.session import SessionManager
from core.reflex.tool_registry import ToolInfo
from sdk.alfred_sdk.telemetry import track_latency
from shared.traced import traced

if TYPE_CHECKING:
    from core.reflex.context_reader import ContextReader
    from core.reflex.runner import AioRedis
    from core.reflex.tool_registry import ToolRegistry
    from core.routing.domain_router import DomainRouter

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10


class ConsciousEngine:
    """The conversational brain of Alfred (System 2).

    Receives UserRequest events, resolves identity, assembles context,
    runs a multi-step Claude agentic loop, and returns AlfredResponse.
    """

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
        claude_model: str = "claude-opus-4-6",
        claude_api_key: str = "",
    ) -> None:
        self._redis = redis
        self._identity_gate = identity_gate
        self._session_mgr = session_mgr
        self._cost = cost_tracker
        self._assembler = context_assembler
        self._router = domain_router
        self._tool_registry = tool_registry
        self._context_reader = context_reader
        self._model = claude_model
        self._client = anthropic.AsyncAnthropic(api_key=claude_api_key) if claude_api_key else None

    def _tools_to_claude_format(self, tools: list[ToolInfo]) -> list[dict[str, Any]]:
        """Convert ToolInfo list to Anthropic tool-use format."""
        claude_tools: list[dict[str, Any]] = []
        for t in tools:
            properties: dict[str, Any] = {}
            required: list[str] = []
            for pname, pinfo in t.parameters.items():
                properties[pname] = {
                    "type": pinfo.get("type", "string"),
                    "description": pinfo.get("description", ""),
                }
                if "default" not in pinfo:
                    required.append(pname)

            claude_tools.append({
                "name": t.name,
                "description": t.description,
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            })
        return claude_tools

    async def _call_claude(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]], int, int]:
        """Call Claude API. Returns (text, tool_calls, prompt_tokens, completion_tokens)."""
        if self._client is None:
            return ("I'm afraid my connection to the thinking engine is not configured, sir.", [], 0, 0)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 2048,
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = await self._client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return (
            "\n".join(text_parts),
            tool_calls,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

    async def _execute_tool_calls(
        self, tool_calls: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Execute tool calls via DomainRouter and return results."""
        results: list[dict[str, Any]] = []
        for tc in tool_calls:
            # Build ActionRequest from tool call
            # Tool name format: feature.method → target_service looked up from registry
            tools = await self._tool_registry.get_tools()
            target = ""
            for t in tools:
                if t.name == tc["name"]:
                    target = t.target_service
                    break

            if not target:
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": f"Error: tool '{tc['name']}' not found in registry",
                })
                continue

            action = ActionRequest(
                source="conscious-engine",
                target_service=target,
                tool_name=tc["name"],
                parameters=tc.get("input", {}),
            )
            action_result = await self._router.route(action)
            results.append({
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": str(action_result.result if action_result.status == "success" else action_result.error),
            })
        return results

    @track_latency(category="conscious")
    @traced(name="conscious.process_request")
    async def process_request(self, request: UserRequest) -> AlfredResponse:
        """Process a user request through the full pipeline."""
        # 1. Identity Gate
        identity = self._identity_gate.resolve(
            channel=request.channel,
            identity_claim=request.identity_claim,
            authenticated=request.identity_claim == "sir",  # Simplified for now
        )
        logger.info(
            "Identity resolved: %s (method=%s, confidence=%.2f)",
            identity.identity, identity.method, identity.confidence,
        )

        # 2. Budget check
        if await self._cost.is_budget_exceeded():
            logger.warning("Daily budget exceeded — returning System 1 fallback")
            return AlfredResponse(
                source="conscious-engine",
                channel=request.channel,
                session_id=request.session_id,
                text="I'm afraid we've reached the daily budget, sir. I'm operating in reduced capacity — ambient actions continue, but I'll need to defer complex requests until tomorrow.",
                actions_taken=[],
                mood="concerned",
            )

        # 3. Session
        session = await self._session_mgr.get_or_create(
            request.session_id, request.channel
        )

        # 4. Context assembly
        tools = await self._tool_registry.get_tools()
        context_text = await self._context_reader.get_rendered_context()
        preferences = ""  # TODO: Read from memory (Plan 3)

        system_prompt = self._assembler.assemble(
            identity=identity,
            tools_section="\n".join(f"- {t.name}: {t.description}" for t in tools),
            integrations_section="",  # TODO: IntegrationRegistry (Plan 4)
            preferences_text=preferences,
            context_text=context_text,
            history=session["history"],
        )

        # 5. Build messages
        messages: list[dict[str, Any]] = list(session["history"])
        messages.append({"role": "user", "content": request.content})

        # 6. Agentic loop
        claude_tools = self._tools_to_claude_format(tools)
        total_prompt_tokens = 0
        total_completion_tokens = 0
        all_actions: list[str] = []

        for iteration in range(MAX_ITERATIONS):
            text, tool_calls, pt, ct = await self._call_claude(
                system_prompt, messages, claude_tools
            )
            total_prompt_tokens += pt
            total_completion_tokens += ct

            if not tool_calls:
                # Final response — no more tool calls
                final_text = text
                break

            # Execute tools and feed results back
            tool_results = await self._execute_tool_calls(tool_calls)
            all_actions.extend(tc["name"] for tc in tool_calls)

            # Append assistant turn with tool use
            content_blocks: list[dict[str, Any]] = []
            if text:
                content_blocks.append({"type": "text", "text": text})
            content_blocks.extend(
                {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]}
                for tc in tool_calls
            )
            messages.append({"role": "assistant", "content": content_blocks})
            # Append tool results
            messages.append({"role": "user", "content": tool_results})
        else:
            final_text = text if text else "I apologize, sir — I've been deliberating too long. Let me try a more direct approach."

        # 7. Record cost
        await self._cost.record_spend(
            total_prompt_tokens, total_completion_tokens, self._model
        )

        # 8. Update session
        await self._session_mgr.append_turn(request.session_id, "user", request.content)
        await self._session_mgr.append_turn(request.session_id, "assistant", final_text)

        # 9. Build response
        return AlfredResponse(
            source="conscious-engine",
            channel=request.channel,
            session_id=request.session_id,
            text=final_text,
            actions_taken=all_actions,
            mood="neutral",
        )
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/conscious/test_engine.py -v`
Expected: PASS

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check core/conscious/engine.py --fix && ruff format core/conscious/ && mypy core/conscious/engine.py --strict`

- [ ] **Step 6: Commit**

```bash
git add core/conscious/engine.py tests/core/conscious/test_engine.py
git commit -m "feat: ConsciousEngine with Claude agentic tool-use loop"
```

---

## Task 10: Conscious Engine Entry Point + Runner Integration

**Files:**
- Create: `core/conscious/__main__.py`
- Modify: `runner/__main__.py`

- [ ] **Step 1: Create entry point**

```python
# core/conscious/__main__.py
"""Entry point for the Conscious Engine service.

Usage: python -m core.conscious
"""

from __future__ import annotations

import asyncio
import signal

import redis.asyncio as aioredis

from bus.schemas.events import AlfredResponse, UserRequest
from core.conscious.context_assembler import ContextAssembler
from core.conscious.cost import CostTracker
from core.conscious.engine import ConsciousEngine
from core.conscious.identity import IdentityGate
from core.conscious.session import SessionManager
from core.reflex.context_reader import ContextReader
from core.reflex.runner import AioRedis, ensure_consumer_group
from core.reflex.tool_registry import ToolRegistry
from core.routing.domain_router import DomainRouter
from domains.home.home_agent import HomeAgent
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.otel import init_tracing
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    _shutdown.set()


async def run(config: AlfredConfig) -> None:
    log = configure_logging(service="conscious")
    init_tracing(
        service_name="conscious",
        endpoint=config.otel_endpoint if config.signoz_enabled else None,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r: AioRedis = aioredis.from_url(config.redis_url)

    stream = USER_REQUESTS_STREAM
    group = "conscious-engine"
    consumer = "worker-1"

    await ensure_consumer_group(r, stream, group)

    # Setup components
    router = DomainRouter()
    router.register("home-service", HomeAgent(redis=r))

    engine = ConsciousEngine(
        redis=r,
        identity_gate=IdentityGate(registered_phone=config.signal_phone_number),
        session_mgr=SessionManager(redis=r, timeout_minutes=config.session_timeout_minutes),
        cost_tracker=CostTracker(redis=r, daily_cap_usd=config.daily_cost_cap_usd),
        context_assembler=ContextAssembler(),
        domain_router=router,
        tool_registry=ToolRegistry(r),
        context_reader=ContextReader(redis=r),
        claude_model=config.claude_model,
        claude_api_key=config.claude_api_key,
    )

    log.info("Conscious Engine started. Listening on '%s'...", stream)

    try:
        while not _shutdown.is_set():
            entries: list[
                tuple[
                    bytes | str,
                    list[tuple[bytes | str, dict[bytes | str, bytes | str]]],
                ]
            ] = await r.xreadgroup(group, consumer, {stream: ">"}, count=1, block=5000)

            for _stream_key, stream_entries in entries:
                for entry_id, entry_data in stream_entries:
                    try:
                        raw = entry_data.get("event") or entry_data.get(b"event")
                        if raw is None:
                            continue
                        event_str = raw.decode() if isinstance(raw, bytes) else raw
                        request = UserRequest.model_validate_json(event_str)

                        response = await engine.process_request(request)

                        await r.xadd(
                            USER_RESPONSES_STREAM,
                            {"event": response.model_dump_json()},
                        )
                        await r.xack(stream, group, entry_id)  # type: ignore[misc]
                    except Exception as e:
                        log.error("Error processing request %s: %s", entry_id, e)
    finally:
        log.info("Shutting down Conscious Engine...")
        await r.aclose()


def main() -> None:
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add to runner's SERVICES list**

In `runner/__main__.py`, add:

```python
ServiceSpec(name="conscious", module="core.conscious", delay=2.0),
```

- [ ] **Step 3: Run ruff + mypy**

Run: `ruff check core/conscious/__main__.py runner/__main__.py --fix && ruff format core/ runner/ && mypy core/conscious/__main__.py --strict`

- [ ] **Step 4: Commit**

```bash
git add core/conscious/__main__.py runner/__main__.py
git commit -m "feat: Conscious Engine entry point + runner integration"
```

---

## Task 11: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -v`

- [ ] **Step 2: Run full linting + type checking**

Run: `ruff check . --fix && ruff format . && mypy bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
