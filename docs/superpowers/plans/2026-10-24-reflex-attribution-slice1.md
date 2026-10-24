# Reflex anticipation, slice 1 ("who did it") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every home state change reaching Alfred says who caused it (a person, Alfred, an automation, or
something outside), that cause reaches Reflex's prompt, the shadow log and episodic memory, and the owner
can read a private report of who changed what.

**Architecture:** Home Assistant's own `context` (`id`, `user_id`, `parent_id`) rides on
`StateChangedEvent` as an optional `ChangeContext`, resolved by home-service, which alone knows HA users.
Alfred classifies it with one pure function (`core/attribution/cause.py`), records three private 30-day
logs (Alfred's calls, attributed changes, every Reflex decision with its prompt) and prints a report
joining Alfred's calls to the changes they caused by HA context id. Alfred merges first (the SDK carries
the new field), home-service repins and sends it, then an operator step gives home-service an HA user of
its own.

**Tech Stack:** Python 3.13, Pydantic v2, redis-py asyncio (fakeredis in tests), pytest-asyncio, the
evals harness (`evals/harness/`), alfred-home-service (websockets, aiomqtt, loguru).

**Spec:** `docs/superpowers/specs/2026-10-24-reflex-anticipation-design.md`, section "Slice 1: who did
it". Read it before any task.

**Branches and repos.**
- Phase A (Tasks 1–8), PR A in alfred: rename this worktree's branch to `feat/reflex-attribution`
  (`git -C ~/code/.worktrees/alfred/reflex-anticipation branch -m feat/reflex-attribution`). The spec and
  this plan ride in the same PR.
- Phase B (Tasks 9–10), PR B in alfred-home-service: starts only after PR A has merged. Use a new
  worktree `~/code/.worktrees/home-service/change-context/` on `feat/change-context` off `origin/main`.
- Phase C (Tasks 11–13): the operator step, PR C flipping the goldens, and the production check.

## Global Constraints

- Python 3.13+, Pydantic v2, `mypy --strict`, ruff (line length 100). Async for all I/O.
- The four causes are exactly `person`, `alfred`, `automation`, `outside`. A change with no context has
  no cause (`None`), and its daily counter field is `unknown`.
- Cause copy, verbatim: `by {name}`, `by a person`, `by Alfred`, `by an automation`,
  `by a switch, app or device`.
- Every new log and counter is kept 30 days: `alfred:attribution:calls`, `alfred:attribution:changes`,
  `alfred:attribution:causes:<UTC date>`, `alfred:reflex:decision_log`.
- Attribution writes are best-effort: a failed write logs a warning and never raises into routing or
  Reflex, never changes a decision, and never blocks an ACK.
- Events without a context (an older home-service) parse and flow everywhere exactly as today.
- Shadow mode stays: nothing Reflex proposes executes.
- **Every merge deploys and needs the owner's go.** Ask the owner before pushing, opening a PR, posting
  publicly, or editing issues.
- **The repo is public.** No person, household or device names, hostnames, IPs, domains or keys in repo
  files, issues or PR text; goldens use the eval world's generic names. Before every commit and push run
  the two greps in the deploy directory's PWA exposure runbook, section "Secret hygiene"; both must
  print nothing. Never quote those greps in a committed file.
- No model identifiers in commits, PRs, code comments or docs.
- Repo rules: no hardcoded tool lists; no polling; `create_redis()` for clients; `alfred:events` XADDs
  pass maxlen; new features documented in `docs/<feature>.md`; `docs/PRD.md` kept current.
- Gate (alfred), once per task before its commit: `ruff check . && ruff format --check . && mypy --strict
  alfred_cli/ alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/ && .venv/bin/python
  -m pytest -x -q`. During iteration run only the task's test files.
- Gate (home-service): `uv run ruff check . && uv run ruff format --check . && uv run mypy app/
  alfred_ext/ && uv run pytest -q`.

## Review Focus

1. **No context at all** (an older home-service, the eval fake HA before Task 8, a replayed old stream
   entry): cause is `None`, the prompt line, the memory summary and the shadow log read exactly as
   today, the counter says `unknown`, nothing crashes. Tests: Tasks 2, 4, 5.
2. **home-service connected with the owner's token, or `auth/current_user` failing:** nothing is ever
   marked as caused by the service, so Alfred's changes read as a person's until the operator step, and
   the report flags the mismatch. Tests: Tasks 7 and 9.
3. **An HA user with no person entity** (a guest login, a second admin, the person deleted) or a person
   entity without a `user_id`: the cause is `person` and the copy is `by a person`, never a crash or an
   empty name. Tests: Tasks 2, 5, 9.
4. **An availability return bridged into one `last → new` change** keeps the context of the event it was
   built from, so a light coming back from `unavailable` is still attributed. Test: Task 4.
5. **Redis refusing writes** (maxmemory deny-oom) on any of the three logs or the counter: routing still
   returns its result, Reflex still decides and ACKs. Tests: Tasks 3, 4, 6.

---

## Phase A — alfred (PR A)

### Task 1: `ChangeContext` on the wire

