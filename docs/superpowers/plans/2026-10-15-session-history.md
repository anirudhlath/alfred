# Full Session History (PR 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** System 2 remembers a whole session: every turn's tool calls and results carry into the next turn, compaction bounds how much history the model gets, and a change of speaker on a shared session starts over.

**Architecture:**
- **Storage.** `SessionManager` stores the run's whole message history in the session hash's `messages` field, as `ModelMessagesTypeAdapter` JSON, along with the identity that wrote it.
  - Each turn **replaces** the field. Compaction rewrites older messages, so the run's final history *is* the session.
  - Instructions are stripped before storing, because every run sends its own.
  - A pre-existing `history` field (text turns) is converted on read and deleted on the next write.
- **Bounds.** The System 2 agent gets three harness capabilities, cheapest first:
  - a truncate-only `ToolOutputLimits`, which caps any one tool result;
  - `ClearToolResults`, which blanks old tool results past a token ceiling;
  - `SlidingWindowCompaction`, which drops old messages past a message count.
- **Admin.** The admin session list counts user turns from the stored messages.

**Tech Stack:** Python 3.13, Pydantic AI 2.55.0, pydantic-ai-harness 0.55.0 (`compaction`, `tool_output_limits`), redis-py asyncio, fakeredis, FastAPI.

**Spec:** `docs/superpowers/specs/2026-10-08-pydantic-ai-adoption-design.md`:
- component 5, "Full session history with compaction";
- delivery-table row 4: `feat/session-history`, verified by "evals; multi-turn tests".

Base: `master` at `5921523`. Branch: `feat/session-history`. Worktree: `~/code/.worktrees/alfred/session-history`. Tracking: #317.

## Global Constraints

- **Gate:**
  ```
  set -o pipefail; uv sync -q --all-extras && uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync mypy --strict alfred_cli/ alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/ && PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q -p no:randomly
  ```
  Run it once per task, before commit. While iterating, run only the narrow test files.
- **Session hash.** The session hash stays `alfred:sessions:{id}` (`SESSIONS_KEY_PREFIX` in `shared/streams.py`). Its TTL stays `SESSION_TIMEOUT_MINUTES` (default 30), refreshed on every read and write.
- **Field names, verbatim:**

  | field | holds |
  |---|---|
  | `messages` | `ModelMessagesTypeAdapter` JSON |
  | `identity` | the `IdentityResult.identity` that wrote it: `sir` or `guest` |
  | `channel` | unchanged |
  | `created_at` | unchanged |
  | `history` | legacy: read once, never written |
- **Never the default `Spill` band.** `ToolOutputLimits` gets one `Truncate` band and no store (spec component 5). It must not add a `read_tool_result` tool: the catalog stays the agent's one toolset (spec component 2).
- **Thresholds are module constants** in `core/conscious/history.py`, with no env vars. Their values and the measurements behind them are in Ruling 4.
- **No model identifiers** in commit messages, code comments, docs or PR text.
- **The repo is public.** No hostnames, IPs, domains or keys in any file.
- **Secret hygiene.** Run the two greps from the deploy runbook's §Secret hygiene (`~/code/alfred-deploy/PWA-EXPOSURE-RUNBOOK.md`) before every commit; they must print nothing. Never quote those commands in a committed file.

## Rulings (decided while planning)

1. **`store_run` replaces; it does not append.**
   - The spec names the method `append_run`. `SlidingWindowCompaction` and `ClearToolResults` both rewrite `ctx.messages` in place, so `result.all_messages()` is the compacted history. Probed with pydantic-ai-harness 0.55.0: a 70-message history came back as 44 messages.
   - Appending would undo compaction, so the method writes the run's whole history and is named `store_run`.
   - Cost if wrong: a rename.
2. **Instructions are stripped before storing.**
   - Every `ModelRequest` that `all_messages()` returns carries `instructions`. In production that is the full assembled prompt, about 18k tokens. The model adapter sends only the latest request's instructions.
   - Storing them would multiply the Redis value by the turn count for nothing, so `store_run` sets `instructions=None` on every request.
3. **Speaker change.**
   - The session stores the identity that wrote it last. A turn by a different identity reads no history, and its `store_run` overwrites the session.
   - A session with no `identity` field keeps its history. That means a legacy session, or one first written by a ledger-answered turn before any run.
   - So legacy sessions convert as the spec says, for the 30 minutes they can live after deploy.
4. **Thresholds, from production measurements over 7 days of Langfuse (2026-10-08 to 10-15).**
   - **What was measured:**
     - System 2 input per model request: p50 17,944 tokens, max 24,623. Nearly all of it is instructions and tool definitions.
     - Turns per session: 1–2.
     - Model requests per turn: 1–2.
     - Largest observed tool result: 187 characters.
     - An unfiltered `get_live_state` in the prod container: 77,678 characters, about 19k tokens. A domain-filtered one is 4,600 characters or less.
     - The eval model's context window is 32,768 tokens.
   - **The constants:**

     | constant | value | effect |
     |---|---|---|
     | `TOOL_RESULT_MAX_CHARS` | 100,000 | Above the largest real result, so current-turn behaviour is unchanged. It is a ceiling, not a trim. |
     | `CLEAR_TOOL_RESULTS_OVER_TOKENS` | 28,000 | The estimate includes instructions and tool definitions. A session starts blanking old results once its history adds roughly 4–10k tokens, which leaves room for output on the 32k eval model. |
     | `TOOL_RESULTS_KEPT` | 3 | The harness default. The three most recent tool call/return pairs are never blanked, so the result a follow-up refers to survives. |
     | `CLEAR_AT_LEAST_TOKENS` | 2,000 | Never blank for a trivial gain. |
     | `SESSION_MAX_MESSAGES` | 60 | |
     | `SESSION_KEPT_MESSAGES` | 40 | 30 or more text turns, an order of magnitude above the measured sessions. |
   - `ClearToolResults` is in addition to the spec's list (sliding window plus `ToolOutputLimits`). It costs nothing and is the only bound that acts on a single long-tailed result from an earlier turn.
   - Cost if wrong: the constants change.
5. **`ToolOutputLimits` without its read tool.**
   - `ToolOutputLimits.get_toolset()` always registers `read_tool_result`, even when no band spills (pydantic-ai-harness 0.55.0, `tool_output_limits/_capability.py`).
   - `TruncateToolOutput` subclasses it and returns `None` from `get_toolset`. Probed: the tool list is then only the agent's own tools, and a 150,000-character return reaches the model as exactly 100,000.
6. **A run that ends without its own answer** (not configured, budget spent mid-run, request limit, retries exhausted):
   - It stores the prior history plus the turn as text: the user's words and the reply sent, as today. It never stores the half-finished run's messages, so a stored history never ends in a tool call without its return.
   - A provider error still raises and stores nothing, as today.
7. **A ledger-answered turn** (`record_partial_turn`):
   - It resolves the request's identity the way a normal turn does.
   - It appends the turn as text to the stored messages under the same speaker rule, then stores the result with that identity.
8. **Concurrent turns on one session still race.** The last `store_run` wins, as the read-modify-write of `append_turn` does today. Requests are consumed by one runner, so this needs two requests for one session in flight at once. Not addressed here.
9. **Admin turn count:**
   - `turns` counts requests carrying a `UserPromptPart`, from `messages`, or from the converted legacy `history`.
   - A legacy `[user, assistant, user]` now counts 2, not 3. The old count was entries, not turns.
   - Unreadable JSON counts 0, as today.

