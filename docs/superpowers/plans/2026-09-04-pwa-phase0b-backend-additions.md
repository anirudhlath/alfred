# PWA Phase 0b — Backend Additions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add every backend read and write the PWA design assumes (spec §10) so that Phases 1–4 of the client never have to touch the server.

**Architecture:** Small, additive changes that follow existing conventions: two new fields on bus/SDK schemas (`ActionRequest.reason`, `CostState.request_count`), pending-action reads next to the existing confirm route, richer auth sessions plus session/passkey/pairing management on the existing WebAuthn router, four new admin overview fields fed from Redis keys the daemons already own (plus one new hash for the Librarian), and an attention-set read/write pair on the admin router. No new services, no new storage engines, no schema migrations — new hash fields default when absent.

**Tech Stack:** Python 3.13, FastAPI, pydantic v2, redis.asyncio, loguru/stdlib logging (per module), pytest + pytest-asyncio (`asyncio_mode = auto`), ruff (line-length 100), mypy `--strict`.

**Spec:** `docs/superpowers/specs/2026-09-04-mobile-first-pwa-client-design.md` §10 ("Handoff gaps and backend additions"). Web Push (spec §6) is **out of scope** here — it is Phase 5.

---

## Before you start

1. **Plan 0 must be merged first.** This plan assumes `core/identity/auth_routes.py` already has `_AUTH_SESSION_TTL = 8 * 3600` and that `create_admin_router()` is gated by `require_authenticated` only (`docs/superpowers/plans/2026-09-04-pwa-phase0-security.md`). If `grep -n "_AUTH_SESSION_TTL = " core/identity/auth_routes.py` does not print `8 * 3600`, stop and merge plan 0.

2. **Work in a fresh worktree** — never commit from `~/code/alfred-deploy/alfred` (the live checkout):

```bash
cd ~/code/alfred-deploy/alfred
git fetch origin
git worktree add ~/code/.worktrees/alfred/pwa-phase0b-backend -b feat/pwa-phase0b-backend origin/master
cd ~/code/.worktrees/alfred/pwa-phase0b-backend
uv sync
uv run pytest -q
```

Expected: the last line reads `NNNN passed` with 0 failures (the count changes over time; failures mean the checkout is broken — fix that before continuing).

