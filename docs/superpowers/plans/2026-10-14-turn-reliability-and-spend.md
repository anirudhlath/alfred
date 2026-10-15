# Turn reliability and spend (PR 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A failed System 2 turn is retried the industry-standard way and answered exactly once, side effects never run twice (#304), and spend is counted and alerted safely across processes (#309).

**Architecture:** There are three retry layers, each with one owner:
- a `tenacity` retrying `httpx2` transport under every OpenAI-compatible model;
- delivery-counted Redis-stream redelivery, with backoff, a cap and a dead-letter stream;
- connect-only retries in `HomeAgent`.

A per-request side-effect ledger and an "answered" marker make the turn consumer idempotent. Spend moves to pydantic-ai-harness `SpendLimits` over `RedisSpendStore`, which keeps atomic Lua counters, plus a `SET NX` exactly-once alert.

**Tech Stack:** Python 3.13, Pydantic AI 2.55.0, pydantic-ai-harness 0.55.0, openai 3.29, httpx2, tenacity 9.1, redis-py asyncio, fakeredis, pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-pydantic-ai-adoption-design.md`:
- components 6 ("A failed turn answers once"), **6b ("Retries", added 2026-10-14)** and 7 ("Spend");
- the Decisions rows dated 2026-10-14.

Stacked on PR 2 (#360, `feat/pydantic-ai-system2`). Branch: `feat/turn-ledger-and-spend`. Closes #304, #309.

## Global Constraints

- **Gate:**
  ```
  set -o pipefail; uv sync -q --all-extras && uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync mypy --strict alfred_cli/ alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/ && PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q -p no:randomly
  ```
  Run it once per task, before commit. While iterating, run only the narrow test files.
- Exact pins stay: `pydantic-ai-slim[openai,openrouter]==2.55.0` and `pydantic-ai-harness==0.55.0`. `tenacity` moves from the `evals` extra to `[project.dependencies]` (`tenacity>=9.1,<10`).
- No model identifiers in commit messages, code comments or docs prose. Model strings in config defaults and the price table are allowed.
- The repo is public. Never write hostnames, IPs or the owner's domain into any file.
- Conventional commits, one or more per task. Never push; the controller pushes.
- Every new Redis key is declared in `shared/streams.py` beside the existing keys.
- Logging follows the module's existing logger (loguru in core/conscious, stdlib `logging` in shared/agents.py). Match the file you are in.
- `FAILED_REPLY` text, verbatim: `I'm afraid I couldn't finish that, sir — my reasoning service isn't answering. Please try again in a moment.`
- Retry numbers, verbatim from spec 6b:
  - HTTP: 3 attempts, 30 s budget, full jitter `uniform(0, min(8, 0.5 × 2ⁿ))`, statuses `{408, 409, 429, 500, 502, 503, 504, 529}`.
  - Turns: base idle 15 s doubling, `MAX_DELIVERIES = 3`, replay window 300 s.
  - HomeAgent: 3 attempts, 0.2 s base, 2 s cap, connect errors only.

## Review Focus

1. **A 429 whose `Retry-After` exceeds the remaining budget.** It must surface at once as `ModelHTTPError(429)`, not sleep past the web channel's 60 s wait.
2. **A crash between XADD of the response and XACK.** Redelivery must not answer twice: the answered marker. Nor run tools twice: the ledger.
3. **A redelivered entry older than 300 s.** It must not run, because a 5-minute-late "unlock the door" is dangerous. It is answered with `FAILED_REPLY` and dead-lettered.
4. **Redis unavailable during a spend write after a charged response.** The turn must still answer: under-count, WARNING. It must not raise and replay.
5. **Two processes crossing 80% in the same second.** Exactly one notification.

---

### Task 1: The retrying model transport

**Files:**
- Create: `shared/llm_http.py`
- Modify: `shared/agents.py` (`resolve_model`), `pyproject.toml` (tenacity to runtime deps), `uv.lock`
- Test: `tests/shared/test_llm_http.py`, `tests/shared/test_agents.py`