**Files:**
- Modify: `bus/schemas/events.py` (add `ChangeContext` above `StateChangedEvent`, lines 34-42)
- Modify: `sdk/alfred_sdk/events.py` (the same, above `StateChangedEvent` at line 28)
- Test: `sdk/tests/test_schema_compatibility.py`, `bus/schemas/tests/test_events.py`
- Docs: `docs/event-bus.md` (StateChangedEvent fields), `docs/sdk.md` (what a publisher may send)

**Interfaces:**
- Produces, identical in both files:

```python
class ChangeContext(BaseModel):
    """Who caused a state change, as the publishing service resolved it."""

    id: str | None = None          # the platform's context id (HA: context.id)
    user_id: str | None = None     # the platform user who caused it
    parent_id: str | None = None   # set when an automation or script caused it
    person: str | None = None      # person entity of that user, when the service could map it
    by_service: bool = False       # the publishing service's own call caused it (on Alfred's request)

# StateChangedEvent gains:
    context: ChangeContext | None = None
```

- [ ] **Step 1: Write the failing tests.** In `sdk/tests/test_schema_compatibility.py`:
  `test_state_changed_context_roundtrip_both_ways` builds a bus event with
  `context=ChangeContext(id="c1", user_id="u1", parent_id=None, person="person.alex", by_service=False)`,
  dumps it, validates it as the SDK class and back, and asserts `context == ` the original field by
  field; and `test_state_changed_without_context_parses_as_none` validates a payload with no `context`
  key in both classes and asserts `event.context is None`. In `bus/schemas/tests/test_events.py`:
  `test_change_context_defaults` asserts `ChangeContext().by_service is False` and every other field is
  `None`.
- [ ] **Step 2: Run them and see them fail.** `.venv/bin/python -m pytest sdk/tests/test_schema_compatibility.py bus/schemas/tests/test_events.py -q` → ImportError on `ChangeContext`.
- [ ] **Step 3: Add `ChangeContext` and `StateChangedEvent.context` to both files** as in Interfaces.
- [ ] **Step 4: Run them and see them pass**, then run the whole `tests/core/reflex`, `core/reflex/tests`
  and `core/triggers/tests` directories: existing tests build events without a context and must still pass.
- [ ] **Step 5: Docs.** Add the `context` field to `docs/event-bus.md`'s StateChangedEvent table and one
  paragraph to `docs/sdk.md`: a publisher fills `ChangeContext` when its platform says who caused a
  change, sets `by_service` only for changes its own calls caused, and leaves it out otherwise.
- [ ] **Step 6: Gate, secret greps, commit** `feat(bus): carry who caused a state change on StateChangedEvent`.

### Task 2: The cause and its words

**Files:**
- Create: `core/attribution/__init__.py`, `core/attribution/cause.py`
- Test: `tests/core/attribution/__init__.py`, `tests/core/attribution/test_cause.py`

**Interfaces:**
- Consumes: `ChangeContext` (Task 1).
- Produces:

```python
Cause = Literal["person", "alfred", "automation", "outside"]
ATTRIBUTED: Final[frozenset[Cause]] = frozenset({"person", "alfred", "automation"})

def cause_of(context: ChangeContext | None) -> Cause | None: ...
def cause_words(context: ChangeContext | None, name: str | None = None) -> str | None: ...
```

`cause_of` order (first match wins): no context → `None`; `by_service` → `alfred`; `user_id` →
`person`; `parent_id` → `automation`; else `outside`. `cause_words` returns `None` when `cause_of` does;
for `person` it uses `name`, else `context.person`, else `a person`; the other three are the fixed copy
from Global Constraints.