3. **Conventions you must follow** (from `CLAUDE.md` and `.claude/rules/python-conventions.md`):
   - Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/` before every commit. All must be clean.
   - Logging style is **per module**: `core/channels/*`, `core/identity/*`, `core/routing/*`, `core/reflex/*` use loguru with brace placeholders (`logger.info("x {}", y)`); `core/conscious/cost.py`, `core/librarian/consolidator.py` and `core/librarian/scheduler.py` use stdlib `logging` with `%s` placeholders. Match the file you are in.
   - `xrevrange` must go through `revrange()` in `shared/redis_streams.py` (it owns the `type: ignore`). Never call `redis.xrevrange` directly in new code.
   - Every new hash field is read with a default so existing Redis data keeps working.
   - Conventional-commit messages (`feat:`, `docs:`, `test:`); a `pr-title` check enforces this on the PR title too.
   - **The repo is public.** No hostnames, IPs or secrets in code, tests, docs or commits.

4. **How the tests are wired.** `tests/core/channels/conftest.py::web_client` gives you a `TestClient` with an `AsyncMock` Redis whose `hgetall` returns an authenticated session for the `alfred_auth` cookie it sets. `tests/core/channels/test_admin_api.py::make_admin_client(mock_redis, *, authed=True)` does the same for the admin router. `tests/core/identity/test_auth_routes.py` builds a bare FastAPI app with `create_auth_router` and **no** `AuthCookieMiddleware`, so auth-route dependencies must read Redis themselves rather than trust `request.state`.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `bus/schemas/events.py` | Modify | `ActionRequest.reason` |
| `sdk/alfred_sdk/events.py` | Modify | SDK mirror of `ActionRequest.reason` |
| `core/routing/domain_router.py` | Modify | Put `reason` in the confirmation notification metadata |
| `core/conscious/engine.py` | Modify | Offer a `reason` parameter on critical tools; move it from `parameters` onto the action |
| `core/routing/pending.py` | Modify | `get_pending_action`, `list_pending_actions`, `pending_action_payload` |
| `core/channels/web_server.py` | Modify | `GET /api/actions/pending`, `GET /api/actions/{request_id}`, `latency_ms` on service/integration status |
| `core/conscious/cost.py` | Modify | `CostState.request_count` + computed `avg_usd` |
| `core/memory/schemas.py` | Modify | `RoutineSpec.confidence_history` |
| `core/librarian/consolidator.py` | Modify | Maintain `confidence_history`; record `alfred:librarian:status` |
| `core/librarian/scheduler.py` | Modify | Record `next_run_at` after every cycle |
| `shared/streams.py` | Modify | `LIBRARIAN_STATUS_KEY`, `WEBAUTHN_PAIRING_KEY`, `WEBAUTHN_PAIRING_FAILS_KEY` |
| `shared/redis_streams.py` | Modify | `revrange()` gains `max_id`/`min_id` |
| `core/channels/stream_catalog.py` | Modify | `rate_5m` per stream |
| `core/channels/admin_api.py` | Modify | Overview `reflex` + `librarian`; `GET/PUT /api/admin/attention` |
| `core/reflex/attention.py` | Modify | `attention_seen_list`, `attention_domains` |
| `core/identity/auth_routes.py` | Modify | Session metadata, sessions/passkeys management, `logout?all=1`, pairing code, registration gate |
| `docs/webauthn.md`, `docs/admin-api.md`, `docs/autonomy.md`, `docs/secrets.md`, `docs/architecture.md`, `docs/PRD.md`, `CLAUDE.md` | Modify | Keep the docs current |
| `tests/bus/test_action_confirmed.py` | Modify | `reason` roundtrip |
| `tests/core/routing/test_router_enforcement.py` | Modify | `reason` in notification metadata |
| `tests/core/conscious/test_engine.py` | Modify | `reason` parameter offered + moved |
| `tests/core/routing/test_pending.py` | Modify | Pending reads |
| `tests/core/channels/test_actions_confirm.py` | Modify | Pending read routes |
| `tests/core/conscious/test_cost.py` | Modify | `request_count` / `avg_usd` |
| `tests/core/librarian/test_consolidator_v2.py` | Modify | `confidence_history`, status hash |
| `tests/core/librarian/test_scheduler.py` | Modify | `record_next_run` |
| `tests/shared/test_redis_streams_revrange.py` | Create | `revrange` kwargs |
| `tests/core/channels/test_stream_catalog.py` | Modify | `rate_5m` |
| `tests/core/channels/test_admin_api.py` | Modify | Overview fields, attention endpoints |
| `tests/core/channels/test_service_integrations_api.py` | Modify | `latency_ms` |
| `tests/core/reflex/test_attention.py` | Modify | `attention_seen_list`, `attention_domains` |
| `tests/core/identity/test_auth_routes.py` | Modify | Session metadata, sessions, passkeys, pairing, gate |

---

### Task 1: `ActionRequest.reason` — from the model, through the router, to the prompt

The Door shows *why* Alfred wants to do something. Today the model has no way to say why: `ActionRequest` has no field for it. This task adds the field to the bus schema and the SDK mirror, gives the model a `reason` argument on every critical tool, moves that argument off `parameters` (so the domain service never sees it) and puts it in the confirmation notification metadata.

**Files:**
- Modify: `bus/schemas/events.py:36-44`
- Modify: `sdk/alfred_sdk/events.py:39-47`
- Modify: `core/routing/domain_router.py:101-128` (`_intercept_critical`)
- Modify: `core/conscious/engine.py:228-260` (`_tools_to_openai_format`, `_INTEGRATION_PREFIX`) and `core/conscious/engine.py:423-424, 476-494` (`_dispatch_tool_call`)
- Test: `tests/bus/test_action_confirmed.py`
- Test: `tests/core/routing/test_router_enforcement.py:101-119`
- Test: `tests/core/conscious/test_engine.py`

- [ ] **Step 1: Write the failing schema test**

Append to `tests/bus/test_action_confirmed.py`:

```python
def test_reason_defaults_to_none_and_roundtrips_bus_to_sdk() -> None:
    """`reason` is optional, and survives the bus → SDK → bus JSON roundtrip."""
    from bus.schemas.events import ActionRequest as BusAction
    from sdk.alfred_sdk.events import ActionRequest as SdkAction

    bare = BusAction(
        source="conscious-engine", target_service="home-service", tool_name="home.unlock_door"
    )
    assert bare.reason is None

    bus_action = BusAction(
        source="conscious-engine",
        target_service="home-service",
        tool_name="home.unlock_door",
        parameters={"entity_id": "lock.front_door"},
        reason="You asked me to let the dog walker in at 3pm.",
    )
    sdk_action = SdkAction.model_validate_json(bus_action.model_dump_json())
    assert sdk_action.reason == "You asked me to let the dog walker in at 3pm."
    back = BusAction.model_validate_json(sdk_action.model_dump_json())
    assert back.reason == bus_action.reason
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/bus/test_action_confirmed.py -v -k reason`
Expected: FAIL with `ValidationError` / `Unexpected keyword argument 'reason'` (pydantic rejects the unknown field).

- [ ] **Step 3: Add the field to both schemas**

In `bus/schemas/events.py`, inside `class ActionRequest(BaseEvent)`, add the line after `parameters`:

```python
    parameters: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None  # why the actor wants this — shown on the confirmation prompt
    confirmed: bool = False  # set True only by the confirmation flow (contract C3)
```

Make the identical edit in `sdk/alfred_sdk/events.py` inside its `class ActionRequest(BaseEvent)` (the SDK mirror must stay field-for-field identical with the bus schema).

- [ ] **Step 4: Run the schema tests**

Run: `uv run pytest tests/bus/test_action_confirmed.py -v`
Expected: all PASS (the new test plus the existing `confirmed` roundtrips).

- [ ] **Step 5: Write the failing router test**

In `tests/core/routing/test_router_enforcement.py`, add one assertion at the end of `test_critical_unconfirmed_intercepted` (after the `parameters` assertion on line 119):

```python
    assert pub_kwargs["metadata"]["parameters"] == {"room": "living_room"}
    assert pub_kwargs["metadata"]["reason"] is None  # _action() sets no reason
```

And add a new test right after it:

```python
@pytest.mark.asyncio
async def test_critical_intercept_forwards_reason_to_notification() -> None:
    router, _agent, _redis, notifier = _router()
    action = _action("conscious-engine", "home.unlock_door").model_copy(
        update={"reason": "The dog walker is at the door."}
    )
    await router.route(action)

    pub_kwargs = notifier.publish.call_args.kwargs
    assert pub_kwargs["metadata"]["reason"] == "The dog walker is at the door."
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest tests/core/routing/test_router_enforcement.py -v -k "intercepted or reason"`
Expected: both FAIL with `KeyError: 'reason'`.

- [ ] **Step 7: Put `reason` in the notification metadata**

In `core/routing/domain_router.py::_intercept_critical`, change the `metadata=` dict:

```python
                metadata={
                    "pending_action_id": action.request_id,
                    "tool_name": action.tool_name,
                    "parameters": action.parameters,
                    "reason": action.reason,
                },
```

- [ ] **Step 8: Run the router tests**

Run: `uv run pytest tests/core/routing/test_router_enforcement.py -v`
Expected: all PASS.

- [ ] **Step 9: Write the failing engine tests**

Append to `tests/core/conscious/test_engine.py`:

```python
def _critical_tool() -> ToolInfo:
    return ToolInfo(
        name="home.unlock_door",
        description="Unlock a door",
        parameters={"entity_id": {"type": "str", "description": "Lock entity"}},
        feature_name="home",
        feature_description="Home control",
        target_service="home-service",
        risk="critical",
    )


def test_critical_tools_get_a_reason_parameter(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """Critical tools are offered an optional `reason` argument; benign tools are not."""
    engine = ConsciousEngine(**mock_deps)
    benign = ToolInfo(
        name="home.get_lights",
        description="Get light state",
        parameters={},
        feature_name="home",
        feature_description="Home control",
        target_service="home-service",
    )

    result = engine._tools_to_openai_format([_critical_tool(), benign])

    critical_schema = result[0]["function"]["parameters"]
    assert critical_schema["properties"]["reason"]["type"] == "string"
    assert "reason" not in critical_schema["required"]
    assert "entity_id" in critical_schema["required"]
    benign_schema = result[1]["function"]["parameters"]
    assert "reason" not in benign_schema["properties"]


@pytest.mark.asyncio
async def test_dispatch_moves_reason_onto_the_action(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """`reason` leaves `parameters` (the domain service never sees it) and lands on the action."""
    action_result = MagicMock()
    action_result.status = "success"
    action_result.result = {"ok": True}
    mock_deps["domain_router"].route.return_value = action_result
    engine = ConsciousEngine(**mock_deps)

    tool_call = {
        "id": "tc-1",
        "name": "home.unlock_door",
        "input": {"entity_id": "lock.front_door", "reason": "The dog walker is here."},
    }
    await engine._dispatch_tool_call(tool_call, tools=[_critical_tool()])

    routed = mock_deps["domain_router"].route.call_args[0][0]
    assert routed.reason == "The dog walker is here."
    assert routed.parameters == {"entity_id": "lock.front_door"}
    # The caller's dict is not mutated
    assert tool_call["input"] == {"entity_id": "lock.front_door", "reason": "The dog walker is here."}
```

- [ ] **Step 10: Run them to verify they fail**

Run: `uv run pytest tests/core/conscious/test_engine.py -v -k reason`
Expected: `test_critical_tools_get_a_reason_parameter` FAILS with `KeyError: 'reason'`; `test_dispatch_moves_reason_onto_the_action` FAILS on `routed.reason == ...` (`None != 'The dog walker is here.'`).

- [ ] **Step 11: Offer `reason` on critical tools and move it onto the action**

In `core/conscious/engine.py`, add a second class constant next to `_INTEGRATION_PREFIX` (line ~259):

```python
    # Prefix for integration tool names to distinguish from domain tools
    _INTEGRATION_PREFIX: ClassVar[str] = "integration_"
    # Extra argument offered on critical tools; moved off `parameters` onto ActionRequest.reason
    _REASON_PARAM: ClassVar[str] = "reason"
```

In `_tools_to_openai_format`, after the `for pname, pinfo in t.parameters.items():` loop and before `openai_tools.append(`, add:

```python
            if t.risk == "critical" and self._REASON_PARAM not in properties:
                properties[self._REASON_PARAM] = {
                    "type": "string",
                    "description": (
                        "One sentence, addressed to the user, saying why this action is "
                        "needed. It is shown on the confirmation prompt."
                    ),
                }
```

(`reason` is deliberately **not** appended to `required` — the model may omit it.)

In `_dispatch_tool_call`, change the second line of the body so the params dict is a copy:

```python
        name = tc["name"]
        params = dict(tc.get("input", {}))
```

Then replace the domain-tool block (`# 4. Domain tools …` through the `ActionRequest(...)` construction) with:

```python
        # 4. Domain tools — route to external service via DomainRouter
        target = ""
        risk = "benign"
        for t in tools:
            if t.name == name:
                target = t.target_service
                risk = t.risk
                break

        if not target:
            return self._make_tool_result(tc["id"], f"Error: tool '{name}' not found in registry")

        reason = params.pop(self._REASON_PARAM, None) if risk == "critical" else None
        action = ActionRequest(
            source="conscious-engine",
            target_service=target,
            tool_name=name,
            parameters=params,
            reason=str(reason) if reason else None,
        )
```

The lines after (`action_result = await self._router.route(action)` onward) stay as they are.

- [ ] **Step 12: Run the engine tests**

Run: `uv run pytest tests/core/conscious/test_engine.py -v`
Expected: all PASS (including the pre-existing `test_tools_to_openai_format`, whose benign tool must not gain a `reason` property).

- [ ] **Step 13: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: `All checks passed!`, no format diffs, `Success: no issues found`.

```bash
git add bus/schemas/events.py sdk/alfred_sdk/events.py core/routing/domain_router.py core/conscious/engine.py tests/bus/test_action_confirmed.py tests/core/routing/test_router_enforcement.py tests/core/conscious/test_engine.py
git commit -m "feat(actions): carry the model's reason on critical ActionRequests"
```

---

### Task 2: Pending-action reads — `GET /api/actions/pending` and `GET /api/actions/{request_id}`

The Door needs to *list* what is waiting (to render the sheet on app open) and *read one* (to render a push-tap deep link, including its remaining fuse). Today only `POST …/confirm` exists. Reads must not consume the entry.

**Files:**
- Modify: `core/routing/pending.py`
- Modify: `core/channels/web_server.py` (imports at line 48; new routes **before** the confirm route at line 752)
- Test: `tests/core/routing/test_pending.py`
- Test: `tests/core/channels/test_actions_confirm.py`

- [ ] **Step 1: Write the failing helper tests**

Change the imports at the top of `tests/core/routing/test_pending.py` to:

```python
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from bus.schemas.events import ActionRequest


async def _aiter(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item
```

Then append these tests:

```python
@pytest.mark.asyncio
async def test_get_pending_returns_action_and_ttl_without_consuming() -> None:
    from core.routing.pending import get_pending_action

    redis = AsyncMock()
    action = _action()
    redis.get = AsyncMock(return_value=action.model_dump_json().encode())
    redis.ttl = AsyncMock(return_value=120)

    found = await get_pending_action(redis, action.request_id)

    assert found is not None
    got, ttl = found
    assert got.request_id == action.request_id
    assert ttl == 120
    redis.get.assert_awaited_once_with(f"alfred:pending_actions:{action.request_id}")
    redis.getdel.assert_not_called()


@pytest.mark.asyncio
async def test_get_pending_missing_returns_none_and_negative_ttl_clamps() -> None:
    from core.routing.pending import get_pending_action

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    assert await get_pending_action(redis, "ghost") is None

    action = _action()
    redis.get = AsyncMock(return_value=action.model_dump_json())
    redis.ttl = AsyncMock(return_value=-2)  # key vanished between GET and TTL
    found = await get_pending_action(redis, action.request_id)
    assert found is not None
    assert found[1] == 0


@pytest.mark.asyncio
async def test_list_pending_scans_prefix_and_sorts_oldest_first() -> None:
    from core.routing.pending import list_pending_actions

    older = _action().model_copy(update={"timestamp": datetime(2026, 9, 4, 8, 0, tzinfo=UTC)})
    newer = _action().model_copy(update={"timestamp": datetime(2026, 9, 4, 8, 1, tzinfo=UTC)})
    store = {
        f"alfred:pending_actions:{newer.request_id}": newer.model_dump_json().encode(),
        f"alfred:pending_actions:{older.request_id}": older.model_dump_json().encode(),
        "alfred:pending_actions:vanished": None,
    }
    redis = AsyncMock()
    redis.scan_iter = MagicMock(return_value=_aiter(list(store)))
    redis.get = AsyncMock(side_effect=lambda key: store[key])
    redis.ttl = AsyncMock(return_value=200)

    items = await list_pending_actions(redis)

    assert [a.request_id for a, _ in items] == [older.request_id, newer.request_id]
    assert all(ttl == 200 for _, ttl in items)
    redis.scan_iter.assert_called_once_with(match="alfred:pending_actions:*")


def test_pending_action_payload_shape() -> None:
    from core.routing.pending import pending_action_payload

    action = _action().model_copy(update={"reason": "The dog walker is here."})
    payload = pending_action_payload(action, 90)

    assert payload["request_id"] == action.request_id
    assert payload["tool_name"] == "home.unlock_door"
    assert payload["target_service"] == "home-service"
    assert payload["parameters"] == {"entity_id": "lock.front_door"}
    assert payload["reason"] == "The dog walker is here."
    assert payload["source"] == "conscious-engine"
    assert payload["timestamp"] == action.timestamp.isoformat()
    assert payload["ttl_seconds"] == 90
    expires = datetime.fromisoformat(payload["expires_at"])
    assert 85 <= (expires - datetime.now(UTC)).total_seconds() <= 90
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/core/routing/test_pending.py -v`
Expected: the four new tests FAIL with `ImportError: cannot import name 'get_pending_action'` (and the same for `list_pending_actions`, `pending_action_payload`); the existing tests still PASS.

- [ ] **Step 3: Implement the helpers**

In `core/routing/pending.py`, change the imports to:

```python
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from loguru import logger

from bus.schemas.events import ActionRequest
from shared.streams import ACTIONS_STREAM, PENDING_ACTIONS_PREFIX, decode_stream_value

if TYPE_CHECKING:
    from shared.types import AioRedis
```

Append after `confirm_pending_action`:

```python
async def get_pending_action(
    redis: AioRedis, request_id: str
) -> tuple[ActionRequest, int] | None:
    """Read a pending action and its remaining TTL without consuming it.

    Returns None when the entry is missing or expired. A TTL of -2 (key gone
    between GET and TTL) or -1 (no expiry, should never happen) is clamped to 0.
    """
    raw: bytes | str | None = await redis.get(pending_key(request_id))
    if raw is None:
        return None
    action = ActionRequest.model_validate_json(decode_stream_value(raw))
    ttl = await redis.ttl(pending_key(request_id))
    return action, max(int(ttl), 0)


async def list_pending_actions(redis: AioRedis) -> list[tuple[ActionRequest, int]]:
    """Every pending action with its remaining TTL, oldest request first."""
    found: list[tuple[ActionRequest, int]] = []
    async for key in redis.scan_iter(match=f"{PENDING_ACTIONS_PREFIX}*"):
        request_id = decode_stream_value(key)[len(PENDING_ACTIONS_PREFIX) :]
        item = await get_pending_action(redis, request_id)
        if item is not None:  # may have expired mid-scan
            found.append(item)
    found.sort(key=lambda pair: pair[0].timestamp)
    return found


def pending_action_payload(action: ActionRequest, ttl_seconds: int) -> dict[str, Any]:
    """JSON shape the web clients render for one pending action."""
    return {
        "request_id": action.request_id,
        "tool_name": action.tool_name,
        "target_service": action.target_service,
        "parameters": action.parameters,
        "reason": action.reason,
        "source": action.source,
        "timestamp": action.timestamp.isoformat(),
        "ttl_seconds": ttl_seconds,
        "expires_at": (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat(),
    }
```

- [ ] **Step 4: Run the helper tests**

Run: `uv run pytest tests/core/routing/test_pending.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing route tests**

Change the imports at the top of `tests/core/channels/test_actions_confirm.py` to:

```python
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from bus.schemas.events import ActionRequest
from core.channels.web_server import create_app
from shared.streams import ACTIONS_STREAM


async def _aiter(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item
```

Append these tests:

```python
def test_get_pending_action_returns_payload(web_client: TestClient) -> None:
    action = _pending_action().model_copy(update={"reason": "You asked me to."})
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=action.model_dump_json().encode())
    redis.ttl = AsyncMock(return_value=42)

    resp = web_client.get(f"/api/actions/{action.request_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == action.request_id
    assert body["tool_name"] == "home.unlock_door"
    assert body["reason"] == "You asked me to."
    assert body["ttl_seconds"] == 42
    assert "expires_at" in body
    redis.getdel.assert_not_called()


def test_get_pending_action_missing_returns_404(web_client: TestClient) -> None:
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.get = AsyncMock(return_value=None)

    resp = web_client.get("/api/actions/ghost-id")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Pending action not found or expired"


def test_list_pending_actions(web_client: TestClient) -> None:
    action = _pending_action()
    redis: AsyncMock = web_client.app.state.redis  # type: ignore[attr-defined]
    redis.scan_iter = MagicMock(
        return_value=_aiter([f"alfred:pending_actions:{action.request_id}"])
    )
    redis.get = AsyncMock(return_value=action.model_dump_json().encode())
    redis.ttl = AsyncMock(return_value=250)

    resp = web_client.get("/api/actions/pending")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["actions"]) == 1
    assert body["actions"][0]["request_id"] == action.request_id
    assert body["actions"][0]["ttl_seconds"] == 250


def test_pending_reads_require_auth() -> None:
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = AsyncMock()
    client = TestClient(app)  # no auth cookie

    assert client.get("/api/actions/pending").status_code == 401
    assert client.get("/api/actions/any-id").status_code == 401
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/core/channels/test_actions_confirm.py -v`
Expected: the four new tests FAIL with `assert 404 == 200` / `assert 404 == 401` (no such routes yet — FastAPI's 404 is not the pending-specific 404, and `test_get_pending_action_missing_returns_404` fails on the `detail` text); the three existing tests PASS.

- [ ] **Step 7: Add the routes**

In `core/channels/web_server.py`, change line 48 to:

```python
from core.routing.pending import (
    confirm_pending_action,
    get_pending_action,
    list_pending_actions,
    pending_action_payload,
)
```

Insert the two read routes **immediately before** `@app.post("/api/actions/{request_id}/confirm")`. Order matters: the literal `/api/actions/pending` path must be registered before the `/{request_id}` pattern or FastAPI will treat `pending` as an id.

```python
    @app.get("/api/actions/pending")
    async def list_pending(request: Request) -> dict[str, list[dict[str, Any]]]:
        """Every critical action still waiting for confirmation, oldest first."""
        if not getattr(request.state, "authenticated", False):
            raise HTTPException(status_code=401, detail="Authentication required")
        r: aioredis.Redis[Any] = app.state.redis  # type: ignore[type-arg]
        items = await list_pending_actions(r)
        return {"actions": [pending_action_payload(a, ttl) for a, ttl in items]}

    @app.get("/api/actions/{request_id}")
    async def get_pending(request_id: str, request: Request) -> dict[str, Any]:
        """One pending action with its remaining fuse. Does not consume it."""
        if not getattr(request.state, "authenticated", False):
            raise HTTPException(status_code=401, detail="Authentication required")
        r: aioredis.Redis[Any] = app.state.redis  # type: ignore[type-arg]
        item = await get_pending_action(r, request_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Pending action not found or expired")
        action, ttl = item
        return pending_action_payload(action, ttl)
```

- [ ] **Step 8: Run the route tests**

Run: `uv run pytest tests/core/channels/test_actions_confirm.py -v`
Expected: all seven PASS.

- [ ] **Step 9: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean.

```bash
git add core/routing/pending.py core/channels/web_server.py tests/core/routing/test_pending.py tests/core/channels/test_actions_confirm.py
git commit -m "feat(actions): read pending critical actions without consuming them"
```

---

### Task 3: `CostState.request_count` and computed `avg_usd`

The System bench shows "N requests · avg $X" next to today's spend. The overview already returns the stored `alfred:cost:daily` JSON verbatim (`admin_api.py:222-223`), so adding the fields to `CostState` is enough for the API — `avg_usd` is a pydantic computed field, which `model_dump_json()` serialises and `model_validate_json()` ignores on the way back in.

**Files:**
- Modify: `core/conscious/cost.py:9, 21-27, 87-106, 118-127, 129-150`
- Test: `tests/core/conscious/test_cost.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/conscious/test_cost.py`:

```python
@pytest.mark.asyncio
async def test_record_spend_counts_requests(mock_redis: AsyncMock) -> None:
    existing = CostState(date=_TODAY, spend_usd=1.0, cap_usd=5.0, request_count=3)
    mock_redis.get.return_value = existing.model_dump_json().encode()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)

    state = await tracker.record_spend(
        prompt_tokens=1000, completion_tokens=500, model="claude-opus-4-6"
    )

    assert state.request_count == 4
    assert state.spend_usd > 1.0


def test_avg_usd_is_spend_over_requests() -> None:
    assert CostState(date=_TODAY, spend_usd=0.0, cap_usd=5.0).avg_usd == 0.0
    state = CostState(date=_TODAY, spend_usd=1.5, cap_usd=5.0, request_count=4)
    assert state.avg_usd == 0.375
    assert "avg_usd" in state.model_dump()


def test_stored_json_without_new_fields_still_loads() -> None:
    """Data written before this change (no request_count/avg_usd) and data written
    after it (avg_usd present in JSON) both validate."""
    old = CostState.model_validate_json('{"date": "2026-09-01", "spend_usd": 0.5, "cap_usd": 5.0}')
    assert old.request_count == 0
    assert old.avg_usd == 0.0

    new = CostState(date=_TODAY, spend_usd=2.0, cap_usd=5.0, request_count=2)
    reloaded = CostState.model_validate_json(new.model_dump_json())
    assert reloaded.request_count == 2
    assert reloaded.avg_usd == 1.0


@pytest.mark.asyncio
async def test_mark_alert_sent_keeps_request_count(mock_redis: AsyncMock) -> None:
    existing = CostState(date=_TODAY, spend_usd=4.5, cap_usd=5.0, request_count=7)
    mock_redis.get.return_value = existing.model_dump_json().encode()
    tracker = CostTracker(redis=mock_redis, daily_cap_usd=5.0)

    await tracker.mark_alert_sent()

    saved = CostState.model_validate_json(mock_redis.set.call_args[0][1])
    assert saved.alert_sent is True
    assert saved.request_count == 7
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/core/conscious/test_cost.py -v -k "request_count or avg_usd or new_fields"`
Expected: FAIL with `ValidationError` (`request_count` unexpected) and `AttributeError: 'CostState' object has no attribute 'avg_usd'`.

- [ ] **Step 3: Implement**

In `core/conscious/cost.py`, change the pydantic import (line 9):

```python
from pydantic import BaseModel, computed_field
```

Replace `class CostState`:

```python
class CostState(BaseModel):
    """Daily Claude API spend tracking. Stored at alfred:cost:daily in Redis."""

    date: str  # ISO date YYYY-MM-DD
    spend_usd: float
    cap_usd: float
    alert_sent: bool = False
    request_count: int = 0  # LLM calls recorded today

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avg_usd(self) -> float:
        """Mean spend per recorded call today (0.0 before the first call)."""
        return round(self.spend_usd / self.request_count, 6) if self.request_count else 0.0
```

In `record_spend`, replace the `state = CostState(...)` block with a `model_copy` so unrelated fields carry over:

```python
        state = await self._get_state()
        cost = self._estimate_cost(prompt_tokens, completion_tokens, model)
        state = state.model_copy(
            update={
                "spend_usd": state.spend_usd + cost,
                "cap_usd": self._daily_cap,
                "request_count": state.request_count + 1,
            }
        )
        await self._save_state(state)
```

In `mark_alert_sent`, replace the `state = CostState(...)` block with:

```python
        state = state.model_copy(update={"alert_sent": True})
```

In `send_alert_if_needed`, replace the `state = CostState(...)` block (after `await self._notifier.publish(...)`) with the same line:

```python
        state = state.model_copy(update={"alert_sent": True})
```

- [ ] **Step 4: Run the cost tests**

Run: `uv run pytest tests/core/conscious/test_cost.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean. (If mypy complains about `computed_field` on a property, the `# type: ignore[prop-decorator]` above is the documented pydantic v2 idiom — keep it, don't remove the decorator.)

```bash
git add core/conscious/cost.py tests/core/conscious/test_cost.py
git commit -m "feat(cost): count requests and expose average spend per call"
```

---

### Task 4: `RoutineSpec.confidence_history` (last 8 cycles)

The Triggers bench draws a sparkline of each routine's confidence. The Librarian is the only writer of `confidence`, so it keeps the history: every lifecycle pass appends the confidence it just decided on (capped at 8, newest last), and a new candidate is seeded with its initial score. `RoutineStore.save` uses `model_dump(mode="json")`, so the new list persists to YAML with no store changes; routines saved before this change load with an empty list.

**Files:**
- Modify: `core/memory/schemas.py:8, 59-70`
- Modify: `core/librarian/consolidator.py:814-822` (candidate creation), `887-893` (hit branch), `926-932` (miss branch)
- Test: `tests/core/librarian/test_consolidator_v2.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/librarian/test_consolidator_v2.py` (after the Part E lifecycle tests):

```python
@pytest.mark.asyncio
async def test_update_routine_lifecycle_hit_appends_confidence_history() -> None:
    """Every lifecycle pass appends the routine's current confidence, newest last."""
    from core.memory.schemas import RoutineSpec

    routine = RoutineSpec(
        name="morning_routine",
        trigger_pattern="morning",
        steps=[],
        confidence=0.8,
        learned_from=["ep-1"],
        state="active",
        confidence_history=[0.7],
    )
    librarian, routine_store = _make_librarian_with_routine_store([routine])

    import datetime as dt

    with patch("core.librarian.consolidator.datetime") as mock_dt:
        mock_dt.now.return_value = dt.datetime(2026, 3, 24, 8, 0, 0, tzinfo=dt.UTC)
        mock_dt.UTC = dt.UTC
        mock_dt.timedelta = dt.timedelta
        await librarian._update_routine_lifecycle()

    saved = routine_store.save.call_args[0][0]
    assert saved.confidence_history == [0.7, 0.8]


@pytest.mark.asyncio
async def test_update_routine_lifecycle_miss_records_decayed_confidence_and_caps_at_8() -> None:
    """A miss appends the *new* (possibly decayed) confidence; the list never exceeds 8."""
    from core.memory.schemas import RoutineSpec

    routine = RoutineSpec(
        name="evening_routine",
        trigger_pattern="evening",  # 17:00-23:00, so a noon check is a miss
        steps=[],
        confidence=0.8,
        learned_from=["ep-1"],
        state="active",
        consecutive_misses=0,
        confidence_history=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
    )
    librarian, routine_store = _make_librarian_with_routine_store([routine])

    import datetime as dt

    with patch("core.librarian.consolidator.datetime") as mock_dt:
        mock_dt.now.return_value = dt.datetime(2026, 3, 24, 12, 0, 0, tzinfo=dt.UTC)
        mock_dt.UTC = dt.UTC
        mock_dt.timedelta = dt.timedelta
        await librarian._update_routine_lifecycle()

    saved = routine_store.save.call_args[0][0]
    assert saved.consecutive_misses == 1
    assert len(saved.confidence_history) == 8
    assert saved.confidence_history[-1] == saved.confidence
    assert saved.confidence_history[0] == 0.2  # oldest sample dropped


@pytest.mark.asyncio
async def test_detect_patterns_seeds_confidence_history() -> None:
    """A freshly detected candidate starts its history with its initial confidence."""
    from unittest.mock import MagicMock

    routine_store = MagicMock()
    routine_store.list_all.return_value = []
    librarian = _make_librarian()
    librarian._routines = routine_store
    entries = [_make_entry_with_id(f"ep-{i}", days_ago=i * 2) for i in range(5)]
    llm_payload = [
        {
            "name": "evening_dim",
            "trigger_pattern": "20:00 daily",
            "steps": [{"description": "Dim living room lights to 30%"}],
            "confidence": 0.8,
            "learned_from": ["ep-0", "ep-2", "ep-4"],
        }
    ]
    mock_response = AsyncMock()
    mock_response.choices = [AsyncMock(message=AsyncMock(content=json.dumps(llm_payload)))]

    with patch("litellm.acompletion", return_value=mock_response):
        result = await librarian._detect_patterns(entries)

    assert result[0].confidence_history == [0.8]


def test_routine_spec_without_history_loads_empty() -> None:
    """Routines saved before this change (no confidence_history key) still validate."""
    from core.memory.schemas import RoutineSpec

    loaded = RoutineSpec.model_validate(
        {
            "name": "old",
            "trigger_pattern": "morning",
            "steps": [],
            "confidence": 0.5,
            "learned_from": [],
            "state": "active",
        }
    )
    assert loaded.confidence_history == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/core/librarian/test_consolidator_v2.py -v -k "confidence_history or without_history"`
Expected: three FAIL with `ValidationError` (`confidence_history` unexpected) and `test_routine_spec_without_history_loads_empty` FAILS with `AttributeError`.

- [ ] **Step 3: Add the field**

In `core/memory/schemas.py`, change the pydantic import to:

```python
from pydantic import BaseModel, Field
```

Add the field at the end of `class RoutineSpec`:

```python
    last_suggested: datetime | None = None
    confidence_history: list[float] = Field(default_factory=list)  # newest last, ≤8 (Librarian)
```

- [ ] **Step 4: Maintain the history in the Librarian**

In `core/librarian/consolidator.py`, add a module-level constant and helper directly below `logger = logging.getLogger(__name__)` (line 37):

```python
logger = logging.getLogger(__name__)

# How many lifecycle-cycle confidence samples a routine keeps (sparkline on the Triggers bench)
_CONFIDENCE_HISTORY_LEN = 8


def _append_confidence(history: list[float], value: float) -> list[float]:
    """Return `history` with `value` appended, trimmed to the newest samples."""
    return [*history, round(value, 4)][-_CONFIDENCE_HISTORY_LEN:]
```

In `_detect_patterns`, seed the candidate (the `RoutineSpec(...)` construction around line 814):

```python
                candidate = RoutineSpec(
                    name=name,
                    trigger_pattern=item.get("trigger_pattern", ""),
                    steps=steps,
                    confidence=confidence,
                    learned_from=item.get("learned_from", []),
                    state="candidate",
                    confidence_history=[round(confidence, 4)],
                )
```

In `_update_routine_lifecycle`, the hit branch becomes:

```python
            if pattern_fired:
                routine = routine.model_copy(
                    update={
                        "last_hit": now,
                        "consecutive_misses": 0,
                        "confidence_history": _append_confidence(
                            routine.confidence_history, routine.confidence
                        ),
                    }
                )
```

and the miss branch's final `model_copy` becomes:

```python
                routine = routine.model_copy(
                    update={
                        "consecutive_misses": new_misses,
                        "state": new_state,
                        "confidence": new_confidence,
                        "confidence_history": _append_confidence(
                            routine.confidence_history, new_confidence
                        ),
                    }
                )
```

- [ ] **Step 5: Run the Librarian and memory tests**

Run: `uv run pytest tests/core/librarian/ tests/core/memory/ -v`
Expected: all PASS.

- [ ] **Step 6: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean.

```bash
git add core/memory/schemas.py core/librarian/consolidator.py tests/core/librarian/test_consolidator_v2.py
git commit -m "feat(librarian): keep the last 8 confidence samples on every routine"
```

---

### Task 5: Librarian status hash — `last_run_at`, `reviewed`, `next_run_at`

The Memory bench shows "Librarian last ran 14 min ago · reviewed 23 · next in 46 min". Nothing records that today. The Librarian writes `last_run_at`/`reviewed` at the end of every `consolidate()` (including the empty path) and the scheduler writes `next_run_at` after every cycle. Both are best-effort — a Redis hiccup must never break consolidation.

**Files:**
- Modify: `shared/streams.py:9` (add the key next to `LIBRARIAN_QUEUE`)
- Modify: `core/librarian/consolidator.py:27` (import), `1015-1016` (empty path), `1072-1073` (result path), new methods
- Modify: `core/librarian/scheduler.py`
- Test: `tests/core/librarian/test_consolidator_v2.py`
- Test: `tests/core/librarian/test_scheduler.py`

- [ ] **Step 1: Write the failing consolidator tests**

Append to `tests/core/librarian/test_consolidator_v2.py`:

```python
@pytest.mark.asyncio
async def test_consolidate_records_status_on_empty_scratchpad() -> None:
    """Even a no-op cycle stamps last_run_at/reviewed so the dashboard shows it ran."""
    from shared.streams import LIBRARIAN_STATUS_KEY

    librarian = _make_librarian(api_key="")
    librarian._redis.lrange.return_value = []
    librarian._redis.rename.side_effect = Exception("no such key")

    await librarian.consolidate()

    librarian._redis.hset.assert_awaited()
    key, mapping = (
        librarian._redis.hset.call_args[0][0],
        librarian._redis.hset.call_args.kwargs["mapping"],
    )
    assert key == LIBRARIAN_STATUS_KEY
    assert mapping["reviewed"] == "0"
    datetime.datetime.fromisoformat(mapping["last_run_at"])  # ISO-8601, raises otherwise


@pytest.mark.asyncio
async def test_record_next_run_writes_next_run_at() -> None:
    from shared.streams import LIBRARIAN_STATUS_KEY

    librarian = _make_librarian(api_key="")
    at = datetime.datetime(2026, 9, 4, 9, 30, tzinfo=_UTC)

    await librarian.record_next_run(at)

    librarian._redis.hset.assert_awaited_once_with(
        LIBRARIAN_STATUS_KEY, mapping={"next_run_at": "2026-09-04T09:30:00+00:00"}
    )


@pytest.mark.asyncio
async def test_status_write_failure_does_not_break_consolidation() -> None:
    librarian = _make_librarian(api_key="")
    librarian._redis.lrange.return_value = []
    librarian._redis.rename.side_effect = Exception("no such key")
    librarian._redis.hset.side_effect = ConnectionError("redis down")

    result = await librarian.consolidate()

    assert result["entries_processed"] == 0
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/core/librarian/test_consolidator_v2.py -v -k "status or next_run"`
Expected: FAIL with `ImportError: cannot import name 'LIBRARIAN_STATUS_KEY'` and `AttributeError: 'Librarian' object has no attribute 'record_next_run'`.

- [ ] **Step 3: Add the key and the writers**

In `shared/streams.py`, directly after `LIBRARIAN_QUEUE = "alfred:librarian:queue"` (line 9):

```python
LIBRARIAN_QUEUE = "alfred:librarian:queue"
# HASH: last_run_at (ISO), reviewed (int as str), next_run_at (ISO) — read by the admin overview
LIBRARIAN_STATUS_KEY = "alfred:librarian:status"
```

In `core/librarian/consolidator.py`, change line 27 to:

```python
from shared.streams import LIBRARIAN_QUEUE, LIBRARIAN_STATUS_KEY
```

Add two methods to `class Librarian` directly above `async def consolidate(`:

```python
    async def _record_run(self, reviewed: int) -> None:
        """Stamp the status hash after a cycle. Best-effort — never fails the cycle."""
        try:
            await self._redis.hset(
                LIBRARIAN_STATUS_KEY,
                mapping={
                    "last_run_at": datetime.now(UTC).isoformat(),
                    "reviewed": str(reviewed),
                },
            )
        except Exception as exc:
            logger.warning("Could not record Librarian run status: %s", exc)

    async def record_next_run(self, at: datetime) -> None:
        """Record when the scheduler will run the next cycle (shown on the dashboard)."""
        await self._redis.hset(LIBRARIAN_STATUS_KEY, mapping={"next_run_at": at.isoformat()})
```

In `consolidate()`, the empty path becomes:

```python
        if not lines:
            logger.info("Scratchpad empty — nothing to consolidate")
            await self._record_run(0)
            return {"entries_processed": 0, "routines_reindexed": routines_reindexed}
```

and the end of the full path becomes:

```python
        logger.info("Consolidation complete: %s", result)
        await self._record_run(len(lines))
        return result
```

- [ ] **Step 4: Run the consolidator tests**

Run: `uv run pytest tests/core/librarian/test_consolidator_v2.py -v`
Expected: all PASS. (`_make_librarian` uses `AsyncMock()` for Redis, so `hset` is awaitable everywhere without fixture changes.)

- [ ] **Step 5: Write the failing scheduler test**

Change the imports at the top of `tests/core/librarian/test_scheduler.py` to:

```python
import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.librarian.scheduler import LibrarianScheduler
```

Append:

```python
@pytest.mark.asyncio
async def test_scheduler_records_next_run_after_each_cycle() -> None:
    """After every cycle (success or failure) the scheduler stamps next_run_at."""
    mock_librarian = AsyncMock()
    mock_librarian.consolidate = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    scheduler = LibrarianScheduler(librarian=mock_librarian, interval_seconds=0.01)

    before = datetime.now(UTC)
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert mock_librarian.record_next_run.await_count >= 1
    next_run = mock_librarian.record_next_run.await_args[0][0]
    assert before <= next_run <= datetime.now(UTC) + timedelta(seconds=0.01)


@pytest.mark.asyncio
async def test_scheduler_survives_record_next_run_failure() -> None:
    mock_librarian = AsyncMock()
    mock_librarian.consolidate = AsyncMock(return_value={"entries_processed": 0})
    mock_librarian.record_next_run = AsyncMock(side_effect=ConnectionError("redis down"))
    scheduler = LibrarianScheduler(librarian=mock_librarian, interval_seconds=0.01)

    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert mock_librarian.consolidate.call_count >= 2
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/core/librarian/test_scheduler.py -v`
Expected: `test_scheduler_records_next_run_after_each_cycle` FAILS with `assert 0 >= 1`; the other three PASS.

- [ ] **Step 7: Record the next run from the scheduler**

Replace `core/librarian/scheduler.py` imports and `run()`:

```python
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
```

```python
    async def run(self) -> None:
        """Run consolidation cycles forever until cancelled."""
        logger.info("Librarian scheduler started (interval=%ds)", int(self._interval))
        while True:
            try:
                result = await self._librarian.consolidate()
                logger.info("Librarian cycle complete: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Librarian consolidation failed: %s", exc)

            next_run = datetime.now(UTC) + timedelta(seconds=self._interval)
            try:
                await self._librarian.record_next_run(next_run)
            except Exception as exc:
                logger.warning("Could not record next Librarian run: %s", exc)

            await asyncio.sleep(self._interval)
```

- [ ] **Step 8: Run the scheduler tests**

Run: `uv run pytest tests/core/librarian/ -v`
Expected: all PASS.

- [ ] **Step 9: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean.

```bash
git add shared/streams.py core/librarian/consolidator.py core/librarian/scheduler.py tests/core/librarian/test_consolidator_v2.py tests/core/librarian/test_scheduler.py
git commit -m "feat(librarian): record last/next run and reviewed count in a status hash"
```

---

### Task 6: Per-stream `rate_5m`

The Activity bench draws a throughput figure per stream. Redis stream IDs are millisecond timestamps, so "entries in the last five minutes" is one `XREVRANGE key + <now-5min> COUNT 5000`. `revrange()` in `shared/redis_streams.py` is the codebase's only allowed `xrevrange` call site, so it grows `max_id`/`min_id` keyword arguments first.

**Files:**
- Modify: `shared/redis_streams.py:70-81`
- Modify: `core/channels/stream_catalog.py` (imports; `stream_summaries`)
- Create: `tests/shared/test_redis_streams_revrange.py`
- Test: `tests/core/channels/test_stream_catalog.py:30-34` + new tests

- [ ] **Step 1: Write the failing `revrange` test**

Create `tests/shared/test_redis_streams_revrange.py`:

```python
"""revrange() forwards the id bounds to XREVRANGE."""

from __future__ import annotations

from unittest.mock import AsyncMock

from shared.redis_streams import revrange


async def test_revrange_defaults_to_whole_stream() -> None:
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[("1-0", {"event": "{}"})])

    out = await revrange(redis, "alfred:events", count=1)

    assert out == [("1-0", {"event": "{}"})]
    redis.xrevrange.assert_awaited_once_with("alfred:events", max="+", min="-", count=1)


async def test_revrange_forwards_bounds() -> None:
    redis = AsyncMock()
    redis.xrevrange = AsyncMock(return_value=[])

    await revrange(redis, "alfred:events", count=10, max_id="200-0", min_id="100-0")

    redis.xrevrange.assert_awaited_once_with("alfred:events", max="200-0", min="100-0", count=10)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/shared/test_redis_streams_revrange.py -v`
Expected: `test_revrange_defaults_to_whole_stream` FAILS on `assert_awaited_once_with` (called with `count=1` only); `test_revrange_forwards_bounds` FAILS with `TypeError: revrange() got an unexpected keyword argument 'max_id'`.

- [ ] **Step 3: Extend `revrange`**

Replace the function in `shared/redis_streams.py`:

```python
async def revrange(
    redis: AioRedis,
    stream: str,
    *,
    count: int,
    max_id: str = "+",
    min_id: str = "-",
) -> list[tuple[bytes | str, dict[bytes | str, bytes | str]]]:
    """Typed ``XREVRANGE`` — owns the stub-gap ignore for the whole codebase.

    ``max_id``/``min_id`` bound the scan (newest first); the defaults cover
    the whole stream.
    """
    entries: list[tuple[bytes | str, dict[bytes | str, bytes | str]]]
    entries = await redis.xrevrange(  # type: ignore[assignment,misc,unused-ignore]
        stream, max=max_id, min=min_id, count=count
    )
    return entries
```

- [ ] **Step 4: Run the `revrange` tests plus its existing callers**

Run: `uv run pytest tests/shared/test_redis_streams_revrange.py tests/core/channels/test_request_bus.py tests/core/channels/test_telemetry_ws.py -v`
Expected: all PASS — `core/channels/request_bus.py:35` and `core/channels/telemetry_ws.py:38` call `revrange(..., count=1)` and must keep working with the new defaults.

- [ ] **Step 5: Write the failing catalog tests**

In `tests/core/channels/test_stream_catalog.py`, change the expected dict in `test_stream_summaries_defensive_on_missing_stream`:

```python
    assert out["events"] == {"length": 0, "last_id": None, "last_ts": None, "rate_5m": 0.0}
```

Append:

```python
async def test_stream_summaries_reports_rate_over_five_minutes() -> None:
    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(
        return_value={"length": 42, "last-entry": (b"1718000000123-0", {b"event": b"{}"})}
    )
    redis.xrevrange = AsyncMock(return_value=[("2-0", {}), ("1-0", {})])

    out = await stream_summaries(redis)

    assert out["events"]["rate_5m"] == round(2 / 300, 3)
    kwargs = redis.xrevrange.await_args.kwargs
    assert kwargs["max"] == "+"
    assert kwargs["count"] == 5000
    assert kwargs["min"].endswith("-0")
    assert int(kwargs["min"].split("-")[0]) > 0


async def test_stream_summaries_rate_is_zero_when_revrange_fails() -> None:
    redis = AsyncMock()
    redis.xinfo_stream = AsyncMock(
        return_value={"length": 1, "last-entry": (b"1718000000123-0", {b"event": b"{}"})}
    )
    redis.xrevrange = AsyncMock(side_effect=Exception("boom"))

    out = await stream_summaries(redis)

    assert out["events"]["length"] == 1
    assert out["events"]["rate_5m"] == 0.0
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/core/channels/test_stream_catalog.py -v`
Expected: the three touched tests FAIL with `KeyError: 'rate_5m'` / dict mismatch; the rest PASS.

- [ ] **Step 7: Compute the rate in `stream_summaries`**

In `core/channels/stream_catalog.py`, change the imports to:

```python
import json
import time
from typing import Any

from shared.redis_streams import revrange
from shared.streams import (
    ACTIONS_STREAM,
    EVENTS_STREAM,
    HOME_ACTION_RESULTS_STREAM,
    HOME_STATE_STREAM,
    NOTIFICATION_DISPATCH_STREAM,
    REFLEX_OBSERVATIONS_STREAM,
    USER_REQUESTS_STREAM,
    USER_RESPONSES_STREAM,
    decode_stream_value,
)
from shared.types import AioRedis  # noqa: TC001
```

Add two constants and a helper directly above `async def stream_summaries(`:

```python
_RATE_WINDOW_SECONDS = 300
_RATE_SAMPLE_CAP = 5000  # a stream busier than ~16/s saturates the figure instead of the scan


async def _rate_5m(redis: AioRedis, key: str, now_ms: int) -> float:
    """Entries per second over the last five minutes (0.0 on any failure)."""
    try:
        recent = await revrange(
            redis,
            key,
            count=_RATE_SAMPLE_CAP,
            min_id=f"{now_ms - _RATE_WINDOW_SECONDS * 1000}-0",
        )
    except Exception:
        return 0.0
    return round(len(recent) / _RATE_WINDOW_SECONDS, 3)
```

Replace `stream_summaries`:

```python
async def stream_summaries(redis: AioRedis) -> dict[str, dict[str, Any]]:
    """Length, last-entry recency and 5-minute rate for every catalog stream.
    Missing streams report zero — never raise."""
    out: dict[str, dict[str, Any]] = {}
    now_ms = int(time.time() * 1000)
    for name, key in STREAM_CATALOG.items():
        try:
            raw: Any = await redis.xinfo_stream(key)
            info: dict[str | bytes, Any] = raw
            last = info.get("last-entry") or info.get(b"last-entry")
            last_id = decode_stream_value(last[0]) if last else None
            out[name] = {
                "length": int(info.get("length") or info.get(b"length") or 0),
                "last_id": last_id,
                "last_ts": _id_to_ts(last_id) if last_id else None,
                "rate_5m": await _rate_5m(redis, key, now_ms),
            }
        except Exception:
            out[name] = {"length": 0, "last_id": None, "last_ts": None, "rate_5m": 0.0}
    return out
```

- [ ] **Step 8: Run the catalog and admin tests**

Run: `uv run pytest tests/core/channels/test_stream_catalog.py tests/core/channels/test_admin_api.py -v`
Expected: all PASS. `_overview_redis()` in the admin tests makes `xinfo_stream` raise, so every stream takes the `except` branch and `xrevrange` is never called there.

- [ ] **Step 9: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean.

```bash
git add shared/redis_streams.py core/channels/stream_catalog.py tests/shared/test_redis_streams_revrange.py tests/core/channels/test_stream_catalog.py
git commit -m "feat(admin): report a five-minute entry rate per stream"
```

---

### Task 7: Overview `reflex` and `librarian` blocks

The System bench header shows the Reflex model and its recent latency; the Memory bench shows the Librarian status from Task 5. Reflex latency is derived from the observation stream: every `ReflexObservation` carries its own `timestamp` and the originating event under `trigger_event.timestamp`, so `observation − trigger` is the decision latency. The overview reads the newest 20 with `revrange()`.

**Files:**
- Modify: `core/channels/admin_api.py:13-47` (imports), `139-151` (`_base_overview`), new helpers, `212-242` (`overview`)
- Test: `tests/core/channels/test_admin_api.py`

- [ ] **Step 1: Write the failing tests**

In `tests/core/channels/test_admin_api.py`, add to `_overview_redis()` one line so the reflex read has something to return:

```python
    r.xinfo_stream = AsyncMock(side_effect=Exception("missing"))
    r.xrevrange = AsyncMock(return_value=[])
    return r
```

Append two assertions to the end of `test_overview_reports_redis_down`:

```python
    assert data["inference"] == {"ollama": False, "lmstudio": False}
    assert data["reflex"] == {"model": None, "last_ms": None, "p50_ms": None}
    assert data["librarian"] == {"last_run_at": None, "reviewed": None, "next_run_at": None}
```

Append new tests:

```python
def _observation(observed_at: str, triggered_at: str) -> tuple[bytes, dict[bytes, bytes]]:
    event = {
        "event_type": "reflex_observation",
        "timestamp": observed_at,
        "source": "reflex-engine",
        "origin": "state_change",
        "trigger_event": {"event_type": "state_changed", "timestamp": triggered_at},
    }
    return (b"1-0", {b"event": json.dumps(event).encode()})


def test_overview_reports_reflex_latency_and_librarian_status(monkeypatch: Any) -> None:
    monkeypatch.setenv("REFLEX_BACKEND", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:8b")
    r = _overview_redis()
    # newest first, as XREVRANGE returns them
    r.xrevrange = AsyncMock(
        return_value=[
            _observation("2026-09-04T10:00:00.400+00:00", "2026-09-04T10:00:00.000+00:00"),
            _observation("2026-09-04T09:59:00.250+00:00", "2026-09-04T09:59:00.000+00:00"),
            _observation("2026-09-04T09:58:00.100+00:00", "2026-09-04T09:58:00.000+00:00"),
            (b"0-0", {b"event": b"not json"}),  # corrupt entry is skipped
        ]
    )

    async def _hgetall(key: str) -> dict[bytes, bytes]:
        if key == f"{AUTH_SESSION_PREFIX}{_SESSION}":
            return {b"authenticated": b"1"}
        if key == "alfred:librarian:status":
            return {
                b"last_run_at": b"2026-09-04T09:00:00+00:00",
                b"reviewed": b"23",
                b"next_run_at": b"2026-09-04T10:00:00+00:00",
            }
        return {}

    r.hgetall = AsyncMock(side_effect=_hgetall)
    client = make_admin_client(r)

    data = client.get("/api/admin/overview").json()

    assert data["reflex"] == {"model": "qwen3:8b", "last_ms": 400.0, "p50_ms": 250.0}
    assert data["librarian"] == {
        "last_run_at": "2026-09-04T09:00:00+00:00",
        "reviewed": 23,
        "next_run_at": "2026-09-04T10:00:00+00:00",
    }
    r.xrevrange.assert_awaited_once_with(
        "alfred:reflex:observations", max="+", min="-", count=20
    )


def test_overview_reflex_survives_stream_failure(monkeypatch: Any) -> None:
    monkeypatch.setenv("REFLEX_BACKEND", "openai")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "Qwen/Qwen3-8B")
    r = _overview_redis()
    r.xrevrange = AsyncMock(side_effect=Exception("boom"))
    client = make_admin_client(r)

    data = client.get("/api/admin/overview").json()

    assert data["reflex"] == {"model": "Qwen/Qwen3-8B", "last_ms": None, "p50_ms": None}
    assert data["librarian"] == {"last_run_at": None, "reviewed": None, "next_run_at": None}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/core/channels/test_admin_api.py -v -k "overview"`
Expected: the three touched tests FAIL with `KeyError: 'reflex'`; others PASS.

- [ ] **Step 3: Implement**

In `core/channels/admin_api.py`, add `import statistics` to the stdlib imports (between `import re` and `from datetime import …`), add `from shared.redis_streams import revrange` directly above `from shared.streams import (`, and add two keys to that import block (keep it alphabetical):

```python
from shared.streams import (
    ACTIONS_STREAM,
    CONTEXT_PREFIX,
    COST_DAILY_KEY,
    DEFERRED_NOTIFICATIONS_KEY,
    DEVICE_TOKENS_KEY,
    DND_STATE_KEY,
    LIBRARIAN_STATUS_KEY,
    REFLEX_OBSERVATIONS_STREAM,
    SCRATCHPAD_QUEUE,
    SESSIONS_KEY_PREFIX,
    TRIGGERS_KEY,
    decode_stream_value,
)
```

Extend `_base_overview()`:

```python
    return {
        "redis": {"connected": False},
        "cost": None,
        "dnd": {"active": False},
        "counts": {"sessions": 0, "devices": 0, "deferred": 0, "triggers": 0},
        "streams": {},
        "inference": {"ollama": False, "lmstudio": False},
        "reflex": {"model": None, "last_ms": None, "p50_ms": None},
        "librarian": {"last_run_at": None, "reviewed": None, "next_run_at": None},
    }
```

Add two helpers directly below `_decode_hash`:

```python
_REFLEX_LATENCY_SAMPLES = 20


async def _reflex_latencies(r: AioRedis, *, count: int = _REFLEX_LATENCY_SAMPLES) -> list[float]:
    """Decision latency (ms) of the newest Reflex observations, newest first.

    Each observation stamps its own ``timestamp`` and carries the originating
    event under ``trigger_event.timestamp``; the difference is how long the
    Reflex Engine took. Entries that don't parse are skipped; any Redis error
    yields an empty list so the overview never 500s.
    """
    try:
        entries = await revrange(r, REFLEX_OBSERVATIONS_STREAM, count=count)
    except Exception:
        return []
    out: list[float] = []
    for _entry_id, fields in entries:
        try:
            event = decode_entry(fields)
            observed = datetime.fromisoformat(event["timestamp"])
            triggered = datetime.fromisoformat(event["trigger_event"]["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(round((observed - triggered).total_seconds() * 1000, 1))
    return out


def _librarian_status(fields: dict[str, Any]) -> dict[str, Any]:
    """Shape the ``alfred:librarian:status`` hash for the overview."""
    reviewed = fields.get("reviewed")
    return {
        "last_run_at": fields.get("last_run_at"),
        "reviewed": int(reviewed) if isinstance(reviewed, str) and reviewed.isdigit() else None,
        "next_run_at": fields.get("next_run_at"),
    }
```

`decode_entry` is already imported from `core.channels.stream_catalog`; it raises `ValueError`/`json.JSONDecodeError` (a `ValueError` subclass) on a corrupt payload, which the `except` above swallows.

In the `overview` route, replace the tail (from `cfg = AlfredConfig.from_env()` to `return out`) with:

```python
        cfg = AlfredConfig.from_env()
        out["inference"] = {
            "ollama": await _check_http(request, cfg.ollama_host.rstrip("/") + "/api/tags"),
            "lmstudio": await _check_http(request, cfg.lmstudio_host.rstrip("/") + "/v1/models"),
        }
        latencies = await _reflex_latencies(r)
        out["reflex"] = {
            "model": cfg.openai_compat_model if cfg.reflex_backend == "openai" else cfg.ollama_model,
            "last_ms": latencies[0] if latencies else None,
            "p50_ms": round(statistics.median(latencies), 1) if latencies else None,
        }
        out["librarian"] = _librarian_status(_decode_hash(await r.hgetall(LIBRARIAN_STATUS_KEY)))
        return out
```

- [ ] **Step 4: Run the admin tests**

Run: `uv run pytest tests/core/channels/test_admin_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean.

```bash
git add core/channels/admin_api.py tests/core/channels/test_admin_api.py
git commit -m "feat(admin): surface reflex latency and librarian status on the overview"
```

---

### Task 8: `latency_ms` on integration status

The System bench lists every integration with "healthy · 42 ms". `GET /api/integrations/{name}/status` already does the probe; it just doesn't say how long it took. Timing is measured around the probe only (not the manifest lookup) with `time.perf_counter()`.

**Files:**
- Modify: `core/channels/web_server.py:1-20` (add `import time`), new module helper, `649-691` (`_service_status`, `integration_status`)
- Test: `tests/core/channels/test_service_integrations_api.py:326-360`
- Test: `tests/core/channels/test_settings_api.py:156-170`

- [ ] **Step 1: Write the failing tests**

In `tests/core/channels/test_service_integrations_api.py`, extend `test_status_proxies_health_connected` and `test_status_unreachable_service`:

```python
def test_status_proxies_health_connected(service_client: TestClient) -> None:
    resp = service_client.get("/api/integrations/home-service/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "home-service"
    assert data["healthy"] is True
    assert data["detail"]["ha"]["state"] == "connected"
    assert isinstance(data["latency_ms"], float)
    assert data["latency_ms"] >= 0.0
```

```python
def test_status_unreachable_service(
    service_client: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    service_handler.unreachable = True
    resp = service_client.get("/api/integrations/home-service/status")
    data = resp.json()
    assert data["healthy"] is False
    assert "error" in data["detail"]
    assert isinstance(data["latency_ms"], float)
```

Append a new test after `test_status_unknown_name_404`:

```python
def test_status_no_endpoint_has_null_latency(service_client_no_endpoint: TestClient) -> None:
    resp = service_client_no_endpoint.get("/api/integrations/home-service/status")
    data = resp.json()
    assert data["healthy"] is False
    assert data["detail"] == {"error": "no endpoint declared"}
    assert data["latency_ms"] is None
```

(`home_service_manifest_no_endpoint` removes `credentials_endpoint`, and the base `home_service_manifest` fixture declares no `service_endpoint`, so this manifest has no endpoint at all.)

In `tests/core/channels/test_settings_api.py::test_health_check_endpoint`, add at the end:

```python
    assert data["healthy"] is True
    assert isinstance(data["latency_ms"], float)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/core/channels/test_service_integrations_api.py tests/core/channels/test_settings_api.py -v -k "status or health_check"`
Expected: the four touched tests FAIL with `KeyError: 'latency_ms'`.

- [ ] **Step 3: Implement**

In `core/channels/web_server.py`, add `import time` to the stdlib imports (after `import os`). Add a module-level helper directly above `async def require_trusted_network(` (line ~220):

```python
def _elapsed_ms(started: float) -> float:
    """Milliseconds since a ``time.perf_counter()`` reading, one decimal."""
    return round((time.perf_counter() - started) * 1000, 1)
```

In `_service_status`, time the probe:

```python
        if not endpoint:
            return {
                "name": name,
                "healthy": False,
                "detail": {"error": "no endpoint declared"},
                "latency_ms": None,
            }
        health_url = urljoin(endpoint, "/health")
        started = time.perf_counter()
        try:
            resp = await app.state.http.get(health_url)
            payload: dict[str, Any] = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "name": name,
                "healthy": False,
                "detail": {"error": str(exc)},
                "latency_ms": _elapsed_ms(started),
            }
        return {
            "name": name,
            "healthy": service_payload_healthy(resp.status_code, payload),
            "detail": payload,
            "latency_ms": _elapsed_ms(started),
        }
```

In `integration_status`, time the adapter branch:

```python
        started = time.perf_counter()
        try:
            instance = IntegrationRegistry.get(name)
            healthy = await instance.health_check()
        except Exception:
            healthy = False
        return {"name": name, "healthy": healthy, "latency_ms": _elapsed_ms(started)}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/core/channels/test_service_integrations_api.py tests/core/channels/test_settings_api.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean.

```bash
git add core/channels/web_server.py tests/core/channels/test_service_integrations_api.py tests/core/channels/test_settings_api.py
git commit -m "feat(integrations): report probe latency on the status endpoint"
```

---

### Task 9: Attention-set API — `GET /api/admin/attention`, `PUT /api/admin/attention/{domain}`

The Triggers bench has an "Attention" gate: which entities may wake the Reflex SLM. Runtime helpers exist (`attention_add`/`attention_remove`/`attention_list` in `core/reflex/attention.py`), but nothing lists the domains or exposes the `:seen` companion set, and there is no HTTP surface. The PUT takes two lists — `allow` (add) and `ask` (remove, sticky) — mirroring the two runtime primitives; the design's "allow / ask" vocabulary maps onto "in the set / not in the set".

**Files:**
- Modify: `core/reflex/attention.py` (two helpers after `attention_list`)
- Modify: `core/channels/admin_api.py` (imports, `AttentionUpdate` model, two routes before `return router`)
- Test: `tests/core/reflex/test_attention.py`
- Test: `tests/core/channels/test_admin_api.py`

- [ ] **Step 1: Write the failing helper tests**

In `tests/core/reflex/test_attention.py`, add a `scan_iter` method to `FakeSetRedis`:

```python
    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))

    async def scan_iter(self, match: str = "*") -> Any:
        prefix = match.rstrip("*")
        for key in sorted(self.sets):
            if key.startswith(prefix):
                yield key
```

Append:

```python
@pytest.mark.asyncio
async def test_seen_list_and_domains_helpers() -> None:
    from core.reflex.attention import (
        attention_add,
        attention_domains,
        attention_remove,
        attention_seen_list,
    )

    redis = FakeSetRedis()
    await attention_add(redis, "home", "light.kitchen")  # type: ignore[arg-type]
    await attention_remove(redis, "home", "sensor.dryer_power")  # type: ignore[arg-type]
    await attention_add(redis, "media", "player.living_room")  # type: ignore[arg-type]

    assert await attention_seen_list(redis, "home") == [  # type: ignore[arg-type]
        "light.kitchen",
        "sensor.dryer_power",
    ]
    assert await attention_domains(redis) == ["home", "media"]  # type: ignore[arg-type]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/core/reflex/test_attention.py -v -k helpers`
Expected: `test_seen_list_and_domains_helpers` FAILS with `ImportError: cannot import name 'attention_domains'`; `test_add_and_list_helpers` PASSES.

- [ ] **Step 3: Add the helpers**

In `core/reflex/attention.py`, directly after `attention_list`:

```python
async def attention_seen_list(redis: AioRedis, domain: str) -> list[str]:
    """Return sorted members of the sticky ``:seen`` companion set."""
    members: set[bytes | str] = await redis.smembers(attention_seen_key(domain))
    return sorted(decode_stream_value(m) for m in members)


async def attention_domains(redis: AioRedis) -> list[str]:
    """Every domain that has an attention set (or a ``:seen`` set), sorted."""
    domains: set[str] = set()
    async for key in redis.scan_iter(match=f"{ATTENTION_PREFIX}*"):
        suffix = decode_stream_value(key)[len(ATTENTION_PREFIX) :]
        domains.add(suffix.removesuffix(":seen"))
    return sorted(domains)
```

- [ ] **Step 4: Run the attention tests**

Run: `uv run pytest tests/core/reflex/test_attention.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing route tests**

Append to `tests/core/channels/test_admin_api.py`:

```python
def _attention_redis(sets: dict[str, set[str]]) -> AsyncMock:
    """AsyncMock Redis whose SET commands operate on a shared dict."""
    r = AsyncMock()

    async def _sadd(key: str, member: str) -> int:
        sets.setdefault(key, set()).add(member)
        return 1

    async def _srem(key: str, member: str) -> int:
        sets.get(key, set()).discard(member)
        return 1

    async def _smembers(key: str) -> set[bytes]:
        return {m.encode() for m in sets.get(key, set())}

    r.sadd = AsyncMock(side_effect=_sadd)
    r.srem = AsyncMock(side_effect=_srem)
    r.smembers = AsyncMock(side_effect=_smembers)
    r.scan_iter = MagicMock(side_effect=lambda match="*": _aiter(sorted(sets)))
    return r


def test_attention_get_lists_every_domain() -> None:
    sets = {
        "alfred:attention:home": {"light.kitchen"},
        "alfred:attention:home:seen": {"light.kitchen", "sensor.dryer_power"},
        "alfred:attention:media:seen": {"player.living_room"},
    }
    client = make_admin_client(_attention_redis(sets))

    resp = client.get("/api/admin/attention")

    assert resp.status_code == 200
    assert resp.json() == {
        "domains": [
            {
                "domain": "home",
                "members": ["light.kitchen"],
                "seen": ["light.kitchen", "sensor.dryer_power"],
            },
            {"domain": "media", "members": [], "seen": ["player.living_room"]},
        ]
    }


def test_attention_get_degrades_to_empty_on_redis_error() -> None:
    r = _overview_redis()
    r.scan_iter = MagicMock(side_effect=ConnectionError("down"))
    client = make_admin_client(r)

    resp = client.get("/api/admin/attention")

    assert resp.status_code == 200
    assert resp.json() == {"domains": []}


def test_attention_put_adds_and_removes() -> None:
    sets: dict[str, set[str]] = {"alfred:attention:home": {"sensor.dryer_power"}}
    client = make_admin_client(_attention_redis(sets))

    resp = client.put(
        "/api/admin/attention/home",
        json={"allow": ["light.kitchen"], "ask": ["sensor.dryer_power"]},
    )

    assert resp.status_code == 200
    assert resp.json() == {
        "domain": "home",
        "members": ["light.kitchen"],
        "seen": ["light.kitchen", "sensor.dryer_power"],
    }
    assert sets["alfred:attention:home"] == {"light.kitchen"}
    # Removal is sticky: the entity stays in :seen so the seed rule can't re-add it
    assert "sensor.dryer_power" in sets["alfred:attention:home:seen"]


def test_attention_put_rejects_bad_domain() -> None:
    client = make_admin_client(_attention_redis({}))

    resp = client.put("/api/admin/attention/Not-A-Domain", json={"allow": ["x"]})

    assert resp.status_code == 400


def test_attention_requires_auth() -> None:
    client = make_admin_client(_attention_redis({}), authed=False)
    assert client.get("/api/admin/attention").status_code == 401
    assert client.put("/api/admin/attention/home", json={"allow": []}).status_code == 401
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/core/channels/test_admin_api.py -v -k attention`
Expected: all five FAIL with a 404 where 200/400/401 was expected (the routes don't exist yet, and FastAPI's 404 fires before the router's auth dependency).

- [ ] **Step 7: Add the routes**

In `core/channels/admin_api.py`, change the pydantic import to `from pydantic import BaseModel, Field`, and add the reflex helpers import directly below `from core.memory.paths import …`:

```python
from core.reflex.attention import (
    attention_add,
    attention_domains,
    attention_list,
    attention_remove,
    attention_seen_list,
)
```

Below `class TriggerEnabledRequest(BaseModel)`, add:

```python
class AttentionUpdate(BaseModel):
    """Entities to add to (`allow`) or remove from (`ask`) a domain's attention set."""

    allow: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)


_DOMAIN_RE = re.compile(r"^[a-z0-9_]{1,64}$")
```

Then add a helper directly below `_librarian_status` (from Task 7):

```python
async def _attention_domain(r: AioRedis, domain: str) -> dict[str, Any]:
    return {
        "domain": domain,
        "members": await attention_list(r, domain),
        "seen": await attention_seen_list(r, domain),
    }
```

Inside `create_admin_router`, directly before `return router`:

```python
    @router.get("/attention")
    async def attention(request: Request) -> dict[str, Any]:
        """Every domain's attention set and its sticky ``:seen`` companion."""
        r = _redis(request)
        try:
            domains = await attention_domains(r)
            return {"domains": [await _attention_domain(r, d) for d in domains]}
        except Exception as exc:
            logger.warning("Attention read failed: {}", exc)
            return {"domains": []}

    @router.put("/attention/{domain}")
    async def update_attention(
        request: Request, domain: str, body: AttentionUpdate
    ) -> dict[str, Any]:
        """Add (`allow`) or sticky-remove (`ask`) entities for one domain."""
        if not _DOMAIN_RE.match(domain):
            raise HTTPException(status_code=400, detail="Invalid domain")
        r = _redis(request)
        for entity_id in body.allow:
            await attention_add(r, domain, entity_id)
        for entity_id in body.ask:
            await attention_remove(r, domain, entity_id)
        logger.info(
            "Attention set '{}' updated via admin: +{} -{}",
            domain,
            len(body.allow),
            len(body.ask),
        )
        return await _attention_domain(r, domain)
```

- [ ] **Step 8: Run the admin tests**

Run: `uv run pytest tests/core/channels/test_admin_api.py tests/core/reflex/test_attention.py -v`
Expected: all PASS.

- [ ] **Step 9: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean. (`core.channels` already depends on `core.reflex` — `core/channels/service_credentials.py:32` imports `core.reflex.runner` — so the new import introduces no cycle.)

```bash
git add core/reflex/attention.py core/channels/admin_api.py tests/core/reflex/test_attention.py tests/core/channels/test_admin_api.py
git commit -m "feat(admin): read and edit the reflex attention set over HTTP"
```

---

### Task 10: Session metadata — `ip`, `user_agent`, `channel` on every auth session

The Sessions sheet ("iPhone · PWA · 192.0.2.10 · 2 h ago") needs each session to know where it came from. Both `register/complete` and `login/complete` build the session hash with copy-pasted code; this task folds them into one `_start_session` helper that also records the caller's address, user agent and the client `channel` (`web` | `pwa` | `ios`, sent as `_channel` in the completion body — same convention as `_challenge_id`).

**Files:**
- Modify: `core/identity/auth_routes.py` (module helpers after `_to_transports`; `_start_session` inside `create_auth_router`; the two session blocks at lines 181-200 and 268-288)
- Test: `tests/core/identity/test_auth_routes.py`

- [ ] **Step 1: Write the failing tests**

Change the imports at the top of `tests/core/identity/test_auth_routes.py` to:

```python
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from httpx import Response

from core.identity.auth_routes import create_auth_router
from core.identity.credentials import CredentialStore
from shared.streams import AUTH_SESSION_PREFIX
```

Add module-level helpers directly after the `client` fixture:

```python
_CRED_ID = "AQID"  # base64url of b"\x01\x02\x03"


async def _register_test_passkey(store: CredentialStore) -> None:
    await store.save_credential(
        credential_id=_CRED_ID,
        public_key=b"\x03",
        sign_count=0,
        device_name="Phone",
        transports=["internal"],
    )


def _passkey_login(
    client: TestClient,
    redis_mock: AsyncMock,
    *,
    body_extra: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """Drive /login/complete with the WebAuthn signature check patched out."""
    redis_mock.get = AsyncMock(return_value=b"AQID")  # the stored challenge, base64url
    verification = MagicMock()
    verification.new_sign_count = 1
    body: dict[str, object] = {
        "_challenge_id": "c1",
        "id": _CRED_ID,
        "rawId": _CRED_ID,
        "type": "public-key",
        "response": {"clientDataJSON": "e30", "authenticatorData": "e30", "signature": "e30"},
    }
    body.update(body_extra or {})
    with patch(
        "core.identity.auth_routes.verify_authentication_response", return_value=verification
    ):
        return client.post("/api/auth/login/complete", json=body, headers=headers)
```

Append the test class:

```python
class TestSessionMetadata:
    """Every session records where it came from (spec §10: sessions sheet)."""

    @pytest.mark.asyncio
    async def test_login_records_ip_user_agent_and_channel(
        self, store: CredentialStore, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        await _register_test_passkey(store)

        resp = _passkey_login(
            client,
            redis_mock,
            body_extra={"_channel": "pwa"},
            headers={"user-agent": "AlfredPWA/1.0"},
        )

        assert resp.status_code == 200
        key = redis_mock.hset.call_args[0][0]
        mapping = redis_mock.hset.call_args.kwargs["mapping"]
        assert key.startswith(AUTH_SESSION_PREFIX)
        assert mapping["authenticated"] == "1"
        assert mapping["credential_id"] == _CRED_ID
        assert mapping["channel"] == "pwa"
        assert mapping["user_agent"] == "AlfredPWA/1.0"
        assert mapping["ip"] == "testclient"
        datetime.fromisoformat(mapping["created_at"])
        assert "alfred_auth=" in resp.headers["set-cookie"]

    @pytest.mark.asyncio
    async def test_unknown_or_missing_channel_is_web(
        self, store: CredentialStore, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        await _register_test_passkey(store)

        _passkey_login(client, redis_mock, body_extra={"_channel": "toaster"})
        assert redis_mock.hset.call_args.kwargs["mapping"]["channel"] == "web"

        _passkey_login(client, redis_mock)
        assert redis_mock.hset.call_args.kwargs["mapping"]["channel"] == "web"

    def test_registration_records_channel_too(
        self, store: CredentialStore, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        redis_mock.get = AsyncMock(return_value=b"AQID")
        verification = MagicMock()
        verification.credential_id = b"\x01\x02\x03"
        verification.credential_public_key = b"\x03"
        verification.sign_count = 0
        with patch(
            "core.identity.auth_routes.verify_registration_response", return_value=verification
        ):
            resp = client.post(
                "/api/auth/register/complete",
                json={
                    "_challenge_id": "c1",
                    "_device_name": "Phone",
                    "_channel": "ios",
                    "id": _CRED_ID,
                    "rawId": _CRED_ID,
                    "type": "public-key",
                    "response": {"clientDataJSON": "e30", "attestationObject": "e30"},
                },
                headers={"user-agent": "AlfredApp/2.0"},
            )

        assert resp.status_code == 200
        mapping = redis_mock.hset.call_args.kwargs["mapping"]
        assert mapping["channel"] == "ios"
        assert mapping["user_agent"] == "AlfredApp/2.0"
        assert mapping["ip"] == "testclient"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/core/identity/test_auth_routes.py::TestSessionMetadata -v`
Expected: all three FAIL with `KeyError: 'channel'`.

- [ ] **Step 3: Implement**

In `core/identity/auth_routes.py`, add after `_to_transports`:

```python
_SESSION_CHANNELS = frozenset({"web", "pwa", "ios"})


def _session_channel(body: dict[str, Any]) -> str:
    """Which client completed the ceremony — ``_channel`` in the completion body."""
    channel = body.get("_channel", "web")
    return channel if channel in _SESSION_CHANNELS else "web"


def _set_session_cookie(response: JSONResponse, request: Request, session_id: str) -> None:
    """Attach the HttpOnly session cookie (Secure whenever the request was HTTPS)."""
    response.set_cookie(
        key="alfred_auth",
        value=session_id,
        max_age=_AUTH_SESSION_TTL,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
```

Inside `create_auth_router`, directly after `router = APIRouter(prefix="/api/auth", tags=["auth"])`:

```python
    async def _start_session(request: Request, credential_id: str, channel: str) -> str:
        """Create an authenticated session hash (TTL 8h) and return its id."""
        session_id = str(uuid.uuid4())
        key = f"{AUTH_SESSION_PREFIX}{session_id}"
        await redis.hset(
            key,
            mapping={
                "authenticated": "1",
                "credential_id": credential_id,
                "created_at": datetime.now(UTC).isoformat(),
                "ip": request.client.host if request.client else "",
                "user_agent": request.headers.get("user-agent", "")[:200],
                "channel": channel,
            },
        )
        await redis.expire(key, _AUTH_SESSION_TTL)
        return session_id
```

In `register_complete`, replace everything from `session_id = str(uuid.uuid4())` to `return response` with:

```python
        session_id = await _start_session(request, credential_id, _session_channel(body))
        response = JSONResponse({"status": "ok", "credential_id": credential_id})
        _set_session_cookie(response, request, session_id)
        return response
```

In `login_complete`, replace the same span (from `session_id = str(uuid.uuid4())` to `return response`) with:

```python
        session_id = await _start_session(request, cred.credential_id, _session_channel(body))
        response = JSONResponse({"status": "ok"})
        _set_session_cookie(response, request, session_id)
        return response
```

- [ ] **Step 4: Run the auth route tests**

Run: `uv run pytest tests/core/identity/ -v`
Expected: all PASS — including plan 0's `TestSessionLifetime` (the cookie attributes are unchanged) and `tests/core/identity/test_auth_middleware.py` (the middleware only reads `authenticated`/`credential_id`; extra hash fields are ignored).

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean.

```bash
git add core/identity/auth_routes.py tests/core/identity/test_auth_routes.py
git commit -m "feat(auth): record ip, user agent and channel on every session"
```

---

### Task 11: Sessions — `GET /api/auth/sessions`, `DELETE /api/auth/sessions/{id}`, `POST /api/auth/logout?all=1`

The Sessions sheet lists every live session, marks the current one, and can end any of them (or all of them). Auth sessions live at `alfred:auth:{session_id}` — not to be confused with the admin router's `alfred:sessions:*` (chat sessions).

**Files:**
- Modify: `core/identity/auth_routes.py` (imports; module helpers `_decode_session`/`_key_suffix`; `current_session` + `_all_sessions` inside the router; two new routes; `logout`)
- Test: `tests/core/identity/test_auth_routes.py`

- [ ] **Step 1: Write the failing tests**

Add `from collections.abc import AsyncIterator` to the test imports and, after `_passkey_login`, these helpers:

```python
def _aiter(items: list[str]) -> AsyncIterator[str]:
    async def gen() -> AsyncIterator[str]:
        for item in items:
            yield item

    return gen()


def _session_record(credential_id: str, channel: str, created_at: str) -> dict[bytes, bytes]:
    """A session hash as the production pool returns it (decode_responses=False)."""
    return {
        b"authenticated": b"1",
        b"credential_id": credential_id.encode(),
        b"created_at": created_at.encode(),
        b"ip": b"203.0.113.7",
        b"user_agent": b"AlfredPWA/1.0",
        b"channel": channel.encode(),
    }
```

Append the test class:

```python
class TestSessionsApi:
    @pytest.fixture
    def sessions(self, redis_mock: AsyncMock) -> dict[str, dict[bytes, bytes]]:
        sessions = {
            f"{AUTH_SESSION_PREFIX}s-current": _session_record(
                _CRED_ID, "pwa", "2026-09-04T08:00:00+00:00"
            ),
            f"{AUTH_SESSION_PREFIX}s-older": _session_record(
                "other-cred", "web", "2026-09-03T08:00:00+00:00"
            ),
            f"{AUTH_SESSION_PREFIX}s-pending": {b"authenticated": b"0"},
        }
        redis_mock.hgetall = AsyncMock(side_effect=lambda key: sessions.get(key, {}))
        redis_mock.scan_iter = MagicMock(side_effect=lambda match="*": _aiter(list(sessions)))
        redis_mock.ttl = AsyncMock(return_value=1200)
        redis_mock.delete = AsyncMock(return_value=1)
        return sessions

    @pytest.mark.asyncio
    async def test_list_sessions_newest_first_with_current_marked(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.get("/api/auth/sessions")

        assert resp.status_code == 200
        body = resp.json()["sessions"]
        # newest first; the unauthenticated record is skipped
        assert [s["session_id"] for s in body] == ["s-current", "s-older"]
        assert body[0] == {
            "session_id": "s-current",
            "credential_id": _CRED_ID,
            "device_name": "Phone",
            "channel": "pwa",
            "ip": "203.0.113.7",
            "user_agent": "AlfredPWA/1.0",
            "created_at": "2026-09-04T08:00:00+00:00",
            "expires_in": 1200,
            "current": True,
        }
        assert body[1]["device_name"] == "Unknown device"
        assert body[1]["current"] is False

    def test_sessions_require_an_authenticated_cookie(
        self, client: TestClient, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        assert client.get("/api/auth/sessions").status_code == 401
        assert client.delete("/api/auth/sessions/s-older").status_code == 401
        client.cookies.set("alfred_auth", "s-pending")
        assert client.get("/api/auth/sessions").status_code == 401

    def test_delete_another_session(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete("/api/auth/sessions/s-older")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-older")
        assert "set-cookie" not in resp.headers

    def test_delete_own_session_clears_cookie(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete("/api/auth/sessions/s-current")

        assert resp.status_code == 200
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-current")
        assert "Max-Age=0" in resp.headers["set-cookie"]

    def test_logout_all_ends_every_authenticated_session(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        client.cookies.set("alfred_auth", "s-current")

        resp = client.post("/api/auth/logout?all=1")

        assert resp.status_code == 200
        deleted = {call.args[0] for call in redis_mock.delete.await_args_list}
        assert deleted == {
            f"{AUTH_SESSION_PREFIX}s-current",
            f"{AUTH_SESSION_PREFIX}s-older",
        }
        assert "Max-Age=0" in resp.headers["set-cookie"]

    def test_logout_all_needs_an_authenticated_cookie(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        client.cookies.set("alfred_auth", "s-pending")

        client.post("/api/auth/logout?all=1")

        # Only its own (unauthenticated) key is touched — never everyone else's
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-pending")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/core/identity/test_auth_routes.py::TestSessionsApi -v`
Expected: the list/delete tests FAIL with `assert 404 == 200` / `assert 404 == 401` (Starlette 404 for unknown routes, or 405 for `DELETE` on a path that doesn't exist); `test_logout_all_ends_every_authenticated_session` FAILS on the deleted-set comparison (only `s-current` is deleted today); `test_logout_all_needs_an_authenticated_cookie` PASSES already.

- [ ] **Step 3: Implement**

In `core/identity/auth_routes.py`, change the fastapi import to:

```python
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
```

Add two module helpers after `_set_session_cookie` (from Task 10):

```python
def _decode_session(raw: Any) -> dict[str, str]:
    """Normalise a session hash (bytes or str keys and values) to str → str."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        key = k.decode() if isinstance(k, bytes) else str(k)
        out[key] = v.decode(errors="replace") if isinstance(v, bytes) else str(v)
    return out


def _key_suffix(key: Any, prefix: str) -> str:
    """The part of a Redis key after ``prefix`` (keys arrive as bytes on the prod pool)."""
    text = key.decode() if isinstance(key, bytes) else str(key)
    return text[len(prefix) :]
```

Inside `create_auth_router`, directly after `_start_session` (from Task 10):

```python
    async def current_session(
        alfred_auth: str | None = Cookie(default=None),
    ) -> tuple[str, dict[str, str]]:
        """The caller's authenticated session as ``(session_id, record)``, else 401.

        Reads Redis directly rather than trusting ``request.state`` so the
        router also works without ``AuthCookieMiddleware`` (as in its tests).
        """
        if not alfred_auth:
            raise HTTPException(status_code=401, detail="Authentication required")
        record = _decode_session(await redis.hgetall(f"{AUTH_SESSION_PREFIX}{alfred_auth}"))
        if record.get("authenticated") != "1":
            raise HTTPException(status_code=401, detail="Authentication required")
        return alfred_auth, record

    async def _all_sessions() -> list[tuple[str, dict[str, str]]]:
        """Every live, authenticated session as ``(session_id, record)``."""
        found: list[tuple[str, dict[str, str]]] = []
        async for key in redis.scan_iter(match=f"{AUTH_SESSION_PREFIX}*"):
            session_id = _key_suffix(key, AUTH_SESSION_PREFIX)
            record = _decode_session(await redis.hgetall(f"{AUTH_SESSION_PREFIX}{session_id}"))
            if record.get("authenticated") == "1":
                found.append((session_id, record))
        return found
```

Add the two routes directly before `@router.post("/logout")`:

```python
    @router.get("/sessions")
    async def list_sessions(
        current: tuple[str, dict[str, str]] = Depends(current_session),
    ) -> JSONResponse:
        """Every live session, newest first, with the caller's marked ``current``."""
        current_id, _ = current
        names = {c.credential_id: c.device_name for c in await store.list_credentials()}
        sessions: list[dict[str, Any]] = []
        for session_id, record in await _all_sessions():
            ttl = await redis.ttl(f"{AUTH_SESSION_PREFIX}{session_id}")
            credential_id = record.get("credential_id", "")
            sessions.append(
                {
                    "session_id": session_id,
                    "credential_id": credential_id,
                    "device_name": names.get(credential_id, "Unknown device"),
                    "channel": record.get("channel", "web"),
                    "ip": record.get("ip", ""),
                    "user_agent": record.get("user_agent", ""),
                    "created_at": record.get("created_at", ""),
                    "expires_in": max(int(ttl), 0),
                    "current": session_id == current_id,
                }
            )
        sessions.sort(key=lambda s: str(s["created_at"]), reverse=True)
        return JSONResponse({"sessions": sessions})

    @router.delete("/sessions/{session_id}")
    async def delete_session(
        session_id: str,
        current: tuple[str, dict[str, str]] = Depends(current_session),
    ) -> JSONResponse:
        """End one session. Ending your own also clears the cookie."""
        deleted = await redis.delete(f"{AUTH_SESSION_PREFIX}{session_id}")
        logger.info("Auth session {} ended via the sessions API", session_id)
        response = JSONResponse({"deleted": bool(deleted)})
        if session_id == current[0]:
            response.delete_cookie(key="alfred_auth")
        return response
```

Replace the `logout` route:

```python
    @router.post("/logout")
    async def logout(
        alfred_auth: str | None = Cookie(default=None),
        all_sessions: bool = Query(default=False, alias="all"),
    ) -> JSONResponse:
        """End the caller's session — or every session with ``?all=1``.

        ``all`` is only honoured for an authenticated caller, so a guessed
        cookie value can never log the real user out of every device.
        """
        if alfred_auth:
            own_key = f"{AUTH_SESSION_PREFIX}{alfred_auth}"
            record = _decode_session(await redis.hgetall(own_key))
            if all_sessions and record.get("authenticated") == "1":
                for session_id, _ in await _all_sessions():
                    await redis.delete(f"{AUTH_SESSION_PREFIX}{session_id}")
                logger.info("All auth sessions ended via logout?all=1")
            else:
                await redis.delete(own_key)

        response = JSONResponse({"status": "ok"})
        response.delete_cookie(key="alfred_auth")
        return response
```

- [ ] **Step 4: Run the auth tests**

Run: `uv run pytest tests/core/identity/ -v`
Expected: all PASS, including the pre-existing `TestLogout.test_clears_session_and_cookie` (no `?all`, so exactly one `delete`).

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean.

```bash
git add core/identity/auth_routes.py tests/core/identity/test_auth_routes.py
git commit -m "feat(auth): list and end sessions, logout everywhere with ?all=1"
```

---

### Task 12: Passkeys — `GET /api/auth/credentials`, `DELETE /api/auth/credentials/{id}`

The Passkeys sheet lists registered credentials and removes one — never the last one, or the user locks themselves out. Removing a passkey also ends every session that was opened with it.

**Files:**
- Modify: `core/identity/auth_routes.py` (two routes inside `create_auth_router`, after `delete_session`)
- Test: `tests/core/identity/test_auth_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/identity/test_auth_routes.py` (reuses `TestSessionsApi`'s `sessions` fixture shape, redefined here so the class stands alone):

```python
class TestPasskeysApi:
    @pytest.fixture
    def sessions(self, redis_mock: AsyncMock) -> dict[str, dict[bytes, bytes]]:
        sessions = {
            f"{AUTH_SESSION_PREFIX}s-current": _session_record(
                _CRED_ID, "pwa", "2026-09-04T08:00:00+00:00"
            ),
            f"{AUTH_SESSION_PREFIX}s-laptop": _session_record(
                "laptop-cred", "web", "2026-09-03T08:00:00+00:00"
            ),
        }
        redis_mock.hgetall = AsyncMock(side_effect=lambda key: sessions.get(key, {}))
        redis_mock.scan_iter = MagicMock(side_effect=lambda match="*": _aiter(list(sessions)))
        redis_mock.delete = AsyncMock(return_value=1)
        return sessions

    @pytest.mark.asyncio
    async def test_list_credentials_marks_current(
        self,
        store: CredentialStore,
        client: TestClient,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        await store.save_credential(
            credential_id="laptop-cred",
            public_key=b"\x04",
            sign_count=0,
            device_name="Laptop",
            transports=["usb"],
        )
        client.cookies.set("alfred_auth", "s-current")

        resp = client.get("/api/auth/credentials")

        assert resp.status_code == 200
        creds = {c["credential_id"]: c for c in resp.json()["credentials"]}
        assert set(creds) == {_CRED_ID, "laptop-cred"}
        assert creds[_CRED_ID]["device_name"] == "Phone"
        assert creds[_CRED_ID]["transports"] == ["internal"]
        assert creds[_CRED_ID]["current"] is True
        assert creds["laptop-cred"]["current"] is False
        assert set(creds[_CRED_ID]) == {
            "credential_id",
            "device_name",
            "transports",
            "created_at",
            "last_used_at",
            "current",
        }

    def test_credentials_require_auth(
        self, client: TestClient, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        assert client.get("/api/auth/credentials").status_code == 401
        assert client.delete("/api/auth/credentials/laptop-cred").status_code == 401

    @pytest.mark.asyncio
    async def test_delete_credential_ends_its_sessions(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        await store.save_credential(
            credential_id="laptop-cred",
            public_key=b"\x04",
            sign_count=0,
            device_name="Laptop",
            transports=["usb"],
        )
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete("/api/auth/credentials/laptop-cred")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "sessions_ended": 1}
        assert await store.get_credential("laptop-cred") is None
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-laptop")
        assert "set-cookie" not in resp.headers

    @pytest.mark.asyncio
    async def test_delete_own_credential_clears_cookie(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        await store.save_credential(
            credential_id="laptop-cred",
            public_key=b"\x04",
            sign_count=0,
            device_name="Laptop",
            transports=["usb"],
        )
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/credentials/{_CRED_ID}")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "sessions_ended": 1}
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-current")
        assert "Max-Age=0" in resp.headers["set-cookie"]

    @pytest.mark.asyncio
    async def test_cannot_delete_last_credential(
        self,
        store: CredentialStore,
        client: TestClient,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/credentials/{_CRED_ID}")

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot remove the last passkey — register another first"
        assert await store.get_credential(_CRED_ID) is not None

    @pytest.mark.asyncio
    async def test_delete_unknown_credential_is_404(
        self,
        store: CredentialStore,
        client: TestClient,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete("/api/auth/credentials/nope")

        assert resp.status_code == 404
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/core/identity/test_auth_routes.py::TestPasskeysApi -v`
Expected: all six FAIL — the routes do not exist yet (`404`/`405` where `200`, `401` or `409` is expected).

- [ ] **Step 3: Implement**

In `core/identity/auth_routes.py`, inside `create_auth_router`, directly after `delete_session` (Task 11) and before `@router.post("/logout")`:

```python
    @router.get("/credentials")
    async def list_passkeys(
        current: tuple[str, dict[str, str]] = Depends(current_session),
    ) -> JSONResponse:
        """Every registered passkey; ``current`` is the one this session used."""
        _, record = current
        credentials = [
            {
                "credential_id": c.credential_id,
                "device_name": c.device_name,
                "transports": c.transports,
                "created_at": c.created_at,
                "last_used_at": c.last_used_at,
                "current": c.credential_id == record.get("credential_id"),
            }
            for c in await store.list_credentials()
        ]
        return JSONResponse({"credentials": credentials})

    @router.delete("/credentials/{credential_id}")
    async def delete_passkey(
        credential_id: str,
        current: tuple[str, dict[str, str]] = Depends(current_session),
    ) -> JSONResponse:
        """Remove a passkey and end every session it opened. Never the last one."""
        if await store.get_credential(credential_id) is None:
            raise HTTPException(status_code=404, detail="Passkey not found")
        if len(await store.list_credentials()) <= 1:
            raise HTTPException(
                status_code=409,
                detail="Cannot remove the last passkey — register another first",
            )

        await store.delete_credential(credential_id)
        ended = 0
        ended_own = False
        for session_id, record in await _all_sessions():
            if record.get("credential_id") != credential_id:
                continue
            await redis.delete(f"{AUTH_SESSION_PREFIX}{session_id}")
            ended += 1
            ended_own = ended_own or session_id == current[0]
        logger.info("Passkey {} removed, {} session(s) ended", credential_id, ended)

        response = JSONResponse({"deleted": True, "sessions_ended": ended})
        if ended_own:
            response.delete_cookie(key="alfred_auth")
        return response
```

- [ ] **Step 4: Run the auth tests**

Run: `uv run pytest tests/core/identity/ -v`
Expected: all PASS.

- [ ] **Step 5: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean.

```bash
git add core/identity/auth_routes.py tests/core/identity/test_auth_routes.py
git commit -m "feat(auth): list and remove passkeys, never the last one"
```

---

### Task 13: Pairing code — register a new device from anywhere

Today `register/begin` and `register/complete` are gated by `require_trusted_network`, which means a new phone can only be enrolled from the LAN. The design's "Add this device" flow instead shows a 6-digit code on an already-signed-in device; the new device sends it as an `X-Pairing-Code` header on both registration calls and is let through from any network. (Spec §10 says `pairing_code`; a header keeps `RegisterBeginRequest` unchanged and works identically on `complete`, whose body is the raw WebAuthn credential.) The code lives 5 minutes, is single-use (consumed when the passkey is saved), and is burned after 10 wrong guesses.

**Files:**
- Modify: `shared/streams.py` (two keys after `WEBAUTHN_CHALLENGE_PREFIX`)
- Modify: `core/identity/auth_routes.py` (imports; `_PAIRING_TTL`/`_PAIRING_MAX_FAILURES`; `create_auth_router` signature; `_pairing_code_valid`, `registration_gate`, `POST /pairing`; both register routes)
- Test: `tests/core/identity/test_auth_routes.py` (three existing tests rewired, one new class)

- [ ] **Step 1: Add the Redis keys**

In `shared/streams.py`, after `WEBAUTHN_CHALLENGE_PREFIX`:

```python
WEBAUTHN_PAIRING_KEY: str = "alfred:webauthn:pairing"  # the active 6-digit code, 5 min
WEBAUTHN_PAIRING_FAILS_KEY: str = "alfred:webauthn:pairing:fails"  # wrong guesses; burns at 10
```

- [ ] **Step 2: Rewire the three tests that override the network gate**

The gate is no longer a bare `Depends(require_trusted_network)`, so `app.dependency_overrides[require_trusted_network]` stops reaching it. Pass the gate into `create_auth_router` instead.

Change the fastapi import at the top of `tests/core/identity/test_auth_routes.py` to:

```python
from fastapi import FastAPI, HTTPException, Request
```

Change the `shared.streams` import to:

```python
from shared.streams import (
    AUTH_SESSION_PREFIX,
    WEBAUTHN_CHALLENGE_PREFIX,
    WEBAUTHN_PAIRING_FAILS_KEY,
    WEBAUTHN_PAIRING_KEY,
)
```

Replace the module-level `_reject_network` with:

```python
async def _reject_network(request: Request) -> None:
    """Network gate that always rejects as untrusted."""
    raise HTTPException(status_code=403, detail="Access restricted to trusted networks")


async def _allow_network(request: Request) -> None:
    """Network gate that always passes."""


def _client_with_gate(
    store: CredentialStore,
    redis_mock: AsyncMock,
    gate: Callable[[Request], Awaitable[None]],
) -> TestClient:
    app = FastAPI()
    app.include_router(create_auth_router(store=store, redis=redis_mock, trusted_network_dep=gate))
    return TestClient(app)
```

and add `from collections.abc import AsyncIterator, Awaitable, Callable` in place of the `AsyncIterator` import from Task 11.

Replace `TestRegistrationBegin.test_rejects_untrusted_network`:

```python
    def test_rejects_untrusted_network(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        untrusted = _client_with_gate(store, redis_mock, _reject_network)

        resp = untrusted.post("/api/auth/register/begin", json={"device_name": "Test"})

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Access restricted to trusted networks"
```

Replace `TestRegistrationComplete.test_rejects_untrusted_network`:

```python
    def test_rejects_untrusted_network(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        untrusted = _client_with_gate(store, redis_mock, _reject_network)

        resp = untrusted.post("/api/auth/register/complete", json={"credential": "{}"})

        assert resp.status_code == 403
```

In the exclude-credentials test near line 448, replace `trusted_network_dep=lambda: None,` with `trusted_network_dep=_allow_network,` and delete the comment line above it (`# trusted_network_dep=lambda: None bypasses the IP gate in tests`).

- [ ] **Step 3: Write the failing pairing tests**

Append to `tests/core/identity/test_auth_routes.py`:

```python
def _registration_options_patched() -> Any:
    """Patch the WebAuthn option builders so register/begin runs without a real RP."""
    mock_options = MagicMock()
    mock_options.challenge = b"\x01\x02\x03"
    return (
        patch("core.identity.auth_routes.generate_registration_options", return_value=mock_options),
        patch("core.identity.auth_routes.options_to_json", return_value='{"ok": true}'),
    )


class TestPairingCode:
    @pytest.fixture
    def untrusted(self, store: CredentialStore, redis_mock: AsyncMock) -> TestClient:
        return _client_with_gate(store, redis_mock, _reject_network)

    @pytest.fixture
    def active_code(self, redis_mock: AsyncMock) -> str:
        redis_mock.get = AsyncMock(
            side_effect=lambda key: b"123456" if key == WEBAUTHN_PAIRING_KEY else None
        )
        redis_mock.incr = AsyncMock(return_value=1)
        return "123456"

    def test_minting_requires_auth(self, client: TestClient) -> None:
        assert client.post("/api/auth/pairing").status_code == 401

    def test_mint_stores_a_six_digit_code_for_five_minutes(
        self, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        redis_mock.hgetall = AsyncMock(
            return_value={b"authenticated": b"1", b"credential_id": b"AQID"}
        )
        client.cookies.set("alfred_auth", "s-current")

        resp = client.post("/api/auth/pairing")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["code"]) == 6 and body["code"].isdigit()
        assert body["ttl_seconds"] == 300
        assert "expires_at" in body
        redis_mock.set.assert_awaited_once_with(WEBAUTHN_PAIRING_KEY, body["code"], ex=300)
        redis_mock.delete.assert_awaited_once_with(WEBAUTHN_PAIRING_FAILS_KEY)

    def test_valid_code_lets_an_untrusted_network_begin_registration(
        self, untrusted: TestClient, redis_mock: AsyncMock, active_code: str
    ) -> None:
        gen, to_json = _registration_options_patched()
        with gen, to_json:
            resp = untrusted.post(
                "/api/auth/register/begin",
                json={"device_name": "Phone"},
                headers={"X-Pairing-Code": active_code},
            )

        assert resp.status_code == 200
        redis_mock.incr.assert_not_awaited()

    def test_wrong_code_is_rejected_and_counted(
        self, untrusted: TestClient, redis_mock: AsyncMock, active_code: str
    ) -> None:
        resp = untrusted.post(
            "/api/auth/register/begin",
            json={"device_name": "Phone"},
            headers={"X-Pairing-Code": "000000"},
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Invalid or expired pairing code"
        redis_mock.incr.assert_awaited_once_with(WEBAUTHN_PAIRING_FAILS_KEY)
        redis_mock.expire.assert_awaited_once_with(WEBAUTHN_PAIRING_FAILS_KEY, 300)
        redis_mock.delete.assert_not_awaited()

    def test_tenth_wrong_guess_burns_the_code(
        self, untrusted: TestClient, redis_mock: AsyncMock, active_code: str
    ) -> None:
        redis_mock.incr = AsyncMock(return_value=10)

        resp = untrusted.post(
            "/api/auth/register/begin",
            json={"device_name": "Phone"},
            headers={"X-Pairing-Code": "000000"},
        )

        assert resp.status_code == 403
        redis_mock.delete.assert_awaited_once_with(WEBAUTHN_PAIRING_KEY)

    def test_no_code_off_network_still_hits_the_network_gate(
        self, untrusted: TestClient, redis_mock: AsyncMock
    ) -> None:
        resp = untrusted.post("/api/auth/register/begin", json={"device_name": "Phone"})

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Access restricted to trusted networks"

    def test_code_is_consumed_when_the_passkey_is_saved(
        self, untrusted: TestClient, redis_mock: AsyncMock, active_code: str
    ) -> None:
        def _get(key: str) -> bytes | None:
            if key == WEBAUTHN_PAIRING_KEY:
                return b"123456"
            if key.startswith(WEBAUTHN_CHALLENGE_PREFIX):
                return b"AQID"
            return None

        redis_mock.get = AsyncMock(side_effect=_get)
        verification = MagicMock()
        verification.credential_id = b"\x01\x02\x03"
        verification.credential_public_key = b"\x03"
        verification.sign_count = 0

        with patch(
            "core.identity.auth_routes.verify_registration_response", return_value=verification
        ):
            resp = untrusted.post(
                "/api/auth/register/complete",
                json={
                    "_challenge_id": "c1",
                    "_device_name": "Phone",
                    "id": "AQID",
                    "rawId": "AQID",
                    "type": "public-key",
                    "response": {"clientDataJSON": "e30", "attestationObject": "e30"},
                },
                headers={"X-Pairing-Code": active_code},
            )

        assert resp.status_code == 200
        deleted = [call.args[0] for call in redis_mock.delete.await_args_list]
        assert deleted == [
            f"{WEBAUTHN_CHALLENGE_PREFIX}c1",
            WEBAUTHN_PAIRING_KEY,
            WEBAUTHN_PAIRING_FAILS_KEY,
        ]

    def test_trusted_network_registration_does_not_touch_pairing_keys(
        self, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        gen, to_json = _registration_options_patched()
        with gen, to_json:
            resp = client.post("/api/auth/register/begin", json={"device_name": "Phone"})

        assert resp.status_code == 200
        redis_mock.get.assert_not_awaited()
        redis_mock.incr.assert_not_awaited()
```

Add `from typing import Any` to the test imports.

- [ ] **Step 4: Run to verify the right things fail**

Run: `uv run pytest tests/core/identity/test_auth_routes.py -v`
Expected: `TestPairingCode` — `test_minting_requires_auth` FAILS with 404, `test_mint_stores…` FAILS with 404, the four code-header tests FAIL with `403` bodies of `"Access restricted to trusted networks"` (the header is ignored) or `assert_not_awaited`/`assert_awaited` errors, `test_no_code_off_network…` and `test_trusted_network_registration…` PASS. The two rewired `test_rejects_untrusted_network` tests and the exclude-credentials test (now on `_allow_network`) PASS.

- [ ] **Step 5: Implement**

In `core/identity/auth_routes.py`:

Imports — add `import secrets`, change the datetime and fastapi lines, and extend the `shared.streams` import:

```python
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Request
```

```python
from shared.streams import (
    AUTH_SESSION_PREFIX,
    WEBAUTHN_CHALLENGE_PREFIX,
    WEBAUTHN_PAIRING_FAILS_KEY,
    WEBAUTHN_PAIRING_KEY,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from core.identity.credentials import CredentialStore
```

Constants, after `_CHALLENGE_TTL`:

```python
_PAIRING_TTL = 300  # a pairing code lives 5 minutes
_PAIRING_MAX_FAILURES = 10  # wrong guesses before the active code is burned
```

Signature and docstring of `create_auth_router`:

```python
def create_auth_router(
    *,
    store: CredentialStore,
    redis: Any,
    trusted_network_dep: Callable[[Request], Awaitable[None]] | None = None,
) -> APIRouter:
    """Build the auth APIRouter with all WebAuthn endpoints.

    Args:
        store: WebAuthn credential store.
        redis: Async Redis connection for sessions/challenges.
        trusted_network_dep: Async callable that raises 403 for an untrusted
            ``Request``. If None, imports ``require_trusted_network`` from
            web_server (backwards compat). Registration also passes with a
            valid ``X-Pairing-Code`` header regardless of network.
    """
    if trusted_network_dep is None:
        from core.channels.web_server import require_trusted_network

        trusted_network_dep = require_trusted_network
    network_gate: Callable[[Request], Awaitable[None]] = trusted_network_dep
```

(The `if` block already exists; the `network_gate` line is new. Nested functions do not
see a parameter's narrowed type, so `registration_gate` closes over `network_gate`, which
is declared non-optional.)

Inside the router, directly after `_all_sessions` (Task 11):

```python
    async def _pairing_code_valid(code: str) -> bool:
        """Constant-time check against the active code; count and cap wrong guesses."""
        active = await redis.get(WEBAUTHN_PAIRING_KEY)
        if isinstance(active, bytes):
            active = active.decode()
        if active and secrets.compare_digest(str(active).encode(), code.encode()):
            return True
        fails = int(await redis.incr(WEBAUTHN_PAIRING_FAILS_KEY))
        await redis.expire(WEBAUTHN_PAIRING_FAILS_KEY, _PAIRING_TTL)
        if fails >= _PAIRING_MAX_FAILURES:
            await redis.delete(WEBAUTHN_PAIRING_KEY)
            logger.warning("Pairing code burned after {} wrong guesses", fails)
        return False

    async def registration_gate(
        request: Request,
        x_pairing_code: str | None = Header(default=None, alias="X-Pairing-Code"),
    ) -> None:
        """Let registration through with a valid pairing code, else require the LAN."""
        if x_pairing_code is not None:
            code = x_pairing_code.strip()
            if not await _pairing_code_valid(code):
                raise HTTPException(status_code=403, detail="Invalid or expired pairing code")
            request.state.pairing_code = code
            return
        await network_gate(request)

    @router.post("/pairing")
    async def create_pairing_code(
        _: tuple[str, dict[str, str]] = Depends(current_session),
    ) -> JSONResponse:
        """Mint a one-shot 6-digit code that lets a new device register from anywhere."""
        code = f"{secrets.randbelow(10**6):06d}"
        await redis.set(WEBAUTHN_PAIRING_KEY, code, ex=_PAIRING_TTL)
        await redis.delete(WEBAUTHN_PAIRING_FAILS_KEY)
        expires_at = (datetime.now(UTC) + timedelta(seconds=_PAIRING_TTL)).isoformat()
        logger.info("Pairing code minted, valid for {}s", _PAIRING_TTL)
        return JSONResponse(
            {"code": code, "expires_at": expires_at, "ttl_seconds": _PAIRING_TTL}
        )
```

In both `register_begin` and `register_complete`, replace `_: None = Depends(trusted_network_dep),` with:

```python
        _: None = Depends(registration_gate),
```

In `register_complete`, directly after the `await store.save_credential(...)` call and before `session_id = await _start_session(...)`:

```python
        if getattr(request.state, "pairing_code", None):
            await redis.delete(WEBAUTHN_PAIRING_KEY)
            await redis.delete(WEBAUTHN_PAIRING_FAILS_KEY)
            logger.info("Pairing code consumed by new passkey {}", credential_id)
```

- [ ] **Step 6: Run the auth tests**

Run: `uv run pytest tests/core/identity/ -v`
Expected: all PASS.

- [ ] **Step 7: Run the web-server tests that mount the real router**

Run: `uv run pytest tests/core/channels/ -q`
Expected: PASS — `create_app()` still calls `create_auth_router(store=..., redis=...)` with the default gate, and `TestClient`'s `"testclient"` peer still passes `require_trusted_network`.

- [ ] **Step 8: Lint, type-check, commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean.

```bash
git add shared/streams.py core/identity/auth_routes.py tests/core/identity/test_auth_routes.py
git commit -m "feat(auth): pair a new device with a 6-digit code from any network"
```

---

### Task 14: Documentation

Every reference doc that describes a surface this plan changed. Line numbers are as of the post-plan-0 `master`; if a line has drifted, match on the quoted text.

**Files:**
- Modify: `docs/webauthn.md` (sequence diagram note, components table, security list; new "Sessions, passkeys and pairing" section)
- Modify: `docs/admin-api.md` (overview fields, streams payload, new "Attention" section + controls row)
- Modify: `docs/autonomy.md` (pending reads + `reason`)
- Modify: `docs/secrets.md` (`latency_ms` on status)
- Modify: `docs/architecture.md` (Redis key table)
- Modify: `docs/PRD.md` (three rows)
- Modify: `CLAUDE.md` (two lines)

- [ ] **Step 1: `docs/webauthn.md`**

Replace the components-table row `| Auth Routes | ... | 6 REST endpoints for registration/login/logout |` with:

```markdown
| Auth Routes | `core/identity/auth_routes.py` | 11 REST endpoints: registration/login/logout, sessions, passkeys, pairing |
```

In the "Data Stores" list, after the `Challenges` line add:

```markdown
- **Pairing code:** Redis at `alfred:webauthn:pairing` -- 5min TTL, single-use; wrong guesses counted in `alfred:webauthn:pairing:fails` (burns the code at 10)
```

In "Security Properties", replace the first bullet (`- Registration requires a trusted network (`require_trusted_network`); admin reads and` … `controls need only the session`) with:

```markdown
- Registration requires a trusted network (`require_trusted_network`) **or** a valid
  `X-Pairing-Code` header on both `register/begin` and `register/complete`; admin reads
  and controls need only the session
- Pairing codes are minted only by an authenticated session, live 5 minutes, are consumed
  the moment the new passkey is saved, and are burned after 10 wrong guesses
- The last passkey can never be deleted (409), so the account cannot lock itself out
```

Append a new section before "## Frontend Flow":

```markdown
## Sessions, passkeys and pairing

Every session hash (`alfred:auth:{session_id}`) records `credential_id`, `created_at`,
`ip`, `user_agent` and `channel` (`web` | `pwa` | `ios`, sent by the client as `_channel`
in the `*/complete` body; anything else is stored as `web`).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/auth/sessions` | session | Every live session, newest first; `current: true` marks the caller's |
| `DELETE` | `/api/auth/sessions/{session_id}` | session | End one session (clears the cookie if it is your own) |
| `POST` | `/api/auth/logout?all=1` | session | End every session; without `all` only the caller's |
| `GET` | `/api/auth/credentials` | session | Registered passkeys; `current: true` is the one this session used |
| `DELETE` | `/api/auth/credentials/{credential_id}` | session | Remove a passkey and end its sessions; 409 if it is the last one |
| `POST` | `/api/auth/pairing` | session | Mint a 6-digit pairing code: `{"code", "expires_at", "ttl_seconds": 300}` |