**Interfaces:**
- Produces:
  ```python
  RETRY_STATUSES: frozenset[int]  # {408, 409, 429, 500, 502, 503, 504, 529}
  @dataclass(frozen=True)
  class RetryPolicy:
      attempts: int = 3
      budget_s: float = 30.0
      base_s: float = 0.5
      cap_s: float = 8.0
  def parse_retry_after(headers: httpx2.Headers) -> float | None
  class RetryingTransport(httpx2.AsyncBaseTransport):
      def __init__(self, policy: RetryPolicy = RetryPolicy(), wrapped: httpx2.AsyncBaseTransport | None = None, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, clock: Callable[[], float] = time.monotonic, rng: Callable[[float, float], float] = random.uniform) -> None
  def retrying_http_client(policy: RetryPolicy = RetryPolicy()) -> httpx2.AsyncClient
  ```
- `resolve_model(setting, value)` keeps its signature. For `vllm:` and `openrouter:` strings it now builds the provider with `openai_client=AsyncOpenAI(base_url=..., api_key=..., max_retries=0, http_client=retrying_http_client())`.

Behaviour (spec 6b, "Model requests"):
- Use `tenacity.AsyncRetrying` inside `handle_async_request`. Do not use `pydantic_ai.retries.AsyncHTTPX2TenacityTransport`: on exhaustion it raises `HTTPStatusError` inside the transport, which the openai SDK reports as `APIConnectionError` and so loses the status.
- **Retried exceptions:** `httpx2.TransportError` subclasses that mean "no usable response": `ConnectError`, `ConnectTimeout`, `ReadTimeout`, `WriteTimeout`, `PoolTimeout`, `RemoteProtocolError`, `ReadError`. Check the names exist in httpx2 2.13 and use the real class names.
- **Retried statuses:** any status in `RETRY_STATUSES`. The response body is read (`await response.aread()`) and the response closed before retrying.
- **Wait:** `parse_retry_after(response.headers)` when present. It understands `retry-after-ms`, `retry-after` seconds, and `retry-after` as an HTTP date (`email.utils.parsedate_to_datetime`). Otherwise `rng(0, min(cap_s, base_s * 2 ** (attempt - 1)))`.
- **Stop:**
  - after `attempts` attempts;
  - or when the next wait would end past `budget_s` since the first attempt;
  - or when `Retry-After` exceeds the remaining budget.
- **On stop:**
  - a retryable *status* returns the last response. Keep it unread-closed; re-wrap the read bytes so the SDK can read the body.
  - a retryable *exception* re-raises the last exception.