## Review Focus

1. **Stored `messages` that no longer validate**, after a Pydantic AI upgrade changes the message schema or a hand edit. Expected: the turn runs with no history and logs one warning; the session is never stuck failing every turn. Pinned in Task 1 (`test_unreadable_messages_read_as_no_history`).
2. **A shared satellite session where sir speaks, then a guest, then sir.** Expected: the guest's turn sees none of sir's history, and sir's next turn starts fresh too, because the guest's store replaced the session. Pinned in Task 2 (`test_a_new_speaker_starts_without_the_sessions_history`).
3. **A turn that acted and then ended in the deliberating reply.** Expected: the next turn's history is valid for the model (no dangling tool call), and it holds the text the user was sent. Pinned in Task 2 (`test_a_turn_without_its_own_answer_stores_the_turn_as_text`).
4. **A ledger-answered turn on an expired session.** Expected: the session is recreated holding just that turn; nothing raises. Pinned in Task 2 (`test_record_partial_turn_on_an_expired_session_starts_it`).
5. **One tool result bigger than the ceiling.** Expected: the model sees at most `TOOL_RESULT_MAX_CHARS` of it in this turn, and the stored copy is the clipped one. Pinned in Task 3 (`test_an_oversized_tool_result_is_truncated_before_the_model_and_the_store`).

## File map

| file | change |
|---|---|
| `core/conscious/session.py` | Rewritten. `Session`, `text_turn`, `legacy_messages`, `stored_messages`, `turn_count`, and `SessionManager.load` / `store_run` / `append_text_turn`. `get_or_create` / `append_turn` / `get_history` stay until Task 2 removes them. |
| `core/conscious/engine.py` | `process_request`, `_run` and `record_partial_turn` use the new API. `session_messages` moves to `session.legacy_messages`. New `_resolve`. |
| `core/conscious/history.py` | **New.** Threshold constants, `TruncateToolOutput`, `history_capabilities()`. |
| `core/conscious/agent.py` | Adds `history_capabilities()` to the agent. |
| `core/channels/admin_api.py` | `GET /sessions` counts turns with `turn_count(stored_messages(...))`. |
| `tests/core/conscious/test_session.py` | Rewritten on fakeredis. |
| `tests/core/conscious/test_engine.py` | Session mocks and assertions updated; multi-turn tests added. |
| `tests/core/conscious/test_effects.py` | One partial-turn assertion reads `messages`. |
| `tests/core/channels/test_admin_api.py` | Turn-count tests. |
| `docs/llm.md`, `docs/architecture.md`, `docs/admin-api.md` | Session prose. |

---

### Task 1: Session store with full message history

Adds the new API beside the old, so the engine keeps working until Task 2 switches it.

**Files:**
- Modify: `core/conscious/session.py` (whole file, 78 lines)
- Test: `tests/core/conscious/test_session.py` (rewrite)

**Interfaces:**
- Consumes: `SESSIONS_KEY_PREFIX` from `shared.streams`; `AioRedis` from `shared.types`.
- Produces (Tasks 2 and 4 rely on these exact names):
  - `MESSAGES_FIELD = "messages"`, `IDENTITY_FIELD = "identity"`, `LEGACY_HISTORY_FIELD = "history"`
  - `@dataclass(frozen=True) class Session: channel: str; messages: list[ModelMessage]`
  - `def text_turn(user: str, reply: str) -> list[ModelMessage]`
  - `def legacy_messages(history: list[dict[str, str]]) -> list[ModelMessage]`
  - `def stored_messages(raw: Mapping[Any, bytes | str]) -> list[ModelMessage]`  (`Mapping` is invariant in its key, so `Any` lets both raw Redis bytes and a decoded `dict[str, str]` through)
  - `def turn_count(messages: Sequence[ModelMessage]) -> int`
  - `SessionManager.load(session_id: str, channel: str, identity: str) -> Session`
  - `SessionManager.store_run(session_id: str, messages: Sequence[ModelMessage], identity: str) -> None`
  - `SessionManager.append_text_turn(session_id: str, identity: str, user: str, reply: str) -> None`

- [ ] **Step 1: Write the failing tests.** Replace `tests/core/conscious/test_session.py` with:

```python
"""Tests for SessionManager: full message history per session (spec, component 5)."""

from __future__ import annotations

import json
import logging

import fakeredis
import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from core.conscious.session import (
    IDENTITY_FIELD,
    LEGACY_HISTORY_FIELD,
    MESSAGES_FIELD,
    SessionManager,
    legacy_messages,
    stored_messages,
    text_turn,
    turn_count,
)
from shared.streams import SESSIONS_KEY_PREFIX

KEY = f"{SESSIONS_KEY_PREFIX}sess-1"


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


def _texts(messages: list[ModelMessage]) -> list[tuple[str, str]]:
    """Each message as (kind, its first part's text), for comparing without timestamps."""
    out: list[tuple[str, str]] = []
    for m in messages:
        part = m.parts[0]
        content = getattr(part, "content", None)
        out.append((m.kind, content if isinstance(content, str) else type(part).__name__))
    return out


def _tool_turn() -> list[ModelMessage]:
    return [
        ModelRequest(parts=[UserPromptPart("Is the desk lamp on?")], instructions="BIG PROMPT"),
        ModelResponse(parts=[ToolCallPart("home_get_lights", {"entity_id": "light.desk"}, "c1")]),
        ModelRequest(
            parts=[ToolReturnPart("home_get_lights", {"state": "on"}, "c1")],
            instructions="BIG PROMPT",
        ),
        ModelResponse(parts=[TextPart("It is on, sir.")]),
    ]


async def test_a_new_session_is_created_empty_with_its_channel_and_ttl(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    session = await SessionManager(redis, timeout_minutes=45).load("sess-1", "web_pwa", "sir")

    assert (session.channel, session.messages) == ("web_pwa", [])
    assert await redis.hget(KEY, "channel") == b"web_pwa"
    assert await redis.hget(KEY, "created_at")
    assert 0 < await redis.ttl(KEY) <= 45 * 60


async def test_a_stored_run_is_read_back_with_its_tool_calls(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    mgr = SessionManager(redis)
    await mgr.load("sess-1", "web_pwa", "sir")

    await mgr.store_run("sess-1", _tool_turn(), "sir")
    session = await mgr.load("sess-1", "web_pwa", "sir")

    assert _texts(session.messages) == [
        ("request", "Is the desk lamp on?"),
        ("response", "ToolCallPart"),
        ("request", "ToolReturnPart"),
        ("response", "It is on, sir."),
    ]
    tool_return = session.messages[2].parts[0]
    assert isinstance(tool_return, ToolReturnPart)
    assert tool_return.content == {"state": "on"}


async def test_stored_requests_carry_no_instructions(redis: fakeredis.FakeAsyncRedis) -> None:
    mgr = SessionManager(redis)

    await mgr.store_run("sess-1", _tool_turn(), "sir")

    raw = await redis.hget(KEY, MESSAGES_FIELD)
    assert raw is not None and b"BIG PROMPT" not in raw
    messages = ModelMessagesTypeAdapter.validate_json(raw)
    assert all(m.instructions is None for m in messages if isinstance(m, ModelRequest))


async def test_store_run_replaces_the_history_and_records_the_speaker(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    mgr = SessionManager(redis, timeout_minutes=30)
    await mgr.store_run("sess-1", _tool_turn(), "sir")

    await mgr.store_run("sess-1", text_turn("Hello", "Good evening, sir."), "sir")

    session = await mgr.load("sess-1", "web_pwa", "sir")
    assert _texts(session.messages) == [("request", "Hello"), ("response", "Good evening, sir.")]
    assert await redis.hget(KEY, IDENTITY_FIELD) == b"sir"
    assert 0 < await redis.ttl(KEY) <= 30 * 60


async def test_another_speakers_history_is_not_read(redis: fakeredis.FakeAsyncRedis) -> None:
    mgr = SessionManager(redis)
    await mgr.store_run("sat-kitchen", text_turn("Unlock the door", "Unlocked, sir."), "sir")

    session = await mgr.load("sat-kitchen", "satellite", "guest")

    assert session.messages == []


async def test_a_legacy_text_history_is_converted_and_dropped_on_the_next_write(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    history = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello, sir."}]
    await redis.hset(KEY, mapping={"channel": "signal", LEGACY_HISTORY_FIELD: json.dumps(history)})
    mgr = SessionManager(redis)

    session = await mgr.load("sess-1", "signal", "guest")
    assert _texts(session.messages) == [("request", "Hi"), ("response", "Hello, sir.")]

    await mgr.store_run("sess-1", session.messages, "guest")
    assert await redis.hget(KEY, LEGACY_HISTORY_FIELD) is None
    assert await redis.hget(KEY, "channel") == b"signal"


async def test_unreadable_messages_read_as_no_history(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    await redis.hset(KEY, mapping={"channel": "web_pwa", MESSAGES_FIELD: '[{"kind": "nope"}]'})

    with caplog.at_level(logging.WARNING, logger="core.conscious.session"):
        session = await SessionManager(redis).load("sess-1", "web_pwa", "sir")

    assert session.messages == []
    assert "Unreadable session history" in caplog.text


async def test_append_text_turn_adds_to_the_stored_run(redis: fakeredis.FakeAsyncRedis) -> None:
    mgr = SessionManager(redis)
    await mgr.store_run("sess-1", _tool_turn(), "sir")

    await mgr.append_text_turn("sess-1", "sir", "Turn on the porch light", "I did, sir.")

    session = await mgr.load("sess-1", "web_pwa", "sir")
    assert _texts(session.messages)[-2:] == [
        ("request", "Turn on the porch light"),
        ("response", "I did, sir."),
    ]
    assert len(session.messages) == 6


async def test_append_text_turn_by_another_speaker_starts_over(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    mgr = SessionManager(redis)
    await mgr.store_run("sat-kitchen", _tool_turn(), "sir")

    await mgr.append_text_turn("sat-kitchen", "guest", "Lights on", "Done.")

    raw = await redis.hgetall(f"{SESSIONS_KEY_PREFIX}sat-kitchen")
    assert _texts(stored_messages(raw)) == [("request", "Lights on"), ("response", "Done.")]
    assert raw[IDENTITY_FIELD.encode()] == b"guest"


async def test_append_text_turn_on_a_missing_session_creates_it(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await SessionManager(redis).append_text_turn("sess-1", "sir", "Hi", "Hello, sir.")

    assert _texts(stored_messages(await redis.hgetall(KEY))) == [
        ("request", "Hi"),
        ("response", "Hello, sir."),
    ]


def test_legacy_messages_keep_user_and_assistant_text_only() -> None:
    messages = legacy_messages(
        [
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "ignored"},
            {"role": "assistant", "content": "Good evening, sir."},
        ]
    )

    assert _texts(messages) == [("request", "Hi"), ("response", "Good evening, sir.")]


def test_turn_count_counts_user_prompts_not_tool_returns() -> None:
    assert turn_count(_tool_turn() + text_turn("And now?", "Still on, sir.")) == 2
    assert turn_count([]) == 0


def test_stored_messages_reads_str_or_bytes_fields() -> None:
    payload = ModelMessagesTypeAdapter.dump_json(text_turn("Hi", "Hello")).decode()

    assert len(stored_messages({MESSAGES_FIELD: payload})) == 2
    assert len(stored_messages({MESSAGES_FIELD.encode(): payload.encode()})) == 2
    assert stored_messages({}) == []
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/core/conscious/test_session.py -q -p no:randomly`
Expected: an ImportError collecting the module (`cannot import name 'IDENTITY_FIELD'`).

- [ ] **Step 3: Implement.** Replace `core/conscious/session.py` with the code below. `get_or_create`, `append_turn` and `get_history` are kept verbatim at the bottom until Task 2 removes them.