Pairing a new phone: the signed-in device calls `POST /api/auth/pairing` and shows the
code; the new device sends it as `X-Pairing-Code` on `register/begin` and
`register/complete` from any network. An invalid code is a 403
`"Invalid or expired pairing code"`; no code falls back to the trusted-network gate.
```

- [ ] **Step 2: `docs/admin-api.md`**

In the "### Overview" field list, after the `inference.lmstudio` line add:

```markdown
- `reflex.model` — the Reflex SLM in use (`OPENAI_COMPAT_MODEL` or `OLLAMA_MODEL` by `REFLEX_BACKEND`)
- `reflex.last_ms` / `reflex.p50_ms` — trigger-to-observation latency of the newest, and median of the last 20, `alfred:reflex:observations` entries; `null` when the stream is empty or unreadable
- `librarian.last_run_at` / `librarian.reviewed` / `librarian.next_run_at` — from the `alfred:librarian:status` hash the Librarian writes after every cycle; all `null` until it has run once
- `cost.request_count` / `cost.avg_usd` — calls made today and spend per call (`CostState`)
```

Replace the `GET /api/admin/streams` example block with:

```json
{
  "events": {"length": 1042, "last_id": "1749600000000-0", "last_ts": 1749600000.0, "rate_5m": 12.4},
  "actions": {"length": 87, "last_id": "1749599990000-0", "last_ts": 1749599990.0, "rate_5m": 0.0}
}
```

and change the "Missing streams" sentence to:

```markdown
Missing streams (stream key does not exist in Redis yet) report `{"length": 0, "last_id": null, "last_ts": null, "rate_5m": 0.0}` — never raises. `rate_5m` is entries per minute over the last five minutes (sampled from the newest 5000 entries; `0.0` if the read fails).
```

Add a new section between "### Devices" and "### Controls":

```markdown
### Attention

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/admin/attention` | Every Reflex attention domain: `members` (entities that wake the SLM) and `seen` (entities touched at runtime, which the YAML seed leaves alone) |
| `PUT` | `/api/admin/attention/{domain}` | Add entities to, or sticky-remove them from, one domain |

`GET` returns `{"domains": [{"domain": "home", "members": [...], "seen": [...]}, ...]}`,
sorted by domain, `{"domains": []}` if Redis is unreachable. `PUT` takes
`{"allow": ["light.kitchen"], "ask": ["binary_sensor.motion"]}` — `allow` entities go
through `attention_add` (into the set, marked seen); `ask` entities go through
`attention_remove` (out of the set, marked seen so the seed does not re-add them) — and
returns the updated domain in the `GET` shape. Domains are `^[a-z0-9_]{1,64}$`; anything
else is a 400. `AttentionSet.should_fire` checks membership with `SISMEMBER` per event, so
the change applies to the next state change.

---
```