- **Each retry:** one `logging.WARNING` from `shared.llm_http`, naming the role (the request's `X-Alfred-Role` header, `ROLE_HEADER` from `shared/llm_roles.py`), the attempt, the status or exception class, and the wait. Then `trace.get_current_span().add_event("llm.retry", {...})` with the same fields.
- **Requests:** OpenAI request bodies are bytes, so they are replayable. Assert in a test that a JSON POST body is resent identically on attempt 2.
- `retrying_http_client` uses `httpx2.Timeout(600, connect=5)`, matching Pydantic AI's `create_async_httpx2_client` default. The per-request timeout still comes from `ModelSettings.timeout`.
- `resolve_model`:
  - use `pydantic_ai.models.infer_model(text, provider_factory=...)` if 2.55 has that parameter (check the signature in `.venv`). Otherwise parse the `provider:` prefix and construct `OpenAIChatModel(name, provider=VLLMProvider(openai_client=...))` / `OpenRouterModel(name, provider=OpenRouterProvider(openai_client=...))` (use the classes `infer_model` uses for those prefixes today).
  - **Base URL and key resolution must not change:** `VLLM_BASE_URL`, `OPENROUTER_API_KEY`, `api-key-not-set` fallback.
  - Every other prefix still goes through plain `infer_model`.
  - The existing tests in `tests/shared/test_agents.py` (base_url, model_key, the missing-setting errors) must still pass unchanged.

- [ ] **Step 1: Write the failing tests** in `tests/shared/test_llm_http.py`. Drive `RetryingTransport` over an `httpx2.MockTransport`, with a fake `sleep` that records waits (it does not sleep), a fake `clock` and a fixed `rng`:
  1. 503 then 200 → returns 200; one wait equal to `rng` output; two requests sent; identical bodies.
  2. 429 with `retry-after: 2` then 200 → wait 2.0.
  3. 429 with `retry-after-ms: 1500` → wait 1.5. An HTTP-date `retry-after` 3 s ahead → wait ≈3.
  4. 429 with `retry-after: 120` → no retry; the 429 response is returned with its body readable.
  5. 500 × 3 → three attempts, then the third 500 response is returned with a readable body.
  6. 400 → one attempt, returned.
  7. `ConnectError` twice then 200 → 200. `ConnectError` × 3 → raises `ConnectError`.
  8. Budget: the clock advances 25 s per attempt and a computed wait would cross 30 s → stops early.
  9. A WARNING per retry naming the `X-Alfred-Role` value (caplog on `shared.llm_http`).
  10. **End to end:** a Pydantic AI `Agent` on a `vllm:` model whose `retrying_http_client` wraps a MockTransport answering 429 (`retry-after: 999`) → `agent.run` raises `ModelHTTPError` with `status_code == 429`. This is the status-preservation guarantee. Build it by monkeypatching `retrying_http_client`'s wrapped transport, or by exposing a module-level hook for tests; pick the smaller seam and say which in the report.
  11. In `tests/shared/test_agents.py`: a resolved `vllm:` model's openai client has `max_retries == 0`, and its http client's transport is a `RetryingTransport`. The same for `openrouter:`.
- [ ] **Step 2: Run them; they fail** (module missing).
- [ ] **Step 3: Implement** `shared/llm_http.py` and the `resolve_model` change. Run `uv add 'tenacity>=9.1,<10'`, then remove tenacity from the evals extra if `uv add` leaves it duplicated.
- [ ] **Step 4: Narrow tests pass**: `tests/shared/`, `tests/core/conscious/`, `tests/core/librarian/`.
- [ ] **Step 5: Gate, then commit** `feat(shared): one bounded, status-preserving retry layer under every model request`.

---

### Task 2: Turn failures — classify, redeliver with backoff, cap, dead-letter, answer once

**Files:**
- Create: `core/conscious/failures.py`
- Modify:
  - `shared/redis_streams.py` (add `reclaim_due`, keep `reclaim_stale` / `reclaim_replayable` for the Reflex consumers);
  - `shared/streams.py` (`DEAD_LETTER_STREAM = "alfred:conscious:dead_letters"`, `ANSWERED_KEY = "alfred:conscious:answered:{}"`);
  - `core/conscious/runner.py`;
  - `core/conscious/__main__.py` (main loop).
- Test: `tests/shared/test_redis_streams.py` (new tests), `tests/core/conscious/test_runner.py` (or the existing runner test file; find it with `git grep -l process_request_entry tests`), `tests/core/conscious/test_failures.py`

**Interfaces:**
- Produces:
  ```python
  # core/conscious/failures.py
  FAILED_REPLY: str  # verbatim from Global Constraints
  MAX_DELIVERIES = 3
  class FailureKind(StrEnum): PERMANENT = "permanent"; TRANSIENT = "transient"
  def classify(exc: BaseException) -> FailureKind
  async def dead_letter(redis: AioRedis, *, entry_id: str, event: str, exc: BaseException | None, deliveries: int, reason: str) -> None
  # shared/redis_streams.py
  @dataclass(frozen=True)
  class Redelivery:
      entry_id: str
      fields: dict[bytes | str, bytes | str]
      delivery: int  # deliveries including this one
  async def reclaim_due(redis, stream: str, group: str, consumer: str, *, base_idle_ms: int = 15_000, count: int = 10) -> list[Redelivery]
  # core/conscious/runner.py
  async def process_request_entry(entry_id, entry_data, *, engine, redis, stream, group, consumer, delivery: int = 1, now_ms: int | None = None, after_answer: Callable[[], Awaitable[None]] | None = None) -> None
  ```
  `after_answer` replaces `cost_tracker`, so Task 5 can hang the spend alert on it without touching the runner again. Today's caller passes `cost_tracker.send_alert_if_needed`.

**Classification (spec 6b table):**
- **PERMANENT:**
  - `ModelHTTPError` with a 4xx status not in `{408, 409, 429}`;
  - `pydantic_ai.exceptions.UserError`;
  - `ValueError` / `TypeError` raised by configuration;
  - be conservative: only `UserError` and the 4xx rule are PERMANENT. Everything below defaults to TRANSIENT.
- **TRANSIENT:**
  - `ModelHTTPError` 408/409/429/5xx;
  - `ModelAPIError` (no status);
  - `redis.exceptions.ConnectionError` / `TimeoutError`;
  - builtin `TimeoutError`;
  - any other exception.

**`reclaim_due`:**
- Call `XPENDING stream group - + count` (the extended form returns id, consumer, idle ms, times_delivered).
- Keep entries with `idle >= base_idle_ms * 2 ** (times_delivered - 1)`.
- `XCLAIM` them to `consumer` with `min_idle_time` equal to the same threshold, so a concurrent claimer cannot double-claim. XCLAIM increments the delivery count.
- Return `Redelivery(delivery=times_delivered + 1)`.
- An id XCLAIM returns with no fields (trimmed from the stream) is XACKed and skipped.
- Redis errors: log a warning and return `[]`, like `reclaim_stale`.

**`process_request_entry` order:**
1. **Missing or invalid event:** ACK and drop (unchanged).
2. **Already answered:** if `ANSWERED_KEY.format(request.event_id)` exists → ACK, log INFO, return.
3. **Too old:** if `delivery > 1` and the entry's age (from the id's ms prefix, via the existing `is_replayable` helper and `now_ms`) exceeds `MAX_REPLAY_AGE_MS` → publish `FAILED_REPLY`, mark answered, ACK, `dead_letter(reason="expired")`. Do not run the engine.
4. **Run:** `engine.process_request`. On success: XADD response → `SET ANSWERED_KEY EX 3600` → XACK → `after_answer()`. `after_answer` errors are logged, never raised.
5. **On exception `exc`:**
   - PERMANENT → publish `FAILED_REPLY`, mark answered, ACK, `dead_letter(reason="permanent")`;
   - TRANSIENT and `delivery >= MAX_DELIVERIES` → the same with `reason="exhausted"`;
   - TRANSIENT otherwise → log a WARNING with the delivery count and leave un-ACKed.
   - Task 3 inserts the ledger branch ahead of these.