```python
"""SessionManager — conversation state per session (spec, component 5).

A session is the Redis hash ``alfred:sessions:{id}``: ``channel``, ``created_at``,
``identity`` (who wrote it last) and ``messages``, the last run's whole message history,
tool calls and results included, as ``ModelMessagesTypeAdapter`` JSON. Each turn replaces
``messages``: compaction rewrites older messages during the run, so the run's history is
the session. Sessions expire after the configured idle time.

A turn by a different identity than the one that wrote the session reads no history, so a
shared session (a satellite's ``sat-{name}``) starts over when the speaker changes. A
session written before full history holds a ``history`` field of user and assistant text
instead; it is read as text turns and deleted on the next write.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from shared.streams import SESSIONS_KEY_PREFIX

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from shared.types import AioRedis

logger = logging.getLogger(__name__)

MESSAGES_FIELD: Final = "messages"
IDENTITY_FIELD: Final = "identity"
LEGACY_HISTORY_FIELD: Final = "history"


@dataclass(frozen=True)
class Session:
    """A session as a turn reads it: *messages* is the run's message history."""

    channel: str
    messages: list[ModelMessage]


def text_turn(user: str, reply: str) -> list[ModelMessage]:
    """One turn as text only: the user's words and the reply they were sent."""
    return [ModelRequest(parts=[UserPromptPart(user)]), ModelResponse(parts=[TextPart(reply)])]


def legacy_messages(history: list[dict[str, str]]) -> list[ModelMessage]:
    """A pre-full-history session's text turns as messages. Anything else is skipped."""
    messages: list[ModelMessage] = []
    for turn in history:
        content = turn.get("content", "")
        if turn.get("role") == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content)]))
        elif turn.get("role") == "assistant":
            messages.append(ModelResponse(parts=[TextPart(content)]))
    return messages


def _decoded(raw: Mapping[Any, bytes | str]) -> dict[str, str]:
    return {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }


def stored_messages(raw: Mapping[Any, bytes | str]) -> list[ModelMessage]:
    """A session hash's messages: ``messages`` when present, else the legacy ``history``
    converted. Unreadable JSON is logged and read as no history, so one bad session never
    fails every turn on it."""
    fields = _decoded(raw)
    try:
        if MESSAGES_FIELD in fields:
            return ModelMessagesTypeAdapter.validate_json(fields[MESSAGES_FIELD])
        return legacy_messages(json.loads(fields.get(LEGACY_HISTORY_FIELD) or "[]"))
    except (ValueError, AttributeError, TypeError):
        # ValueError covers pydantic's ValidationError and JSONDecodeError; the other two
        # are a legacy history that is not a list of objects.
        logger.warning("Unreadable session history; reading it as empty", exc_info=True)
        return []


def turn_count(messages: Sequence[ModelMessage]) -> int:
    """User turns in *messages*: requests that carry the user's words."""
    return sum(
        1
        for m in messages
        if isinstance(m, ModelRequest) and any(isinstance(p, UserPromptPart) for p in m.parts)
    )


def _without_instructions(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    """*messages* with every request's instructions dropped: each run sends its own."""
    return [
        replace(m, instructions=None) if isinstance(m, ModelRequest) and m.instructions else m
        for m in messages
    ]


class SessionManager:
    """Manages conversation sessions in Redis (see the module docstring)."""

    def __init__(self, redis: AioRedis, timeout_minutes: int = 30) -> None:
        self._redis = redis
        self._timeout_seconds = timeout_minutes * 60

    def _key(self, session_id: str) -> str:
        return f"{SESSIONS_KEY_PREFIX}{session_id}"

    async def _fields(self, key: str) -> dict[str, str]:
        raw: dict[bytes | str, bytes | str] = await self._redis.hgetall(key)  # type: ignore[misc,unused-ignore]
        return _decoded(raw)

    @staticmethod
    def _history_for(fields: dict[str, str], identity: str) -> list[ModelMessage]:
        """The stored history *identity* may continue: none when someone else wrote it."""
        writer = fields.get(IDENTITY_FIELD)
        if writer is not None and writer != identity:
            logger.info("Session speaker changed; starting without its history")
            return []
        return stored_messages(fields)

    async def load(self, session_id: str, channel: str, identity: str) -> Session:
        """The session a turn by *identity* continues, created when new. Refreshes the TTL."""
        key = self._key(session_id)
        fields = await self._fields(key)
        if fields:
            session = Session(
                channel=fields.get("channel", channel),
                messages=self._history_for(fields, identity),
            )
        else:
            session = Session(channel=channel, messages=[])
            await self._redis.hset(  # type: ignore[misc,unused-ignore]
                key,
                mapping={"channel": channel, "created_at": datetime.now(UTC).isoformat()},
            )
        await self._redis.expire(key, self._timeout_seconds)  # type: ignore[misc,unused-ignore]
        return session

    async def store_run(
        self, session_id: str, messages: Sequence[ModelMessage], identity: str
    ) -> None:
        """Replace the session's history with a finished turn's *messages*, written by
        *identity*. Instructions are dropped first; the legacy text field is deleted."""
        key = self._key(session_id)
        payload = ModelMessagesTypeAdapter.dump_json(_without_instructions(messages)).decode()
        await self._redis.hset(  # type: ignore[misc,unused-ignore]
            key, mapping={MESSAGES_FIELD: payload, IDENTITY_FIELD: identity}
        )
        await self._redis.hdel(key, LEGACY_HISTORY_FIELD)  # type: ignore[misc,unused-ignore]
        await self._redis.expire(key, self._timeout_seconds)  # type: ignore[misc,unused-ignore]

    async def append_text_turn(self, session_id: str, identity: str, user: str, reply: str) -> None:
        """Add a turn known only as text (one answered from its side-effect ledger) to the
        history *identity* may continue, and store the result."""
        fields = await self._fields(self._key(session_id))
        history = self._history_for(fields, identity)
        await self.store_run(session_id, [*history, *text_turn(user, reply)], identity)

    # ── text-only API, removed in Task 2 ────────────────────────────────────────────

    async def get_or_create(self, session_id: str, channel: str) -> dict[str, Any]:
        ...  # keep the current body verbatim

    async def append_turn(self, session_id: str, role: str, content: str) -> None:
        ...  # keep the current body verbatim

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        ...  # keep the current body verbatim
```

  Notes for the implementer:
  - **The three `...` bodies.** Copy the current method bodies from the file you are replacing. They are kept only so the engine still runs until Task 2.
  - **`Any`.** It stays: `stored_messages` and `_decoded` use it in their key type.

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/core/conscious/test_session.py tests/core/conscious/test_engine.py -q -p no:randomly`
Expected: all PASS. The engine tests still use the old API.

- [ ] **Step 5: Gate, secret hygiene, commit.**

```bash
git add core/conscious/session.py tests/core/conscious/test_session.py
git commit -m "feat(sessions): store the run's whole message history per session"
```

---

### Task 2: The engine stores and continues full history

**Files:**
- Modify: `core/conscious/engine.py`:
  - the module docstring;
  - imports, lines 19–27;
  - `session_messages`, lines 88–101 (deleted);
  - `process_request`, lines 263–327;
  - `_run`, lines 369–409;
  - `record_partial_turn`, lines 440–448.
- Modify: `core/conscious/session.py`: delete the three text-only methods.
- Modify: `tests/core/conscious/test_engine.py`, `tests/core/conscious/test_effects.py:601-605`.
- Modify: `docs/llm.md:92,117-119,370`, `docs/architecture.md:668,873`.

**Interfaces:**
- Consumes (Task 1): `Session`, `text_turn`, `stored_messages`, `SessionManager.load` / `store_run` / `append_text_turn`, `SESSIONS_KEY_PREFIX`.
- Produces: `ConsciousEngine._run(request, deps, history: list[ModelMessage], usage) -> tuple[str, Mood, list[ModelMessage]]`. `ConsciousEngine.record_partial_turn(request, text)` keeps its signature, so the runner is unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/core/conscious/test_engine.py`.

  1a. Add these imports beside the existing ones. Drop `session_messages` from the `core.conscious.engine` import, and `call` from `unittest.mock` if nothing else uses it.

```python
from pydantic_ai.messages import ToolReturnPart

from core.conscious.session import (
    LEGACY_HISTORY_FIELD,
    MESSAGES_FIELD,
    Session,
    SessionManager,
    stored_messages,
)
from shared.streams import SESSIONS_KEY_PREFIX
```

  1b. In `_deps`, replace `session_mgr.get_or_create.return_value = {"channel": "web_pwa", "history": []}` with:

```python
    session_mgr.load.return_value = Session(channel="web_pwa", messages=[])
```

  1c. **Mechanical replacements.**

  - `test_a_turn_returns_the_agents_reply_and_stores_both_sides`: replace the `append_turn.assert_has_awaits` with
    ```python
    deps.session_mgr.load.assert_awaited_once_with("sess-1", "web_pwa", "sir")  # type: ignore[attr-defined]
    ((session_id, stored, who), _) = deps.session_mgr.store_run.await_args  # type: ignore[attr-defined]
    assert (session_id, who) == ("sess-1", "sir")
    assert [type(m) for m in stored] == [ModelRequest, ModelResponse]
    assert _user_text(stored[0]) == "Good morning"
    assert stored[1].parts == [TextPart("Good morning, sir.")]
    ```
  - Every `deps.session_mgr.append_turn.assert_awaited()` (lines ~263, 294, 335) becomes `deps.session_mgr.store_run.assert_awaited()`.
  - Every `append_turn.assert_not_awaited()` (lines ~320, 360) becomes `store_run.assert_not_awaited()`.
  - Delete `test_session_messages_keep_user_and_assistant_text_only`. Task 1's `test_legacy_messages_keep_user_and_assistant_text_only` replaces it.
  - In `test_the_session_history_is_the_runs_message_history`, replace the `get_or_create.return_value = {...}` with
    ```python
    deps.session_mgr.load.return_value = Session(  # type: ignore[attr-defined]
        channel="web_pwa",
        messages=[
            ModelRequest(parts=[UserPromptPart("Hi")]),
            ModelResponse(parts=[TextPart("Good evening, sir.")]),
        ],
    )
    ```
  - Replace the two `record_partial_turn` tests (lines ~751–769) with the real-Redis ones in 1d.

  1d. **New tests.** Add them under a new section comment `# ── full session history (spec component 5) ──`. Each one runs real turns through a real `SessionManager` on fakeredis.