- [ ] **Step 3: `docs/autonomy.md`**

Replace the bullet starting `- Web confirm: `POST /api/actions/{request_id}/confirm`` with:

```markdown
- Web confirm: `POST /api/actions/{request_id}/confirm` (auth cookie
  required; 404 when expired). The SPA renders a Confirm button on the
  notification toast (`web/src/lib/notifications.ts`).
- Web reads (auth cookie required, never consume): `GET /api/actions/pending`
  returns `{"actions": [...]}` oldest first; `GET /api/actions/{request_id}`
  returns one, or 404 once it has expired or been answered. Each entry carries
  `request_id`, `tool_name`, `target_service`, `parameters`, `reason`, `source`,
  `timestamp`, `ttl_seconds` (remaining) and `expires_at`.
- `ActionRequest.reason`: for critical tools the Conscious engine advertises an
  optional `reason` parameter; the model's answer is moved off the tool
  parameters onto the request, forwarded in the URGENT notification's metadata,
  and shown on the confirmation prompt so the user sees *why* before approving.
```

- [ ] **Step 4: `docs/secrets.md`**

Replace the bullet starting `- `GET /api/integrations/{name}/status` (service) proxies the service's` … up to `for the status proxy to work.` with the same text plus one sentence, i.e.:

```markdown
- `GET /api/integrations/{name}/status` (service) proxies the service's
  `/health`. Healthy iff HTTP 200, top-level `status == "ok"`, and every
  nested component dict with a `"state"` key reports `"connected"`. The
  `/health` URL is resolved via `urljoin(endpoint, "/health")` against the
  service's registered endpoint host — services MUST expose `/health` at the
  root of that host (not under a sub-path) for the status proxy to work.
  The response also carries `latency_ms` — the probe's round trip — or
  `null` for a service with no endpoint.
```