- [ ] **Step 1: Write the failing tests** (parametrized where natural):
  - `test_no_context_has_no_cause`: `cause_of(None) is None` and `cause_words(None) is None`.
  - `test_by_service_wins_over_user_and_parent`: `ChangeContext(user_id="u", parent_id="p", by_service=True)` → `"alfred"`, words `"by Alfred"`.
  - `test_user_is_a_person`: `ChangeContext(user_id="u")` → `"person"`; words `"by a person"`; with
    `person="person.alex"` → `"by person.alex"`; with `name="Alex"` too → `"by Alex"`.
  - `test_parent_without_user_is_an_automation`: → `"automation"`, `"by an automation"`.
  - `test_neither_is_outside`: `ChangeContext(id="c")` → `"outside"`, `"by a switch, app or device"`.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/core/attribution/test_cause.py -q` → fails (module missing).
- [ ] **Step 3: Implement `cause.py`** as in Interfaces; `__init__.py` gets a one-line docstring only.
- [ ] **Step 4: Run the tests and see them pass.**
- [ ] **Step 5: Gate, secret greps, commit** `feat(attribution): classify who caused a state change`.

### Task 3: The attribution logs

**Files:**
- Modify: `shared/streams.py` (constants), `shared/redis_streams.py` (`append_capped`)
- Create: `core/attribution/log.py`
- Test: `tests/core/attribution/test_log.py`, `tests/shared/test_redis_streams.py` (add to it if it
  exists, else create)

**Interfaces:**
- Consumes: `Cause`, `ATTRIBUTED`, `cause_of` (Task 2); `ActionRequest`, `ActionResult`,
  `StateChangedEvent`, `ChangeContext` (`bus/schemas/events.py`).
- Produces, in `shared/streams.py`:

```python
ATTRIBUTION_CALLS_STREAM = "alfred:attribution:calls"
ATTRIBUTION_CHANGES_STREAM = "alfred:attribution:changes"
ATTRIBUTION_CAUSES_PREFIX = "alfred:attribution:causes:"   # + UTC date, a hash
REFLEX_DECISION_LOG_STREAM = "alfred:reflex:decision_log"
ATTRIBUTION_MAX_AGE = timedelta(days=30)
ATTRIBUTION_CALLS_MAXLEN: Final = 20_000
ATTRIBUTION_CHANGES_MAXLEN: Final = 50_000
REFLEX_DECISION_LOG_MAXLEN: Final = 10_000
```

- In `shared/redis_streams.py`:
  `async def append_capped(redis: AioRedis, stream: str, fields: Mapping[str, str], *, maxlen: int, max_age: timedelta, now: datetime) -> None`
  — one non-transactional pipeline: `XADD … MAXLEN ~ maxlen`, then an exact
  `XTRIM … MINID <ms of now - max_age>-0` (approximate trimming drops only whole radix nodes, so a small
  stream would never age out). It raises on failure; callers decide.
- In `core/attribution/log.py`:

```python
class AlfredCall(BaseModel):
    time: datetime
    source: str            # ActionRequest.source: reflex-engine, conscious-engine, trigger-engine, admin-api
    request_id: str
    target_service: str
    tool_name: str
    parameters: dict[str, Any]
    reason: str | None
    context_id: str | None  # None when the service returned none (an older home-service)

class AttributedChange(BaseModel):
    time: datetime          # the event's timestamp
    entity_id: str
    old_state: str | None
    new_state: str
    cause: Cause
    context: ChangeContext

def causes_key(day: date) -> str: ...
def call_context_id(result: ActionResult) -> str | None: ...
async def record_call(redis: AioRedis, action: ActionRequest, result: ActionResult, *, now: datetime | None = None) -> None: ...
async def record_change(redis: AioRedis, event: StateChangedEvent, *, now: datetime | None = None) -> Cause | None: ...
```

`call_context_id` reads `result.result["result"]["context_id"]` (HomeAgent stores home-service's whole
MCP response, so the tool's return sits under `"result"`), and returns `None` for anything not a
non-empty string. `record_call` appends an `AlfredCall` (as `{"call": model_dump_json()}`) only when
`result.status == "success"`. `record_change` increments field `cause_of(...) or "unknown"` in
`causes_key(today UTC)`, sets the key's TTL to 30 days, and appends an `AttributedChange` (as
`{"change": …}`) only for causes in `ATTRIBUTED`; it returns the cause. Both catch every exception and
log one warning, as `core/reflex/runner.py:count_decision` does.

- [ ] **Step 1: Write the failing tests** (fakeredis, as `tests/core/conscious/test_effects.py` sets it up):
  - `test_append_capped_trims_entries_older_than_max_age`: seed the stream with a raw `XADD` at the
    explicit id `<ms of now - 31 days>-0`, then `append_capped(..., now=datetime.now(UTC))`; only the new
    entry remains.
  - `test_call_context_id_reads_the_tool_result`: `{"id": "r", "result": {"context_id": "abc"}}` →
    `"abc"`; `{"id": "r", "result": {}}`, `None` and `{"data": "x"}` → `None`.
  - `test_record_call_appends_a_successful_call`: the stream holds one entry whose `AlfredCall` has the
    action's source, tool, parameters, reason and `context_id == "abc"`.
  - `test_record_call_skips_an_error_result`: the stream stays empty.
  - `test_record_change_counts_unknown_without_context`: returns `None`, hash field `unknown == 1`,
    changes stream empty, key TTL within `(29 days, 30 days]`.
  - `test_record_change_logs_attributed_but_not_outside`: a `user_id` event lands in the stream with
    `cause == "person"`; an `outside` event only bumps its counter.
  - `test_writes_never_raise_when_redis_refuses`: a redis whose `pipeline`/`hincrby` raise
    `redis.exceptions.ResponseError("OOM command not allowed")`; `record_call` and `record_change`
    return normally (Review Focus 5).
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/core/attribution/test_log.py tests/shared/test_redis_streams.py -q` → fails.
- [ ] **Step 3: Implement** the constants, `append_capped`, and `log.py` as in Interfaces.
- [ ] **Step 4: Run the tests and see them pass.**
- [ ] **Step 5: Gate, secret greps, commit** `feat(attribution): record Alfred's calls and attributed changes for 30 days`.

### Task 4: Record calls where they are routed, changes where Reflex reads them

**Files:**
- Modify: `core/routing/domain_router.py` (`route`, around line 151)
- Modify: `core/reflex/runner.py` (`process_stream_entry`, between the availability bridge and the
  attention gate, lines 226-234)