```python
def _sessions() -> SessionManager:
    return SessionManager(fakeredis.FakeAsyncRedis())


async def _stored(mgr: SessionManager, session_id: str = "sess-1") -> list[Any]:
    return stored_messages(await mgr._redis.hgetall(f"{SESSIONS_KEY_PREFIX}{session_id}"))


@pytest.mark.asyncio
async def test_a_tool_result_reaches_the_next_turn() -> None:
    registry = AsyncMock()
    registry.get_tools.return_value = [_info("home.get_lights")]
    script = Script(
        tool_call("home_get_lights", {"entity_id": "light.desk"}),
        "The desk lamp is on, sir.",
        "It was on when I looked, sir.",
    )
    deps = _deps(script, tool_registry=registry, domain_router=_router(), session_mgr=_sessions())
    engine = ConsciousEngine(deps=deps)

    await engine.process_request(_request(content="Is the desk lamp on?"))
    await engine.process_request(_request(content="Was it on before?"))

    second_turn = script.requests[2][0]
    returns = [p for m in second_turn for p in m.parts if isinstance(p, ToolReturnPart)]
    assert [r.tool_name for r in returns] == ["home_get_lights"]
    assert _user_text(second_turn[0]) == "Is the desk lamp on?"
    assert _user_text(second_turn[-1]) == "Was it on before?"


@pytest.mark.asyncio
async def test_the_stored_history_drops_instructions_and_each_turn_sends_its_own() -> None:
    script = Script("Good evening, sir.", "Still here, sir.")
    deps = _deps(script, session_mgr=_sessions())
    engine = ConsciousEngine(deps=deps)

    await engine.process_request(_request(content="Hello"))
    stored = await _stored(deps.session_mgr)
    await engine.process_request(_request(content="Are you there?"))

    assert all(m.instructions is None for m in stored if isinstance(m, ModelRequest))
    assert script.instructions(1)  # the second turn still carries the full prompt


@pytest.mark.asyncio
async def test_a_new_speaker_starts_without_the_sessions_history() -> None:
    script = Script("Unlocked, sir.", "Hello.", "Good evening, sir.")
    deps = _deps(script, session_mgr=_sessions())
    engine = ConsciousEngine(deps=deps)
    request = {"session_id": "sat-kitchen", "channel": "satellite"}

    await engine.process_request(_request(content="Unlock the front door", **request))
    deps.identity_gate.resolve.return_value = identity("guest")  # type: ignore[attr-defined]
    await engine.process_request(_request(content="Hi there", **request))
    deps.identity_gate.resolve.return_value = identity("sir")  # type: ignore[attr-defined]
    await engine.process_request(_request(content="Evening", **request))

    assert [_user_text(m) for m in script.requests[1][0]] == ["Hi there"]
    sir_again = [m for m in script.requests[2][0] if isinstance(m, ModelRequest)]
    assert [_user_text(m) for m in sir_again] == ["Evening"]


@pytest.mark.asyncio
async def test_a_legacy_text_session_is_continued_then_rewritten() -> None:
    mgr = _sessions()
    history = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello, sir."}]
    await mgr._redis.hset(
        f"{SESSIONS_KEY_PREFIX}sess-1",
        mapping={"channel": "web_pwa", LEGACY_HISTORY_FIELD: json.dumps(history)},
    )
    script = Script("It is 3pm, sir.")
    deps = _deps(script, session_mgr=mgr)

    await ConsciousEngine(deps=deps).process_request(_request(content="What time is it?"))

    assert [type(m) for m in script.requests[0][0]] == [ModelRequest, ModelResponse, ModelRequest]
    raw = await mgr._redis.hgetall(f"{SESSIONS_KEY_PREFIX}sess-1")
    assert LEGACY_HISTORY_FIELD.encode() not in raw
    assert MESSAGES_FIELD.encode() in raw


@pytest.mark.asyncio
async def test_a_turn_without_its_own_answer_stores_the_turn_as_text() -> None:
    """The request limit stops a run mid-tool-loop; the stored history must not end in a
    tool call, and the next turn sees what the user was told."""
    registry = AsyncMock()
    registry.get_tools.return_value = [_info("home.get_lights")]
    calls = [
        tool_call("home_get_lights", {"entity_id": "light.desk"}) for _ in range(REQUEST_LIMIT)
    ]
    script = Script(*calls, "Sorry about that, sir.")
    deps = _deps(script, tool_registry=registry, domain_router=_router(), session_mgr=_sessions())
    engine = ConsciousEngine(deps=deps)

    await engine.process_request(_request(content="Check the lights"))
    await engine.process_request(_request(content="Well?"))

    second_turn = script.requests[REQUEST_LIMIT][0]
    assert [type(m) for m in second_turn] == [ModelRequest, ModelResponse, ModelRequest]
    assert second_turn[1].parts == [TextPart(DELIBERATING_REPLY)]


async def test_record_partial_turn_appends_the_turn_to_the_session() -> None:
    script = Script("Good evening, sir.")
    deps = _deps(script, session_mgr=_sessions())
    engine = ConsciousEngine(deps=deps)
    await engine.process_request(_request(content="Hello"))

    await engine.record_partial_turn(
        _request(content="turn on the porch light"), "I did some of it, sir."
    )

    stored = await _stored(deps.session_mgr)
    assert [_user_text(m) for m in stored if isinstance(m, ModelRequest)] == [
        "Hello",
        "turn on the porch light",
    ]
    assert stored[-1].parts == [TextPart("I did some of it, sir.")]


async def test_record_partial_turn_on_an_expired_session_starts_it() -> None:
    deps = _deps(session_mgr=_sessions())

    await ConsciousEngine(deps=deps).record_partial_turn(_request(content="lights"), "Done.")

    stored = await _stored(deps.session_mgr)
    assert [type(m) for m in stored] == [ModelRequest, ModelResponse]


async def test_record_partial_turn_logs_rather_than_raises_a_session_failure() -> None:
    deps = _deps()
    deps.session_mgr.append_text_turn.side_effect = ConnectionError("gone")  # type: ignore[attr-defined]

    await ConsciousEngine(deps=deps).record_partial_turn(_request(), "partial")
```

  Add `import json` to the module if it is not already imported.

  1e. In `tests/core/conscious/test_effects.py` (~line 601), replace the `get_history` assertion with:

```python
    stored = stored_messages(await redis.hgetall(f"{SESSIONS_KEY_PREFIX}sess-1"))
    assert [type(m).__name__ for m in stored] == ["ModelRequest", "ModelResponse"]
    assert stored[0].parts[0].content == "turn on the porch light"
    assert stored[1].parts[0].content == _PORCH_REPLY
```

  Import `stored_messages` from `core.conscious.session` and `SESSIONS_KEY_PREFIX` from `shared.streams`, unless the module already imports it. Keep the `SessionManager` import, since `_engine` uses it.

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/core/conscious/test_engine.py tests/core/conscious/test_effects.py -q -p no:randomly`
Expected: FAIL. The engine still calls `get_or_create`, so `test_a_tool_result_reaches_the_next_turn` sees no tool return and the mocked `store_run` is never awaited.

- [ ] **Step 3: Implement** in `core/conscious/engine.py`.

  3a. **Imports.** Trim `pydantic_ai.messages` to `ModelMessage` (TYPE_CHECKING is fine, since it is only used in annotations under `from __future__ import annotations`). Add `from core.conscious.session import text_turn` at runtime. Delete `session_messages` entirely. Add `from core.identity.schemas import IdentityResult` under TYPE_CHECKING.

  3b. **Module docstring.** Change "runs the agent over the session's history." to "runs the agent over the session's whole message history, then stores the run's messages back (``core.conscious.session``)."

  3c. **`_resolve`.** Add it and use it in `process_request`:

```python
    def _resolve(self, request: UserRequest) -> IdentityResult:
        """Who sent *request*, by the identity gate."""
        return self._identity_gate.resolve(
            channel=request.channel,
            identity_claim=request.identity_claim,
            authenticated=request.authenticated,
            identity_confidence=request.identity_confidence,
        )