- [ ] **Step 5: `docs/architecture.md` Redis key table**

After the `alfred:pending_actions:{request_id}` row (the last row of the §5.2 table), add:

```markdown
| `alfred:librarian:status` | Hash | `last_run_at`, `reviewed`, `next_run_at` — written after every consolidation cycle (`core/librarian/consolidator.py`) |
| `alfred:auth:{session_id}` | Hash | Passkey session: `authenticated`, `credential_id`, `created_at`, `ip`, `user_agent`, `channel` (TTL 8h, `core/identity/auth_routes.py`) |
| `alfred:webauthn:pairing` | String | Active 6-digit device-pairing code (TTL 300s, single-use) |
| `alfred:webauthn:pairing:fails` | String (int) | Wrong pairing guesses; the code is burned at 10 |
```

- [ ] **Step 6: `docs/PRD.md`**

Replace the row `| Reflex "attention set" — Alfred tunes which entities wake the fast mind, and can retune itself | Planned | spec `2026-07-15-real-home-ha-integration-design.md` (Plan 3) |` with:

```markdown
| Reflex "attention set" — Alfred tunes which entities wake the fast mind, and can retune itself (`attention_*` tools); readable and editable over `GET`/`PUT /api/admin/attention` | Shipped | `docs/admin-api.md` |
```