- Test: `tests/core/routing/test_domain_router.py`, `tests/core/reflex/test_runner_attention.py`,
  `tests/core/reflex/test_availability.py`

**Interfaces:**
- Consumes: `record_call`, `record_change` (Task 3).
- Produces: no new names. `DomainRouter.route` calls `record_call(self._redis, action, result)` after
  `agent.execute_action(action)` when `self._redis` is set, then returns the result unchanged.
  `process_stream_entry` calls `await record_change(redis, event)` on the bridged event, before the
  attention gate, so gated changes are counted too.

- [ ] **Step 1: Write the failing tests:**
  - `test_route_records_a_successful_call` (domain router): a fake agent returning a success result with
    `{"id": "r", "result": {"context_id": "abc"}}`; after `route`, `alfred:attribution:calls` holds one
    entry with `source == action.source`.
  - `test_route_returns_the_result_when_the_call_log_fails`: the router's redis raises on `pipeline`;
    `route` still returns the agent's result (Review Focus 5).
  - `test_gated_events_are_still_counted` (runner attention): an attention set that rejects the event;
    `process_stream_entry` returns False and today's causes hash has `unknown == 1`.
  - `test_a_bridged_return_keeps_its_context` (availability): feed `on → unavailable` then
    `unavailable → off` where the second event carries `ChangeContext(user_id="u")`; the bridged event
    `record_change` saw (patch it with an `AsyncMock(wraps=…)`) has `context.user_id == "u"` (Review
    Focus 4). If `bridge_availability`'s `model_copy` already keeps it, this passes as written; keep it
    as the guard.
- [ ] **Step 2: Run** the three test files → the new tests fail.
- [ ] **Step 3: Add the two calls** as in Interfaces. Comment each in one line, as the surrounding code
  does (best-effort bookkeeping; never blocks the result or the ACK). Then check that every production
  `DomainRouter(...)` that executes home actions is built with `redis` (`grep -rn "DomainRouter(" core`);
  one built without it logs no calls, so pass it there.
- [ ] **Step 4: Run the three files, then all of `tests/core/reflex`, `core/reflex/tests`,
  `tests/core/routing` and `tests/integration/test_reflex_end_to_end.py`.** All pass.
- [ ] **Step 5: Gate, secret greps, commit** `feat(attribution): log routed calls and every state change's cause`.

### Task 5: The cause in the prompt, the shadow log and memory

**Files:**
- Modify: `core/reflex/prompt.py` (`render_event`, line 243)
- Modify: `core/memory/ingestor.py` (`_build_observation_summary`, line 168)
- Test: `tests/core/reflex/test_event_line.py`, `tests/core/memory/test_passive_ingestion.py`,
  `tests/core/reflex/test_shadow_report.py`

**Interfaces:**
- Consumes: `cause_words` (Task 2), `LiveEntity` (`core/reflex/prompt.py:81`).
- Produces: `render_event(event, entities)` keeps its signature. When `cause_words` gives words, they
  are the first detail (`name (room): old → new · by Alex · brightness 60%`); `MAX_EVENT_DETAILS` still
  caps the attribute details only. The person's name is the live-state `friendly_name` of
  `context.person`, looked up in `entities`; without it `cause_words` falls back as Task 2 says.
  `parse_event` is unchanged (it reads only the part before the first `_DETAIL_SEP`).
  The memory summary appends `f", {words}"` after the suffix, with `name=None` (the ingestor has no live
  state, so a person reads as their entity id). `_build_observation_semantic_key` is unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `test_event_line_names_the_person_from_live_state`: event `light.bedroom_lamp off → on` with
    `ChangeContext(user_id="u", person="person.alex")` and entities holding `person.alex` with
    `friendly_name: Alex` → the line contains `" · by Alex"` right after the `off → on` part.
  - `test_event_line_without_a_person_entity_says_a_person`: same context, entities without
    `person.alex` and `person=None` → `" · by a person"` (Review Focus 3).
  - `test_event_line_without_context_is_unchanged`: equals the line today's code renders for the same
    event (assert the exact string; Review Focus 1).
  - `test_cause_does_not_push_out_attribute_details`: an event with three salient attributes and a cause
    renders all three details plus the cause.
  - `test_summary_says_who_caused_it` (ingestor): an observation whose `trigger_event` carries
    `{"by_service": true, …}` → summary ends with `", by Alfred"`; one without `context` → today's
    exact summary (Review Focus 1).
  - `test_report_shows_the_cause` (shadow report): a proposal observation whose event has
    `ChangeContext(parent_id="p")` renders a bullet containing `by an automation`.
- [ ] **Step 2: Run** the three files → the new tests fail.
- [ ] **Step 3: Implement** both changes as in Interfaces. The shadow report needs no code: it renders
  through `render_event`.
- [ ] **Step 4: Run** the three files, then `tests/core/reflex/test_prompt_*.py` and
  `tests/core/memory`. All pass; `test_prompt_wire_forms.py` must not change (it renders events without
  a context).