```

  3d. **`process_request`.** Replace `identity = self._identity_gate.resolve(...)` with `identity = self._resolve(request)`. Replace the session lines:

```python
        session = await self._session_mgr.load(
            request.session_id, request.channel, identity.identity
        )
```

  and, after `TurnDeps(...)`:

```python
        usage = RunUsage()
        text, mood, messages = await self._run(request, deps, session.messages, usage)

        await self._session_mgr.store_run(request.session_id, messages, identity.identity)
        await self._note(now, request.content, text, deps.actions_taken, usage)
        return self._reply(request, text, deps.actions_taken, mood=mood)
```

  3e. **`_run`.**

```python
    async def _run(
        self,
        request: UserRequest,
        deps: TurnDeps,
        history: list[ModelMessage],
        usage: RunUsage,
    ) -> tuple[str, Mood, list[ModelMessage]]:
        """One agent run: its reply, its mood and the session's history after it.

        A finished run's history is ``all_messages()``, compacted during the run. A run
        that ends without its own answer leaves *history* plus the turn as text, never its
        half-finished messages. *usage* accumulates across it, for the note.
        """
        model = self._model
        if model is None:
            return self._ended(request, history, NOT_CONFIGURED_REPLY, "neutral")
        try:
            with (
                langfuse_attributes(
                    session_id=request.session_id, user_id=deps.who, tags=[request.channel]
                ),
                linked_prompts(*deps.prompts.refs()),
            ):
                result = await self._agent.run(
                    request.content,
                    deps=deps,
                    message_history=history,
                    model=model,
                    usage=usage,
                    usage_limits=UsageLimits(request_limit=REQUEST_LIMIT),
                )
            return result.output, "neutral", result.all_messages()
        except SpendLimitExceeded:
            # (keep the two existing comment lines)
            logger.warning("Daily budget spent during the run — returning the budget reply")
            return self._ended(request, history, await self._budget_reply(request), "concerned")
        except UsageLimitExceeded:
            logger.warning("System 2 used its %d model requests without answering", REQUEST_LIMIT)
            return self._ended(request, history, DELIBERATING_REPLY, "neutral")
        except UnexpectedModelBehavior as exc:
            # (keep the three existing comment lines)
            logger.warning("System 2 gave up on the model's replies: %s", exc)
            return self._ended(request, history, DELIBERATING_REPLY, "neutral")

    @staticmethod
    def _ended(
        request: UserRequest, history: list[ModelMessage], text: str, mood: Mood
    ) -> tuple[str, Mood, list[ModelMessage]]:
        """A turn answered with *text* instead of the run's own answer."""
        return text, mood, [*history, *text_turn(request.content, text)]
```

  3f. **`record_partial_turn`.**

```python
    async def record_partial_turn(self, request: UserRequest, text: str) -> None:
        """Store a turn answered from its side-effect ledger (*text*) in the session, as
        text after the stored history, so the next turn knows what was done. Called once
        the answer is published; a failed write is logged, not raised."""
        try:
            who = self._resolve(request).identity
            await self._session_mgr.append_text_turn(
                request.session_id, who, request.content, text
            )
        except Exception:
            logger.warning("Failed to store a partial turn in the session", exc_info=True)
```

  3g. **`core/conscious/session.py`.** Delete the "text-only API" section (`get_or_create`, `append_turn`, `get_history`). `Any` stays imported (Task 1's key types).

  3h. **Docs.**
  - `docs/llm.md:92`, the sequence-diagram line `E->>E: store session text, scratchpad note`, becomes `E->>E: store the session's messages, scratchpad note`.
  - `docs/llm.md:117-119`: replace the paragraph starting "**Sessions** stay text-only in this PR" with:

    ```markdown
    **Sessions** keep the whole conversation: the run's message history, tool calls and
    results included, is stored in the session (`core/conscious/session.py`) and is the next
    turn's history. Each turn replaces it, since compaction (below) rewrites older messages
    during the run. Instructions are not stored; every run sends its own. A turn by a
    different identity than the one that wrote the session starts with no history, so a
    satellite's shared session starts over when the speaker changes. A run that ends without
    its own answer (the deliberating or budget reply) stores the turn as text. Sessions expire
    after `SESSION_TIMEOUT_MINUTES` (default 30) idle.
    ```
  - `docs/llm.md:370`: "and the turn is stored in the session once the answer is published" becomes "and the turn is stored in the session, as text after its history, once the answer is published".
  - `docs/architecture.md:668`: replace the SessionManager bullet's last sentence with "Stores each run's whole message history (tool calls and results included) per session, with compaction bounding it (`core/conscious/history.py`); a change of speaker starts the session over."
  - `docs/architecture.md:873`: the row becomes `| \`alfred:sessions:{id}\` | Hash | Conversation session: \`channel\`, \`created_at\`, \`identity\`, \`messages\` (the run's message history as JSON; TTL \`SESSION_TIMEOUT_MINUTES\`) |`.

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/core/conscious tests/integration/test_conscious_pipeline.py -q -p no:randomly`
Expected: all PASS. Then confirm nothing outside these tests still calls the old API: `git grep -n "get_or_create\|append_turn\|get_history\|session_messages" -- '*.py'` should print nothing.

- [ ] **Step 5: Gate, secret hygiene, commit.**

```bash
git add core/conscious/engine.py core/conscious/session.py tests/core/conscious docs/llm.md docs/architecture.md
git commit -m "feat(conscious): continue each session with its full message history"
```

---

### Task 3: Bound the history: truncate, clear, slide

**Files:**
- Create: `core/conscious/history.py`
- Modify: `core/conscious/agent.py` (imports, module docstring, `build_system2_agent`'s capability list)
- Test: `tests/core/conscious/test_engine.py` (a new section)
- Modify: `docs/llm.md` (one paragraph after Task 2's Sessions paragraph)

**Interfaces:**
- Consumes (Task 2): the engine stores `result.all_messages()`. Test helpers `_sessions()` and `_stored()` from Task 2's tests, plus `SessionManager.store_run`.
- Produces:
  - constants `TOOL_RESULT_MAX_CHARS`, `CLEAR_TOOL_RESULTS_OVER_TOKENS`, `TOOL_RESULTS_KEPT`, `CLEAR_AT_LEAST_TOKENS`, `SESSION_MAX_MESSAGES`, `SESSION_KEPT_MESSAGES`;
  - `class TruncateToolOutput(ToolOutputLimits[TurnDeps])`;
  - `def history_capabilities() -> list[AbstractCapability[TurnDeps]]`.

- [ ] **Step 1: Write the failing tests.** Append them to `tests/core/conscious/test_engine.py`, under `# ── history bounds (spec component 5) ──`:

```python
from pydantic_ai.messages import ToolCallPart

from core.conscious.history import (
    CLEAR_TOOL_RESULTS_OVER_TOKENS,
    SESSION_MAX_MESSAGES,
    TOOL_RESULT_MAX_CHARS,
    TOOL_RESULTS_KEPT,
)
from core.conscious.session import text_turn
```

(Hoist these imports to the top of the module with the others.)

```python
@pytest.mark.asyncio
async def test_no_spilled_result_reader_is_offered() -> None:
    script = Script("Hello, sir.")

    await ConsciousEngine(deps=_deps(script)).process_request(_request())

    assert "read_tool_result" not in script.offered(0)


@pytest.mark.asyncio
async def test_an_oversized_tool_result_is_truncated_before_the_model_and_the_store() -> None:
    registry = AsyncMock()
    registry.get_tools.return_value = [_info("home.get_live_state")]
    router = AsyncMock()
    router.route.return_value = MagicMock(
        status="success", result={"entities": "x" * 150_000}, error=None
    )
    script = Script(tool_call("home_get_live_state", {"entity_id": "all"}), "All quiet, sir.")
    deps = _deps(script, tool_registry=registry, domain_router=router, session_mgr=_sessions())

    await ConsciousEngine(deps=deps).process_request(_request(content="What's on?"))

    [seen] = script.returns(1)
    assert len(seen) <= TOOL_RESULT_MAX_CHARS
    stored = await _stored(deps.session_mgr)
    [kept] = [p for m in stored for p in m.parts if isinstance(p, ToolReturnPart)]
    assert len(kept.model_response_str()) <= TOOL_RESULT_MAX_CHARS


@pytest.mark.asyncio
async def test_a_long_session_is_compacted_before_it_is_stored() -> None:
    mgr = _sessions()
    long_history = [m for i in range(40) for m in text_turn(f"question {i}", f"answer {i}")]
    await mgr.store_run("sess-1", long_history, "sir")
    script = Script("The latest answer, sir.")
    deps = _deps(script, session_mgr=mgr)

    await ConsciousEngine(deps=deps).process_request(_request(content="latest question"))

    assert len(script.requests[0][0]) <= SESSION_MAX_MESSAGES
    stored = await _stored(mgr)
    assert len(stored) <= SESSION_MAX_MESSAGES
    assert _user_text(stored[0]) == "question 0"  # the first user message is kept
    assert stored[-1].parts == [TextPart("The latest answer, sir.")]


@pytest.mark.asyncio
async def test_old_tool_results_are_cleared_past_the_token_ceiling() -> None:
    mgr = _sessions()
    pairs = TOOL_RESULTS_KEPT + 1
    # Each result alone is under the ceiling; together they are well over it (~4 chars/token).
    size = CLEAR_TOOL_RESULTS_OVER_TOKENS * 4 // TOOL_RESULTS_KEPT
    history = []
    for i in range(pairs):
        history += [
            ModelRequest(parts=[UserPromptPart(f"check {i}")]),
            ModelResponse(
                parts=[ToolCallPart("home_get_lights", {"entity_id": "light.desk"}, f"c{i}")]
            ),
            ModelRequest(parts=[ToolReturnPart("home_get_lights", "y" * size, f"c{i}")]),
            ModelResponse(parts=[TextPart(f"done {i}")]),
        ]
    await mgr.store_run("sess-1", history, "sir")
    script = Script("Fine, sir.")
    deps = _deps(script, session_mgr=mgr)

    await ConsciousEngine(deps=deps).process_request(_request(content="and now?"))

    sent = [p.content for m in script.requests[0][0] for p in m.parts if isinstance(p, ToolReturnPart)]
    assert sent[0] == "[tool result cleared]"
    assert all(c == "y" * size for c in sent[1:])
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/core/conscious/test_engine.py -q -p no:randomly -k "spilled or oversized or compacted or cleared"`
Expected: an ImportError (`No module named 'core.conscious.history'`).

- [ ] **Step 3: Implement.** Create `core/conscious/history.py`:

```python
"""System 2's history bounds (spec, component 5): what keeps a session's history, and any
one tool result in it, inside the model's context window.

Measured in production over a week of traces: a model request's input is about 18k tokens
at the median and under 25k at most, nearly all of it instructions and tool definitions; a
session holds one or two turns; the largest tool result is an unfiltered live-state read, a
little under 80,000 characters. The smallest window an eval runs on is 32,768 tokens.

The capabilities run cheapest first. ``TruncateToolOutput`` clips a result as it is made, so
the clipped copy is what the model sees and what the session stores. ``ClearToolResults``
blanks old results once the context estimate (instructions and tool definitions included)
passes its ceiling, never the most recent pairs. ``SlidingWindowCompaction`` drops the
oldest messages past a count, keeping the first user message and tool call/return pairs
together. Both compactions rewrite the run's messages, which is why a turn replaces the
stored session rather than appending to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from pydantic_ai_harness.compaction import ClearToolResults, SlidingWindowCompaction
from pydantic_ai_harness.tool_output_limits import Band, ToolOutputLimits, Truncate

from core.conscious.deps import TurnDeps

if TYPE_CHECKING:
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.toolsets import AgentToolset

# Above the largest real result: a ceiling against a runaway one, not a trim.
TOOL_RESULT_MAX_CHARS: Final = 100_000
# Context estimate past which old tool results are blanked; leaves room to answer on a
# 32k-token window.
CLEAR_TOOL_RESULTS_OVER_TOKENS: Final = 28_000
# The most recent tool call/return pairs, never blanked: what a follow-up refers to.
TOOL_RESULTS_KEPT: Final = 3
# Never blank for less than this.
CLEAR_AT_LEAST_TOKENS: Final = 2_000
SESSION_MAX_MESSAGES: Final = 60
SESSION_KEPT_MESSAGES: Final = 40


class TruncateToolOutput(ToolOutputLimits[TurnDeps]):
    """``ToolOutputLimits`` that only truncates. It never spills, so it registers no
    ``read_tool_result`` tool: the catalog stays the agent's one toolset."""

    def get_toolset(self) -> AgentToolset[TurnDeps] | None:
        return None


def history_capabilities() -> list[AbstractCapability[TurnDeps]]:
    """The bounds, cheapest first (see the module docstring)."""
    return [
        TruncateToolOutput(
            bands=[
                Band(over=TOOL_RESULT_MAX_CHARS, action=Truncate(max_chars=TOOL_RESULT_MAX_CHARS))
            ]
        ),
        ClearToolResults(
            max_tokens=CLEAR_TOOL_RESULTS_OVER_TOKENS,
            keep_pairs=TOOL_RESULTS_KEPT,
            min_clear_tokens=CLEAR_AT_LEAST_TOKENS,
        ),
        SlidingWindowCompaction(
            max_messages=SESSION_MAX_MESSAGES, keep_messages=SESSION_KEPT_MESSAGES
        ),
    ]
```

  In `core/conscious/agent.py`:
  - add `from core.conscious.history import history_capabilities`;
  - build the capability list as below. The existing three keep their relative order; the bounds go before instrumentation, so their compaction spans nest in the run like the others.