Replace the row `| Trusted-network gating for sensitive operations (localhost + Tailscale only) | Shipped | `docs/webauthn.md` |` with two rows:

```markdown
| Trusted-network gating for credential-equivalent operations, or a 5-minute pairing code minted by a signed-in device | Shipped | `docs/webauthn.md` |
| Session and passkey management: list/end sessions, log out everywhere, remove a passkey (never the last) | Shipped | `docs/webauthn.md` |
```

- [ ] **Step 7: `CLAUDE.md`**

Line 74, old:

```
- `core/identity/auth_routes.py` — WebAuthn registration/login/logout endpoints (6 routes under `/api/auth/`)
```

new:

```
- `core/identity/auth_routes.py` — WebAuthn registration/login/logout, sessions, passkeys and pairing-code endpoints (11 routes under `/api/auth/`)
```

Line 270, old:

```
- WebAuthn registration endpoints require trusted network — passkey creation is gated to localhost/Tailscale
```

new:

```
- WebAuthn registration endpoints require trusted network **or** a valid `X-Pairing-Code` (minted by `POST /api/auth/pairing` from a signed-in session, 5 min, single-use, burned after 10 wrong guesses)
```

Check nothing else describes the old shape:

```bash
grep -n "6 routes\|6 REST endpoints\|gated to localhost/Tailscale" CLAUDE.md docs/*.md
```

Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add docs/webauthn.md docs/admin-api.md docs/autonomy.md docs/secrets.md docs/architecture.md docs/PRD.md CLAUDE.md
git commit -m "docs: sessions, passkeys, pairing, pending reads and the new overview fields"
```

---

### Task 15: Full verification

**Files:** none new.

- [ ] **Step 1: The whole suite**

Run: `uv run pytest -q`
Expected: all PASS, no warnings about unawaited coroutines.

- [ ] **Step 2: Lint and types**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/`
Expected: clean on all three.