- [ ] **Step 5: Gate, secret greps, commit** `feat(reflex): say who caused a change in the prompt, shadow log and memory`.

### Task 6: Reflex records every decision with its prompt

**Files:**
- Create: `core/reflex/decision_log.py`
- Modify: `core/reflex/engine.py` (`__init__` line 58, `process_event` line 118, `process_trigger_fired`
  line 135), `core/reflex/__main__.py` (where the engine is built in `run`)
- Test: `tests/core/reflex/test_decision_log.py`, `tests/core/reflex/test_engine_decisions.py`

**Interfaces:**
- Consumes: `append_capped`, `REFLEX_DECISION_LOG_STREAM`, `REFLEX_DECISION_LOG_MAXLEN`,
  `ATTRIBUTION_MAX_AGE` (Task 3); `cause_of`, `Cause` (Task 2).
- Produces:

```python
class DecisionRecord(BaseModel):
    time: datetime
    origin: Literal["state_change", "trigger_fired"]
    event: dict[str, Any]       # the event's model_dump(mode="json")
    cause: Cause | None         # cause_of(event.context) for a state change; None for a trigger
    prompt: str
    proposal: ReflexProposal

class DecisionLog:
    def __init__(self, redis: AioRedis, *, clock: Callable[[], datetime] = _utc_now) -> None: ...
    async def record(self, origin: Literal["state_change", "trigger_fired"], event: StateChangedEvent | TriggerFired, prompt: str, proposal: ReflexProposal) -> None: ...
    async def read(self, *, since: datetime) -> list[DecisionRecord]: ...
```

`ReflexEngine.__init__` gains keyword-only `decision_log: DecisionLog | None = None`. Both `process_*`
methods call `await self._decision_log.record(...)` after `parse_decision`, when it is set, and return
the proposal unchanged; their return type stays `ReflexProposal`, so no caller or mock changes.
`record` is best-effort (warning on failure). `read` exists for slice 2's replay set and for this
task's tests; it is the only reader. `__main__.run` passes `DecisionLog(redis)`. Engines built without
one (tests, evals) record nothing.

- [ ] **Step 1: Write the failing tests:**
  - `test_record_stores_prompt_cause_and_proposal`: record a state change with
    `ChangeContext(user_id="u")`; `read(since=…)` returns one record with that prompt, `cause ==
    "person"` and the proposal.
  - `test_trigger_decisions_have_no_cause`: origin `trigger_fired` → `cause is None`.
  - `test_record_never_raises_when_redis_refuses` (Review Focus 5).
  - `test_engine_records_each_decision` (engine decisions): an engine with a `DecisionLog` on fakeredis
    and the file's existing stub inference; after `process_event`, one record whose `prompt` equals the
    prompt the stub received; after `process_trigger_fired`, a second with origin `trigger_fired`.
  - `test_engine_without_a_log_records_nothing`: the stream does not exist afterwards.