```python
    capabilities: list[AbstractCapability[TurnDeps]] = [
        RepairToolArguments(),
        RecordEffects(),
        *history_capabilities(),
        instrumentation(),
    ]
```

  - Extend the module docstring by one sentence: "``history_capabilities`` (``core.conscious.history``) bounds the session's history and each tool result in it."
  - Add to `docs/llm.md`, right after Task 2's Sessions paragraph:

    ```markdown
    **Compaction** (`core/conscious/history.py`) keeps that history inside the context window,
    cheapest first: one tool result is clipped at 100,000 characters as it is made; once the
    context estimate passes 28,000 tokens, tool results older than the three most recent are
    replaced by `[tool result cleared]`; past 60 messages, the oldest are dropped down to 40,
    keeping the first user message and tool call/return pairs together. The numbers come from
    a week of production traces and the 32k-token eval model; the module docstring has them.
    ```

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/core/conscious -q -p no:randomly`
Expected: all PASS.

  If `test_an_oversized_tool_result_is_truncated_before_the_model_and_the_store` sees more than `TOOL_RESULT_MAX_CHARS`, the catalog has wrapped the result after truncation. Find where in `CatalogToolset` / `RegistryToolset`. Do not raise the constant. Report it as DONE_WITH_CONCERNS with the measured length.

- [ ] **Step 5: Gate, secret hygiene, commit.**

```bash
git add core/conscious/history.py core/conscious/agent.py tests/core/conscious/test_engine.py docs/llm.md
git commit -m "feat(conscious): bound session history with truncation and compaction"
```

---

### Task 4: Admin session list counts turns from messages

**Files:**
- Modify: `core/channels/admin_api.py:457-480` (plus imports)
- Test: `tests/core/channels/test_admin_api.py:665-697`
- Modify: `docs/admin-api.md:305-313`

**Interfaces:**
- Consumes (Task 1): `stored_messages(raw) -> list[ModelMessage]`, `turn_count(messages) -> int`, `MESSAGES_FIELD`, `text_turn`.

- [ ] **Step 1: Write the failing tests.** In `tests/core/channels/test_admin_api.py`:

  1. In `test_sessions_list_populated`, change the expected `turns` from `3` to `2` and its docstring to "…channel, turns (user turns, from a legacy text history), created_at, and ttl populated." The legacy `[user, assistant, user]` is two user turns (Ruling 9).
  2. Add:

```python
def test_sessions_list_counts_user_turns_from_messages() -> None:
    r = _overview_redis()
    r.scan_iter = MagicMock(return_value=aiter_values([b"alfred:sessions:s3"]))
    messages = [
        *text_turn("Is the lamp on?", "Checking."),
        ModelRequest(parts=[ToolReturnPart("home_get_lights", {"state": "on"}, "c1")]),
        ModelResponse(parts=[TextPart("It is on, sir.")]),
        *text_turn("Thanks", "My pleasure, sir."),
    ]
    session = session_hgetall()

    async def _hgetall(key: str) -> dict[bytes, bytes]:
        if session_data := await session(key):
            return session_data
        return {
            b"channel": b"web_pwa",
            MESSAGES_FIELD.encode(): ModelMessagesTypeAdapter.dump_json(messages),
            b"created_at": b"2026-10-15T10:00:00+00:00",
        }

    r.hgetall = AsyncMock(side_effect=_hgetall)
    r.ttl = AsyncMock(return_value=600)
    client = make_admin_client(r)

    [entry] = client.get("/api/admin/sessions").json()["sessions"]

    assert entry["turns"] == 2


def test_sessions_list_counts_unreadable_messages_as_zero_turns() -> None:
    r = _overview_redis()
    r.scan_iter = MagicMock(return_value=aiter_values([b"alfred:sessions:s4"]))
    session = session_hgetall()

    async def _hgetall(key: str) -> dict[bytes, bytes]:
        if session_data := await session(key):
            return session_data
        return {b"channel": b"signal", MESSAGES_FIELD.encode(): b"not json"}

    r.hgetall = AsyncMock(side_effect=_hgetall)
    r.ttl = AsyncMock(return_value=600)
    client = make_admin_client(r)

    [entry] = client.get("/api/admin/sessions").json()["sessions"]

    assert entry["turns"] == 0
```

  with these imports at the top:

```python
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
)

from core.conscious.session import MESSAGES_FIELD, text_turn
```

- [ ] **Step 2: Run the tests to verify they fail.**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/core/channels/test_admin_api.py -q -p no:randomly -k sessions`
Expected: FAIL. `turns` is 3 for the legacy test and 0 for the messages test.

- [ ] **Step 3: Implement.** In `core/channels/admin_api.py`, import `from core.conscious.session import stored_messages, turn_count`, then replace the history block in `sessions()`:

```python
            data = _decode_hash(await r.hgetall(key_str))
            turns = turn_count(stored_messages(data))
```

  Drop the `try/except` around the old count; `stored_messages` already reads unreadable JSON as empty. Remove `json` from the imports only if nothing else in the file uses it (other endpoints do). In `docs/admin-api.md:307-308`, change "counts conversation turns from the JSON `history` field" to "counts the user turns in the stored `messages` (or, for a session written before full history, its text `history`)".

- [ ] **Step 4: Run the tests to verify they pass.**

Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest tests/core/channels/test_admin_api.py -q -p no:randomly`
Expected: all PASS.

- [ ] **Step 5: Gate, secret hygiene, commit.**

```bash
git add core/channels/admin_api.py tests/core/channels/test_admin_api.py docs/admin-api.md
git commit -m "feat(admin): count session turns from the stored messages"
```

---

### Task 5: Live verification (controller, not a subagent)

Run by the controller, and **posted to #317 as raw output** (scorecards, the Redis read, the Langfuse observation list), not a summary.

**vLLM bound:** the eval harness's own concurrency, at most 4 requests in flight. Read Langfuse's keys into the shell from `~/code/langfuse/.env` and never echo them.

- [ ] **Step 1: Multi-turn evals, branch versus master.**
  1. Run `ALFRED_EVALS_HOME_SERVICE=<evals-main worktree> uv run --no-sync alfred evals run conversation home_control --epochs 5` from this worktree.
  2. Run the same command from a detached worktree at `5921523` (`git worktree add --detach ~/code/.worktrees/alfred/evals-base 5921523`; remove it afterwards).
  3. Compare per-golden pass rates. Expected:
     - `conversation.session.followup_refers_back`, `conversation.session.recall_within_session` and `conversation.signal_multi_turn` match or beat master;
     - nothing else drops beyond run-to-run noise (vLLM is not deterministic at T=0, so judge by rates over the 5 epochs, not single samples);
     - lock goldens fail as they do on master (#292).
- [ ] **Step 2: One real multi-turn session on the eval stack.**
  1. Using `--keep` on a branch run, send two turns on one session id through the kept stack: "Is the bedroom lamp on?", then "Turn it off."
  2. Read the session hash with `redis-cli HGET alfred:sessions:<id> messages` inside the container. Confirm it holds the first turn's tool call and return, and no `instructions`.
  3. If the kept stack exports to Langfuse, list the second turn's `chat` generation with the v2 observations API and confirm its input includes the first turn's tool result. If it does not export, the Redis read is the evidence. Say which in the post.
- [ ] **Step 3: Close out.** Run the gate and the secret-hygiene check (the runbook's §Secret hygiene, run from there, never quoted). Report to the owner and ask before pushing or opening the PR.

## After merge (owner's go only)

Merging deploys. Live sessions keep their legacy `history` for up to 30 minutes. Each one is converted on its next turn, and the rest simply expire. Rollback loses at most 30 minutes of session history: the old code reads `history`, which converted sessions no longer have, so they start fresh.

After the deploy:
1. Send two real turns on one PWA session, the second referring to the first's tool result.
2. Confirm the second `chat` observation in Langfuse carries the first turn's tool return.
3. Post it to #317.