- [ ] **Step 3: No stale references**

```bash
grep -rn "trusted_network_dep=lambda\|Depends(trusted_network_dep)\|\"6 routes\"\|6 REST endpoints" core/ tests/ docs/ CLAUDE.md --include='*.py' --include='*.md'
```

Expected: no output.

- [ ] **Step 4: Manual smoke against a running stack (optional, LAN only)**

With the channels process running and a passkey session cookie in `$COOKIE`:

```bash
curl -s -b "alfred_auth=$COOKIE" http://localhost:8081/api/auth/sessions | python3 -m json.tool
curl -s -b "alfred_auth=$COOKIE" http://localhost:8081/api/auth/credentials | python3 -m json.tool
curl -s -b "alfred_auth=$COOKIE" http://localhost:8081/api/actions/pending
curl -s -b "alfred_auth=$COOKIE" http://localhost:8081/api/admin/overview | python3 -c 'import json,sys; o=json.load(sys.stdin); print(o["reflex"], o["librarian"], o["cost"])'
curl -s -b "alfred_auth=$COOKIE" http://localhost:8081/api/admin/attention | python3 -m json.tool
```

Expected: the session list shows your own entry with `"current": true`; credentials lists your passkey; pending is `{"actions": []}`; overview prints the two new dicts and the cost object (with `request_count`/`avg_usd` once anything has been spent today); attention lists the seeded `home` domain.

- [ ] **Step 5: Commit anything the verification changed**

```bash
git status --short
```

Expected: clean. If a fix was needed, commit it with a `fix(...)` message naming the task it belongs to.

---

## Done

When every task is checked, the branch is ready for a PR titled
`feat(pwa): backend additions the PWA design assumes (phase 0b)`. The PR body should list
the 14 commits above and link the spec (§10) — push and open it only when asked.