6. **The failure reply** is built through the engine's `_reply` shape. Add a public `engine.failure_reply(request, text) -> AlfredResponse` that uses the same constructor as `_reply`, with `mood="concerned"` and no actions. That way the channel, session and identity fields match a normal answer.

**Main loop** (`core/conscious/__main__.py`):
- Every iteration, after `read_group`, append `await reclaim_due(r, stream, group, consumer)` and process those with `delivery=red.delivery`.
- Remove `_PEL_RECLAIM_EVERY` and the conscious use of `reclaim_replayable`.
- Live entries get `delivery=1`.

- [ ] **Step 1: Write the failing tests** (fakeredis supports XPENDING/XCLAIM; check the extended XPENDING works in fakeredis 2.39, and fall back to a `MagicMock` redis for that one call if not):
  - `classify` table-driven: 400, 401, 402, 404, 422 → PERMANENT. 408, 409, 429, 500, 503, 529 → TRANSIENT. `ModelAPIError`, redis `ConnectionError`, `TimeoutError`, `RuntimeError` → TRANSIENT. `UserError` → PERMANENT.
  - `reclaim_due`: a pending entry idle 10 s with times_delivered 1 → not due. Idle 16 s → claimed with delivery 2. Times_delivered 2 idle 20 s → not due; idle 31 s → due. A trimmed id → ACKed, not returned.
  - Runner:
    - success sets the answered marker and calls `after_answer` once;
    - an entry whose marker is set is ACKed without calling the engine;
    - a 429 on delivery 1 → no ACK, no response;
    - a 429 on delivery 3 → `FAILED_REPLY` published, ACKed, one dead letter with reason `exhausted`;
    - a 400 on delivery 1 → `FAILED_REPLY`, ACK, dead letter `permanent`;
    - a delivery-2 entry with an id 301 s old → engine not called, `FAILED_REPLY`, dead letter `expired`;
    - an `after_answer` that raises does not un-ACK or raise.
  - **Crash simulation (Review Focus 2):** an XACK that raises after XADD → the next delivery of the same entry finds the marker and ACKs without a second response.