- [ ] **Step 2: Run** the two files → the new tests fail.
- [ ] **Step 3: Implement** `decision_log.py`, the engine hook and the `__main__` wiring.
- [ ] **Step 4: Run** the two files, then all of `tests/core/reflex` and `core/reflex/tests`. All pass.
- [ ] **Step 5: Docs.** In `.claude/rules/core/reflex-engine.md`, extend the "Records what it saw"
  bullet: every decision that reached the model is also kept with its prompt and cause in
  `alfred:reflex:decision_log` for 30 days (private; slice 2's replay set). In `core/CLAUDE.md`'s Reflex
  list add `decision_log.py`.
- [ ] **Step 6: Gate, secret greps, commit** `feat(reflex): keep every decision with its prompt for 30 days`.

### Task 7: The oversight report and the docs

**Files:**
- Create: `core/attribution/report.py`, `docs/attribution.md`
- Modify: `docs/PRD.md` (section 4.4 table; section 9 table), `docs/architecture.md` (one line where the
  state-change flow is described), `core/CLAUDE.md` (an "Attribution (`attribution/`)" section)
- Test: `tests/core/attribution/test_report.py`

**Interfaces:**
- Consumes: `AlfredCall`, `AttributedChange`, `causes_key` (Task 3); `cause_words` (Task 2); the
  stream constants (Task 3); `get_user_timezone`, `create_redis`, `revrange`/`XRANGE` as
  `core/reflex/shadow_report.py` uses them.
- Produces:

```python
PARTS: Final[Mapping[str, str]] = {
    "reflex-engine": "Reflex",
    "conscious-engine": "System 2",
    "trigger-engine": "a trigger",
    "admin-api": "the admin API",
}
async def build_report(redis: AioRedis, *, days: int, now: datetime, tz_name: str) -> str: ...
def main() -> None: ...   # python -m core.attribution.report --days N (default 7)
```

The report is Markdown, in the user's local time, oldest first, with three sections:
1. **Changes by cause**: one row per UTC day in the window, columns `person`, `alfred`, `automation`,
   `outside`, `unknown` (from the causes hashes).
2. **What Alfred changed**: one bullet per `AlfredCall` in the window — time, part (`PARTS`, else the
   raw source), tool, parameters, and the reason in quotes when set — and under it each
   `AttributedChange` whose `context.id` or `context.parent_id` equals the call's `context_id`
   (`entity: old → new`). A call with no `context_id` says `no context id (older home-service)`; a call
   whose id matches nothing says `no change seen`.
3. **Changes by people and automations**: every remaining `person` or `automation` change — time,
   `entity: old → new`, and `cause_words(change.context)`.

When any change matched to a call has a cause other than `alfred`, the report opens with one line:
`N changes matched Alfred's calls but were not attributed to Alfred: home-service is not connected to
Home Assistant as a user of its own.` (Review Focus 2.)

- [ ] **Step 1: Write the failing tests** (fakeredis, records written through Task 3's functions with
  explicit `now`):
  - `test_counts_table_has_every_cause_column`: two days of counters → both rows, `unknown` included.
  - `test_alfred_call_lists_the_changes_it_caused`: a System 2 call with `context_id="abc"` and two
    changes with `context.id == "abc"` → both under the call's bullet, which names `System 2`.
  - `test_a_script_alfred_ran_is_joined_by_parent_id`: a change with `parent_id == "abc"` appears under
    the call.
  - `test_call_without_context_id_says_so` and `test_call_with_no_change_says_so`.
  - `test_people_and_automations_section_excludes_matched_changes`.
  - `test_flags_alfred_changes_read_as_a_person`: a change with `context.id == "abc"` and cause `person`
    → the report starts with the mismatch line, `N` = 1.
  - `test_window_excludes_older_entries`: a call at `now - 8 days` is absent from a 7-day report.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/core/attribution/test_report.py -q` → fails.
- [ ] **Step 3: Implement `report.py`.** Read the streams with `XRANGE` from the window start's
  millisecond id, then keep records whose own `time` is in the window (stream ids are write times; the
  tests write records with an explicit `now`). Skip entries that fail validation (log at debug), as the
  shadow report does. The module
  docstring gives the run command, `docker exec alfred python -m core.attribution.report --days 7`, and
  says the output names people and devices, so it is read privately.
- [ ] **Step 4: Run the tests and see them pass.**
- [ ] **Step 5: Docs.** `docs/attribution.md`: why (who did what, and Alfred never learning its own
  actions as habits from slice 3 on), the four causes and their rules, the copy, the three logs and the
  counters with retention, the report command, the operator requirement (home-service connects as an HA
  user of its own, not the owner's), and what an older home-service looks like (`unknown`). PRD 4.4: a
  row "Who did it: every home change says whether a person, Alfred, an automation or something outside
  caused it; a private report lists what Alfred changed and why" — status `In review`, reference
  `docs/attribution.md` and the spec. PRD section 9: a row pointing at `docs/attribution.md`.
- [ ] **Step 6: Gate, secret greps, commit** `feat(attribution): a private who-did-what report`.

### Task 8: The evals' fake Home Assistant says who caused each change

**Files:**
- Modify: `evals/harness/fake_ha.py` (`set_state` :221, `restore_world` :231, `_command` :284,
  `_call_service` :310, `_push_change` :342)
- Modify: `evals/harness/scenario.py` (`HaEvent` :103), `evals/harness/driver.py` (:520)
- Modify: `evals/harness/worlds/apartment.yaml` (persons :29-30)
- Modify: `evals/harness/checks/llm.py` (add `prompt_contains` beside `prompt_not_contains` :186),
  `evals/harness/checks/__init__.py` (`DETERMINISTIC`, `watches_reflex` :68)
- Create: `evals/suites/reflex/attribution_person.yaml`, `evals/suites/reflex/attribution_automation.yaml`
- Modify: `docs/evals.md` (the `ha_event` step's `by`, the `prompt_contains` check)
- Test: `tests/evals/harness/test_fake_ha.py`, `tests/evals/harness/test_checks.py`,
  `tests/evals/harness/test_scenario.py`, `tests/evals/test_goldens_load.py`

**Interfaces:**
- Produces:
  - `fake_ha.SERVICE_USER: Final = {"id": "eval-alfred", "name": "Alfred", "is_owner": False, "is_admin": False}`, answered to `{"type": "auth/current_user"}`.
  - `FakeHA.set_state(entity_id, state, attributes=None, *, by: str = "outside")`. Contexts:
    `outside` → `{"id": <fresh>, "user_id": None, "parent_id": None}`; `automation` →
    `parent_id = "eval-automation"`; `person.<x>` → `user_id` = that person's `user_id` attribute in the
    world, else `ValueError` naming the person.
  - `_push_change(entity_id, old, new, context)`: the event dict carries `"context"` at event level and
    inside `new_state`, as HA sends it.
  - `_call_service` pushes its changes with `{"id": f"eval-{msg_id}", "user_id": SERVICE_USER["id"],
    "parent_id": None}`, the same id its result already returns. `restore_world` pushes as `outside`.
  - `HaEvent.by: str = Field(default="outside", pattern=r"^(outside|automation|person\.[a-z0-9_]+)$")`;
    the driver passes `by=e.by`.
  - World persons gain `user_id` attributes: `eval-user-alex`, `eval-user-sam`.
  - `prompt_contains(evidence, p: PromptParams) -> CheckResult`: passes when any call of `p.role`
    contains the needle, fails naming the role otherwise (and when no call of that role was recorded);
    registered as `"prompt_contains"`; `watches_reflex` returns True for it with `role == "system1"`,
    as for `prompt_not_contains`.
  - Goldens, both `status: pending`, `prd: [4.4.reflex-attribution]`, `tags: [attribution]`:
    `reflex.attribution.person` — `ha_event {entity_id: light.bedroom_lamp, state: "on", by: person.alex}`,
    expect `prompt_contains {role: system1, text: "by Alex"}`; `reflex.attribution.automation` — the
    same with `by: automation`, expect `text: "by an automation"`. Pending, because the evals' home-service
    forwards no context until PR B ships (Task 12 flips them).

- [ ] **Step 1: Write the failing tests:**
  - fake HA: `test_answers_current_user_with_the_service_user`; `test_call_service_changes_carry_the_call_context`
    (the pushed event's `context` has `id == "eval-<msg_id>"` and the service user's id, matching the
    result's `context.id`); `test_set_state_by_a_person_carries_their_user_id`;
    `test_set_state_by_an_unknown_person_raises`; `test_set_state_defaults_to_outside`.
  - checks: `test_prompt_contains_passes_and_fails` (one passing needle, one missing, one with no
    system1 call); `test_watches_reflex_covers_prompt_contains`.
  - scenario: `test_ha_event_by_rejects_other_values` (`by: "alfred"` fails validation).
  - goldens load: the two new goldens load and are pending.
- [ ] **Step 2: Run** the four test files → the new tests fail.
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run** the four files, then all of `tests/evals`. All pass.
- [ ] **Step 5: Docs** in `docs/evals.md` as listed under Files.
- [ ] **Step 6: Gate, secret greps, commit** `test(evals): the fake Home Assistant says who caused each change`.

### PR A

- [ ] **Step 1:** Run the full alfred gate on the branch tip, and the secret greps over
  `git diff origin/master...HEAD`. Both clean.
- [ ] **Step 2: Stop and ask the owner** to push and open PR A (title
  `feat: who caused each home change (Reflex anticipation, slice 1)`; body per
  `.github/pull_request_template.md`, `Refs #317`, ending with the Claude Code line). The merge deploys
  and needs the owner's go. After the merge, note the squash commit's SHA for Task 10.

---

## Phase B — alfred-home-service (PR B), after PR A has merged

### Task 9: The connection knows its own HA user and passes each change's context

**Files:**
- Modify: `app/ha_connection.py` (`StateListener` :36, `_on_connected` :267, `_handle_message` :360-366,
  `_handle_state_changed` :369-389)
- Modify: `app/live_state.py` (`on_state_changed` :145 takes and ignores the new argument),
  `app/state_forwarder.py` (`on_state_changed` :56, signature only in this task)
- Modify: `tests/fake_ha.py` (answer `auth/current_user`; `_state_changed` and `push_state_changed`
  take an optional `context`)
- Test: `tests/test_ha_connection.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class HAContext:
    id: str | None
    user_id: str | None
    parent_id: str | None
    person: str | None   # entity id of the person whose attributes.user_id == user_id
    by_self: bool        # user_id is this connection's own HA user, and that user is not the owner

StateListener = Callable[[str, str | None, str | None, dict[str, Any], HAContext | None], Awaitable[None]]
```

`HAConnection` gains `user_id: str | None` and `is_owner: bool`, set in `_on_connected` from
`{"type": "auth/current_user"}` right after subscribing. A failed `auth/current_user` logs a warning and
leaves `user_id = None` (setup continues). A user with `is_owner` logs one WARNING (home-service is
using the owner's token, so its changes cannot be told from the owner's) and `by_self` is never set.
`_handle_message` passes `event.get("context")`, falling back to `new_state["context"]`; no context →
`None` to listeners. `person` comes from `self.states` (`person.*` entities); none matching → `None`.
The HA person entity is the spec's "Alfred person" mapping: Alfred keeps no person registry of its own,
and live state already carries each person's friendly name.

- [ ] **Step 1: Write the failing tests** (the repo's fake HA):
  - `test_context_reaches_listeners_with_person_and_self`: the fake HA's current user is `{"id": "svc",
    "is_owner": False, …}`, states hold `person.alex` with `user_id: "u-alex"`; a change with
    `user_id "u-alex"` reaches a listener with `HAContext(person="person.alex", by_self=False, …)`; one
    with `user_id "svc"` has `by_self=True`.
  - `test_owner_token_never_marks_changes_as_self`: current user `is_owner: True`; a change with that
    user's id → `by_self=False`, and a WARNING was logged (Review Focus 2).
  - `test_current_user_failure_is_tolerated`: the fake HA answers `auth/current_user` with an error; the
    connection still reaches `connected` and `by_self` stays False (Review Focus 2).
  - `test_user_without_person_entity_has_no_person` (Review Focus 3).
  - `test_event_without_context_passes_none`.
- [ ] **Step 2: Run** `uv run pytest tests/test_ha_connection.py -q` → the new tests fail.
- [ ] **Step 3: Implement** as in Interfaces; update the two existing listeners' signatures.
- [ ] **Step 4: Run** `uv run pytest -q`. All pass.
- [ ] **Step 5: Gate, secret greps, commit** `feat: pass who caused each state change to listeners`.

### Task 10: Send the context to Alfred, and return each call's context id

**Files:**
- Modify: `pyproject.toml` + `uv.lock` (the `alfred-sdk` rev → PR A's squash commit), `app/state_forwarder.py`
  (`build_event`, `on_state_changed`), `app/home_feature.py` (`_execute` :86-113)
- Modify: `CLAUDE.md` / `README.md` where the `home/state_changed` contract is described
- Test: `tests/test_state_forwarder.py`, `tests/test_home_feature.py`

**Interfaces:**
- Consumes: `HAContext` (Task 9); `alfred_sdk.events.ChangeContext` (PR A).
- Produces: `StateForwarder.build_event(entity_id, old_state, new_state, attributes, context: HAContext | None = None)`
  fills `StateChangedEvent.context = ChangeContext(id=…, user_id=…, parent_id=…, person=…, by_service=context.by_self)`,
  or leaves it `None`. Both `_execute` returns gain `"context_id"`: `call_service(...)`'s
  `result["context"]["id"]` when it is a string, else `None`.

- [ ] **Step 1: Bump the pin** to PR A's squash commit (`uv lock` after editing the rev); run
  `uv run pytest -q` — still green.
- [ ] **Step 2: Write the failing tests:**
  - `test_build_event_carries_the_context`: an `HAContext(by_self=True, person=None, …)` → the event's
    `context.by_service is True` and the ids copied; `None` → `event.context is None`.
  - `test_execute_returns_the_call_context_id` (both the generated path and the `home.call_service`
    escape hatch): the fake connection's `call_service` returns `{"context": {"id": "c9"}, "response":
    None}` → `"context_id": "c9"`; returning `{}` → `"context_id": None`.
- [ ] **Step 3: Run** the two test files → the new tests fail.
- [ ] **Step 4: Implement** as in Interfaces, and update the contract text in the repo docs.
- [ ] **Step 5: Run** the gate. All pass.
- [ ] **Step 6: Secret greps, commit** `feat: send who caused each change to Alfred and return call context ids`.
- [ ] **Step 7: Stop and ask the owner** to push and open PR B (its own PR template if present,
  referencing alfred#317). The merge deploys through alfred's dispatch and needs the owner's go.

---

## Phase C — operator step, goldens, production check

### Task 11: home-service gets an HA user of its own (needs the owner's go)

Every HA token in use today belongs to the owner's HA user, so until this step Alfred's changes read as
the owner's and the report prints its mismatch line.

- [ ] **Step 1: Ask the owner** for the go, then guide them through it in HA's UI: create a user named
  "Alfred" that can log in, **not** an administrator and not the owner. If HA also created a person for
  it, note it: that person would appear in Reflex's House section; the owner decides whether to keep it.
- [ ] **Step 2:** Logged in as that user, the owner mints a long-lived access token (profile → Security).
- [ ] **Step 3:** Replace home-service's HA token in the deploy directory's operator state (never in a
  repo file) and restart the alfred stack the usual way (operator runbook, "Continuous deployment").
- [ ] **Step 4: Verify** home-service logs `Connected to HA` with no `auth/current_user` warning and no
  owner warning. If any command is refused for a non-admin user, make the user an administrator (still
  not the owner): `by_self` needs only a non-owner user of its own.

### Task 12: The goldens go live (PR C)

- [ ] **Step 1:** Move the evals' home-service worktree (`ALFRED_EVALS_HOME_SERVICE`) to home-service
  `origin/main` with PR B in it.
- [ ] **Step 2:** Run `uv run alfred evals run --include-pending --tag attribution --epochs 1` (vLLM is
  shared with production: these goldens are two requests, one at a time). Both pass.
- [ ] **Step 3:** Flip both goldens to `status: shipped`; gate; secret greps; commit
  `test(evals): attribution goldens ship`. **Ask the owner** to push and open PR C; its merge needs the
  owner's go.

### Task 13: Done when — a week in production

- [ ] **Step 1: Spot check with the owner** the day Task 11 lands: the owner turns a light on in the HA
  app, flips a wall switch, asks Alfred to change a light, and lets an HA automation run. Then
  `docker exec alfred python -m core.attribution.report --days 1`: each change shows `person` (by the
  owner's name), `outside`, `alfred` under System 2's call, and `automation`, and no mismatch line.
- [ ] **Step 2:** After seven days, run the report with `--days 7`. "Done" is every day's `unknown`
  column at 0 since PR B deployed. Post only the daily cause counts and the spot check's verdict to
  #317 (with the owner's OK); the report itself names people and devices and stays private.