- [ ] **Step 2: Run them; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Narrow tests pass** (`tests/core/conscious/`, `tests/shared/`, plus `git grep -l reclaim_ tests` for anything touching reclaim).
- [ ] **Step 5: Gate, then commit** `feat(conscious): redeliver failed turns with backoff and a cap, dead-letter, and answer exactly once`.

---

### Task 3: The side-effect ledger (#304)

**Files:**
- Create: `core/conscious/effects.py`
- Modify:
  - the toolsets that dispatch side effects (`core/conscious/tools/registry.py`, `tools/triggers.py`, and the attention tools wherever they are defined);
  - `core/conscious/agent.py` (if the recording is a capability);
  - `core/conscious/runner.py`;
  - `shared/streams.py` (`EFFECTS_KEY = "alfred:conscious:effects:{}"`).
- Test: `tests/core/conscious/test_effects.py`, runner tests

**Interfaces:**
- Produces:
  ```python
  EFFECTS_TTL_S = 3600
  @dataclass(frozen=True)
  class Effect:
      tool: str  # registry / catalog name, e.g. "home.light_turn_on"
      args: dict[str, Any]
      status: Literal["started", "ok", "failed"]
  class EffectLedger:
      def __init__(self, redis: AioRedis) -> None
      async def started(self, request_id: str, tool: str, args: dict[str, Any]) -> int  # index
      async def finished(self, request_id: str, index: int, ok: bool) -> None
      async def read(self, request_id: str) -> list[Effect]
  def ledger_reply(effects: list[Effect]) -> str
  ```
- `TurnServices` gains `effects: EffectLedger` (or the ledger is reachable from deps by the cleanest existing route; record which in the report).

Behaviour (spec component 6):
- **Side-effecting calls:**
  - every registry tool;
  - `triggers_create_trigger`, `triggers_update_trigger`, `triggers_delete_trigger`, `triggers_toggle_trigger`;
  - `attention_add` and `attention_remove`.
- **Never recorded:** memory, live state, listings, integrations and catalog reads.
- Carry the flag as tool metadata (`side_effect: True`) where the `ToolDefinition` is built, and decide from the metadata at record time, so the list lives in one place.
- **Recording point:**
  - prefer a Pydantic AI capability hook around tool execution, if 2.55 has one (check `pydantic_ai/capabilities/abstract.py` for `before_tool_execute` / `wrap_tool_execute` or similar). The ledger then lives in one place;
  - otherwise wrap the dispatch in the shared catalog `call_tool` path.
  - `started` is written **before** the dispatch and `finished` after, with `ok=False` when it raised `ToolFailed`. A refused guest call (scope) or a schema `ModelRetry` is not a side effect and is not recorded.
- **Storage:** an RPUSH of JSON `{tool, args, status}` to `EFFECTS_KEY.format(request_id)` with EXPIRE `EFFECTS_TTL_S`. `finished` uses LSET at the index.
- **Runner** (insert into Task 2's order):
  - 2b, after the answered check: if the ledger for `request.event_id` is non-empty, this is a redelivery after a crash. Publish `ledger_reply(effects)`, mark answered, ACK. Do not run the engine.
  - 5a, before classification on exception: if the ledger is non-empty, publish `ledger_reply`, mark answered, ACK, `dead_letter(reason="partial")`.
- **The ledger reply is appended to the session** (user content + reply) through the session manager, the way `process_request` appends a normal turn. Expose `engine.record_partial_turn(request, text)`, or fold it into `failure_reply`'s caller. Keep one public engine method per need.
- **`ledger_reply`:** a fixed template, no model call. Humanise a tool name: drop the service prefix and replace `_` with spaces, so `home.light_turn_on` becomes "light turn on". Distinguish done from in doubt:
  - all ok: `I'd started on that — I did: light turn on, scene activate — then ran into a problem before I could finish, sir.`
  - any started or failed: append ` I'm not certain these went through: lock unlock. Please check before asking again.`, and list done ones only in the first clause; if none were done, open with `I ran into a problem partway through, sir.`

- [ ] **Step 1: Write the failing tests:**
  - ledger round trip; TTL set;
  - `ledger_reply` for: all ok, mixed ok/started, only failed;
  - a `FunctionModel` turn that calls a registry tool and then raises `ModelHTTPError(503)` on the next model request → the ledger holds one `ok` effect, and the runner publishes the ledger reply and ACKs, with no dead letter of reason `exhausted`. This is #304's acceptance test;
  - redelivery of that entry → the engine is not called and the domain router is not called again;
  - a read-only tool (`memory_recall_memories` or `triggers_list_triggers`) leaves the ledger empty;
  - a guest-refused call leaves it empty.
- [ ] **Step 2: They fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Narrow tests pass.**
- [ ] **Step 5: Gate, then commit** `feat(conscious): a turn that already acted answers from its ledger instead of running again (#304)`.

---

### Task 4: HomeAgent retries a request that never left

**Files:**
- Modify: `domains/home/home_agent.py`
- Test: its existing test module (`git grep -l HomeAgent tests domains`)

Behaviour (spec 6b, "Domain dispatch"):
- Retry the JSON-RPC POST only on `httpx.ConnectError`, `httpx.ConnectTimeout` and `httpx.PoolTimeout`.
- Up to 3 attempts, waiting `uniform(0, min(2.0, 0.2 * 2 ** (attempt - 1)))` between them.
- Use `tenacity.AsyncRetrying` with `retry_if_exception_type`, `stop_after_attempt(3)` and `wait_random_exponential(multiplier=0.2, max=2)`. Tenacity's full-jitter matches.
- Inject the sleep for tests (`AsyncRetrying(sleep=...)`).
- One WARNING per retry.
- `ReadTimeout`, any status error and any other exception keep today's single-attempt `ActionResult(status="error")` behaviour.

- [ ] **Step 1: Failing tests:**
  - ConnectError, ConnectError, then success → success, 3 POSTs;
  - ConnectError × 3 → `status="error"`, 3 POSTs;
  - ReadTimeout → `status="error"`, 1 POST;
  - 500 → 1 POST.
- [ ] **Step 2: They fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Pass.**
- [ ] **Step 5: Gate, then commit** `feat(home): retry a dispatch only when it never reached the service`.

---

### Task 5: Spend on SpendLimits — atomic, gated everywhere, alerted once (#309)

**Files:**
- Create: `shared/spend.py`
- Delete: `core/conscious/cost.py` and its tests (replace them)
- Modify:
  - `core/conscious/agent.py`, `core/conscious/engine.py`, `core/conscious/__main__.py`;
  - `core/librarian/agents.py`, `core/librarian/consolidator.py` (the `cost=` plumbing), `core/librarian/__main__.py`;
  - `core/channels/admin_api.py`;
  - `shared/streams.py` (retire `COST_DAILY_KEY`; add `SPEND_PREFIX = "alfred:spend"` and `SPEND_ALERT_KEY = "alfred:spend:alert:{}"`);
  - the web client only if a field it reads changes (`web/src/lib/system.ts`, `web/src/lib/types.ts`, `web/src/room/StatusLine.tsx`). Keep the JSON shape and the web needs no change.
- Test: `tests/shared/test_spend.py`, engine and librarian tests, admin API tests

**Interfaces:**
- Produces:
  ```python
  FALLBACK_INPUT_USD_PER_MTOK = Decimal("3")
  FALLBACK_OUTPUT_USD_PER_MTOK = Decimal("15")
  ALERT_FRACTION = 0.8
  def price(response: ModelResponse) -> Decimal | None   # PriceFunc
  class TolerantSpendStore:  # wraps a SpendStore; add_many failures log WARNING and return what the protocol expects; reads pass through
  def build_spend_limits(redis: AioRedis, *, cap_usd: float, clock: Callable[[], datetime]) -> SpendLimits
  def local_clock(tz_name: Callable[[], str]) -> Callable[[], datetime]
  async def spend_snapshot(limits: SpendLimits, redis: AioRedis) -> dict[str, Any]   # {date, spend_usd, cap_usd, alert_sent, request_count, avg_usd}
  async def alert_if_needed(limits: SpendLimits, redis: AioRedis, notifier: NotificationPublisher | None) -> bool
  ```

Behaviour (spec component 7, as amended 2026-10-14):
- `build_spend_limits` returns `SpendLimits(budgets=[Budget(usd=Decimal(str(cap_usd)), window="day", warn_at=ALERT_FRACTION, name="daily")], store=TolerantSpendStore(RedisSpendStore(redis, prefix=SPEND_PREFIX)), price=price, on_unpriced="zero", clock=clock)`. Read the installed `RedisSpendStore` source to confirm the prefix rules (no braces) and what `add_many` returns.
- **`price`:**
  - `Decimal(0)` when the response's provider is `vllm`. Check which field `ModelResponse` carries (`provider_name`, or `model_name` plus how `model_key` derives `system`).
  - else `response.cost().total_price` when genai-prices knows the model;
  - else the fallback rates applied to `response.usage` input and output tokens, with one WARNING per model name (an `lru_cache`'d or set-guarded warn).
- **`local_clock`:** returns a callable giving `datetime.now(ZoneInfo(tz_name()))`. Where the engine and the Librarian get the user's zone synchronously is yours to find (`shared/timezone` / `get_user_timezone` is async; cache the last value the engine resolved, falling back to the configured default). Record the choice in the report. A test sets the zone so 23:30 local and 00:30 local fall in different buckets.
- **System 2:**
  - the `SpendLimits` instance is passed as a capability when the agent is built;
  - the engine's pre-run gate becomes `await limits.exhausted()` → `BUDGET_REPLY`;
  - `_run` catches `SpendLimitExceeded` **before** `UsageLimitExceeded` → `BUDGET_REPLY` with `mood="concerned"`;
  - `_record_spend` and every `CostTracker` use go.
- **Librarian:** each of its agents gets the same capability, one `SpendLimits` per process built by `build_spend_limits`. `SpendLimitExceeded` inside a consolidation pass is logged at WARNING and the pass is skipped (spec: "logs it and skips the pass"). The pre-pass check is `exhausted()`.
- **Alert:** `alert_if_needed` reads `status()`. When the daily budget's spent fraction is ≥ `ALERT_FRACTION`, it does `SET SPEND_ALERT_KEY.format(bucket_date) 1 NX EX 172800`; only on success does it publish today's URGENT notification (same title and body as `CostTracker.send_alert_if_needed` today; copy them). It returns whether it sent. It is called:
  - by the runner's `after_answer` (Task 2's hook) in the conscious process;
  - after each Librarian `ask` in both processes. The one-shot Librarian builds a `NotificationPublisher` on its Redis if it does not have one.
- **Admin API:** `/api/admin/overview`'s `cost` comes from `spend_snapshot`. `alert_sent` is the marker's existence, and `request_count` is the budget status's request count. Delete `COST_DAILY_KEY` and its readers.
- **Write tolerance:** a `RedisSpendStore.add_many` that raises (simulate it by closing the fakeredis connection, or with a store double) → the turn still returns its answer and a WARNING is logged (Review Focus 4).

- [ ] **Step 1: Failing tests:**
  - `price`: vllm → 0; a genai-prices-known openrouter model → its price; an unknown openrouter model → the fallback rates; one WARNING across two calls;
  - **two `SpendLimits` instances on one fakeredis, 50 concurrent accruals each** (drive `store.add_many` through the capability, or call the store as the capability would) → the total equals the sum. No lost update. This is #309's acceptance test;
  - `alert_if_needed` from two instances concurrently at 85% → exactly one `publish`;
  - local-midnight bucket split;
  - the engine at the cap → `BUDGET_REPLY` without a model call; `SpendLimitExceeded` mid-run → `BUDGET_REPLY`;
  - the Librarian over the cap → pass skipped, logged;
  - an admin overview shape test with the keys above;
  - a write failure → the answer still returned.
- [ ] **Step 2: They fail.**
- [ ] **Step 3: Implement**, deleting `core/conscious/cost.py`. `git grep -n "CostTracker\|COST_DAILY_KEY\|cost_tracker\|record_spend\|is_budget_exceeded"` must end empty outside docs/plans and docs/superpowers.
- [ ] **Step 4: Narrow tests pass** (`tests/core/conscious tests/core/librarian tests/shared tests/core/channels`).
- [ ] **Step 5: Gate, then commit** `feat(spend): atomic cross-process spend on SpendLimits, gated everywhere, alerted once (#309)`.

---

### Task 6: Docs

**Files:**
- Modify:
  - `docs/llm.md`: a "Retries" section (the three layers, numbers, what is not retried, the dead-letter stream, how to read it with `redis-cli XRANGE alfred:conscious:dead_letters - +`), a "Spend" section, and remove CostTracker mentions;
  - `docs/PRD.md`: rows touching budget and failure behaviour; add or adjust a shipped row for "a failed request is answered once, never re-runs actions";
  - `docs/deployment.md`: `DAILY_COST_CAP_USD` semantics, local-midnight rollover, the dead-letter stream;
  - `CLAUDE.md`, `core/CLAUDE.md`: anywhere naming `cost.py` / `CostTracker` / the reclaim cadence;
  - `docs/admin-api.md`: the overview `cost` fields' meaning (`request_count` = model requests);
  - `evals/coverage.yaml`: if it lists PRD rows you change, keep it consistent.
- [ ] **Step 1:** `git grep -n "CostTracker\|cost\.py\|COST_DAILY\|_PEL_RECLAIM\|reclaim_replayable" -- '*.md' ':!docs/superpowers'`. Fix every hit that describes the conscious engine.
- [ ] **Step 2: Gate, then commit** `docs: retries, the turn ledger and spend`.

---

### Task 7: Verify (controller, not a subagent)

- [ ] Full gate; hygiene greps (runbook §Secret hygiene, never quoted in the repo) against `origin/master`; the commit-message model grep from PR 2's Task 11.
- [ ] **Live:** one System 2 turn and one Librarian analysis against the local vLLM through `resolve_model` (adapt `verify_pr2.py` in the scratchpad). Then:
  - confirm the `X-Alfred-Role` header still reaches vLLM;
  - confirm `alfred:spend:*` hashes appear in a scratch Redis DB, not prod's.
- [ ] **Failure injection, live:** point `VLLM_BASE_URL` at a closed local port for one `agent.run` → three attempts logged, then `ModelAPIError`, all within the budget.
- [ ] **Evals:** `alfred evals run home_control conversation --epochs 5` on this branch against PR 2's tip (`feat/pydantic-ai-system2`), one after the other, never at once. Acceptance: no golden down two or more.
- [ ] Draft the PR description with the actual outputs and mark this plan done. Pushing and opening the PR wait for the owner's go; merging too.
