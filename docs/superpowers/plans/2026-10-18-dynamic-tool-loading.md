# Dynamic Tool Loading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** System 2 sends only its always-loaded tools on each model request and finds the other home-service tools with Pydantic AI's local `search_tools`.

**Architecture:** `RegistryToolset._build` marks every registry tool whose `audience` is not `reflex` as `defer_loading`. A `ToolSearch` capability built in a new `core/conscious/tools/search.py` (keyword strategy pinned, Alfred's own descriptions) joins System 2's capabilities. Discovery rides the message history the session store already round-trips. `TOOL_RESULTS_KEPT` moves from 3 to 4, so a search pair does not displace a real result.

**Tech Stack:** Python 3.13, Pydantic AI 2.55.0 (`pydantic_ai.capabilities.ToolSearch`), pydantic-ai-harness 0.55.0, pytest + fakeredis, `tests/llm_fakes.py` `Script` (a `FunctionModel`; probed: it takes the local search path, so `search_tools` appears in `offered()` and searched tools appear from the next request on).

**Spec:** `docs/superpowers/specs/2026-10-18-dynamic-tool-loading-design.md`

## Global Constraints

- The deferral rule reads registry metadata only: `info.audience != REFLEX_AUDIENCE` (`core/reflex/tool_registry.py`). No tool names in code.
- `ToolSearch(strategy="keywords", max_results=10, tool_description=..., parameter_description=...)`. Pin the strategy explicitly; never rely on the default.
- `REQUEST_LIMIT` stays 10 in code. Only Task 4's measurement may change it.
- No `sequential` flag on any tool: the calls of one response run concurrently.
- No prompt changes. `shared/prompts/defaults/` and Langfuse are untouched.
- The repo is public:
  - no hostnames, IPs, the owner's domain or keys;
  - no model identifiers in code, comments, docs or commits;
  - no personal or device names.
- Gate (run once per task, before its commit):

  ```
  uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync mypy --strict alfred_cli/ alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/ && PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q -p no:randomly
  ```

  While iterating, run only the narrow test file.
- Commit messages are conventional (`feat(conscious): …`, `test(conscious): …`, `docs(llm): …`).

## Review Focus

1. **A model calls a deferred tool without searching.** A weak model, or one that remembers the name from history, does this. Expected: the framework refuses with a hint to search, the model searches, the call runs and the turn is answered. Test: Task 1, `test_calling_an_unsearched_tool_is_refused_with_a_hint_and_the_turn_recovers`.
2. **A search that matches nothing.** Expected: the model reads "No matching tools", nothing is dispatched, and the turn is answered. Test: Task 1, `test_a_search_that_matches_nothing_dispatches_nothing`.
3. **A guest searches for, or names, an elevated deferred tool.** Expected: the search never returns it, and a direct call is never routed. Test: Task 1, `test_a_guest_can_neither_find_nor_call_an_elevated_deferred_tool`.
4. **A session stored before this change.** It holds calls to tools that are now deferred, and no search parts. Expected: it replays without error, and those tools are withheld until searched. Test: Task 2, `test_a_session_stored_before_tool_search_replays_with_its_tools_withheld`.
5. **One of two concurrent side-effecting calls fails.** Expected: the other still runs and settles `ok`, the failed one settles `failed`, and both are in `actions_taken`. Test: Task 3, `test_one_failed_call_of_two_concurrent_side_effects_leaves_the_other_settled_ok`.

---

### Task 1: Defer non-reflex registry tools and add tool search

**Files:**
- Create: `core/conscious/tools/search.py`
- Modify: `core/conscious/tools/registry.py` (`_build`, the `ToolDefinition(...)`)
- Modify: `core/conscious/agent.py` (`build_system2_agent` capabilities)
- Modify the test helpers that build `ToolInfo`, so that tests which call a registry tool directly keep it loaded:
  - `tests/core/conscious/test_agent.py`
  - `tests/core/conscious/test_effects.py`
  - `tests/core/conscious/test_engine.py`
  - `tests/core/conscious/tools/test_registry.py`
- Test: `tests/core/conscious/test_agent.py`, `tests/core/conscious/tools/test_registry.py`
- Docs:
  - `docs/llm.md`: a new `### Tool search` subsection after "The catalog and its toolsets", and the catalog table's row 5;
  - `core/CLAUDE.md`: its `tools/` lines;
  - root `CLAUDE.md`: the `core/conscious/tools/` key path, which gains `search`.

**Interfaces:**
- Produces, in `core/conscious/tools/search.py`:
  - `SEARCH_STRATEGY: Final = "keywords"`
  - `SEARCH_MAX_RESULTS: Final = 10`
  - `SEARCH_DESCRIPTION: Final[str]` and `QUERIES_DESCRIPTION: Final[str]` (exact copy below)
  - `def tool_search() -> ToolSearch[TurnDeps]`
- Produces: every registry tool whose audience is not `reflex` has `tool_def.defer_loading is True`. The model searches through the framework's tool `search_tools`, which takes `{"queries": [str, ...]}`.
- Later tasks build their registry fixtures with `_info(name, risk=..., audience=...)`. The `audience` parameter defaults to `REFLEX_AUDIENCE`, so a fixture tool stays loaded; pass `audience="conscious"` for a deferred one.

Exact copy:

```python
SEARCH_DESCRIPTION = (
    "Find a smart-home tool that is not listed yet by describing what it should do. "
    "The listed tools are already loaded. Matching tools can be called from your next step. "
    "Give one short query per need; one call can carry several."
)
QUERIES_DESCRIPTION = "What each needed tool should do, in a few words: one query per need."
```

- [ ] **Step 1: Give every test `_info` helper an `audience` parameter**

  Add `audience: str = REFLEX_AUDIENCE` to the `_info` helper in each of the four test files above, passed through to `ToolInfo(audience=...)`. Existing tests keep their meaning: they call an always-loaded tool. Run the gate's pytest part. Expected: PASS, unchanged.

- [ ] **Step 2: Write the failing tests**

  In `tests/core/conscious/tools/test_registry.py`:
  - `test_reflex_audience_tools_load_and_others_are_deferred`
    - `RegistryToolset().get_tools(ctx)` over three tools: `_info("home.light_turn_on")` (reflex), `_info("home.vacuum_start", audience="conscious")` and `_info("home.call_service", "critical", audience="conscious")`.
    - Assert `defer_loading` per wire name is `{"home_light_turn_on": False, "home_vacuum_start": True, "home_call_service": True}`.

  In `tests/core/conscious/test_agent.py`, all through `_agent()` (the real `build_system2_agent`). In each, the registry is `[_info("home.light_turn_on"), _info("home.vacuum_start", audience="conscious")]` unless stated otherwise.
  - `test_tool_search_is_pinned_and_described`
    - `cap = tool_search()`.
    - Assert `(cap.strategy, cap.max_results, cap.tool_description, cap.parameter_description) == ("keywords", 10, SEARCH_DESCRIPTION, QUERIES_DESCRIPTION)`.
  - `test_only_reflex_registry_tools_are_loaded_before_a_search`
    - `Script("hi")`.
    - Assert `"home_light_turn_on"`, `"search_tools"` and `"memory_recall_memories"` are in `script.offered(0)`, and `"home_vacuum_start"` is not.
    - Assert the offered `search_tools` definition's `description == SEARCH_DESCRIPTION`.
  - `test_a_searched_tool_is_loaded_and_dispatched`
    - `Script(tool_call("search_tools", {"queries": ["vacuum start"]}), tool_call("home_vacuum_start", {"entity_id": "vacuum.hall"}), "Started, sir.")`.
    - Assert:
      - the output is `"Started, sir."`;
      - `"home_vacuum_start" in script.offered(1)`;
      - the router was awaited once, with an `ActionRequest` whose `tool_name == "home.vacuum_start"`;
      - `deps.actions_taken == ["home.vacuum_start"]` (the search is not an action).
  - `test_calling_an_unsearched_tool_is_refused_with_a_hint_and_the_turn_recovers`
    - `Script(tool_call("home_vacuum_start", {...}), tool_call("search_tools", {"queries": ["vacuum start"]}), tool_call("home_vacuum_start", {...}), "Started, sir.")`.
    - Assert:
      - request 1's last message holds a `RetryPromptPart` whose text contains `"search"`;
      - the router was awaited exactly once;
      - the output is `"Started, sir."`.
  - `test_a_search_that_matches_nothing_dispatches_nothing`
    - `Script(tool_call("search_tools", {"queries": ["teleport the cat"]}), "I have no means of doing that, sir.")`.
    - Assert:
      - the router was never awaited;
      - request 1 carries the search result, and its text contains `"No matching tools"`;
      - the output is the scripted reply.
  - `test_a_guest_can_neither_find_nor_call_an_elevated_deferred_tool`
    - Run as a guest, over `[_info("home.lock_door", "elevated", audience="conscious"), _info("home.vacuum_start", audience="conscious")]`.
    - `Script(tool_call("search_tools", {"queries": ["lock the door"]}), tool_call("home_lock_door", {"entity_id": "lock.front"}), "I'm afraid I can't, sir.")`.
    - Assert:
      - `"home_lock_door"` appears neither in request 1's search result nor in `offered(1)` or `offered(2)`;
      - the router was never awaited.

  Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q -p no:randomly tests/core/conscious/test_agent.py tests/core/conscious/tools/test_registry.py`
  Expected: the new tests FAIL. `search.py` does not exist yet, and nothing is deferred.

- [ ] **Step 3: Implement**

  - `core/conscious/tools/search.py`: the constants above, and `tool_search()` returning `ToolSearch(strategy=SEARCH_STRATEGY, max_results=SEARCH_MAX_RESULTS, tool_description=SEARCH_DESCRIPTION, parameter_description=QUERIES_DESCRIPTION)`. Its module docstring cites the spec and says why the strategy is pinned.
  - `registry.py` `_build`: `ToolDefinition(..., defer_loading=info.audience != REFLEX_AUDIENCE)`. Import `REFLEX_AUDIENCE` from `core.reflex.tool_registry` at runtime: it is a value, not just a type.
  - `agent.py`: put `tool_search()` in the capabilities list after `RecordEffects()`. Passing it explicitly replaces the framework's auto-injected one, which has the same id.

- [ ] **Step 4: Run the narrow tests, then the gate**

  Expected: all PASS. If an existing test elsewhere calls a registry tool built without the helper, give it `audience=REFLEX_AUDIENCE`, unless it is testing deferral.

- [ ] **Step 5: Docs**

  `docs/llm.md` gets a `### Tool search` subsection covering:
  - the rule (reflex audience loaded, everything else on demand);
  - `search_tools`, the pinned keyword strategy and the 10-result cap;
  - the refusal a model gets for an unsearched tool;
  - that searches are not recorded in `actions_taken`;
  - that a guest's corpus is already scoped;
  - that embedding search is the planned follow-up.

  Row 5 of the catalog table gains "non-reflex tools load on demand, see Tool search". Update the two `CLAUDE.md` lines.

- [ ] **Step 6: Commit**

  ```bash
  git add core/conscious/tools/search.py core/conscious/tools/registry.py core/conscious/agent.py tests docs/llm.md core/CLAUDE.md CLAUDE.md
  git commit -m "feat(conscious): load non-reflex registry tools on demand through tool search"
  ```

### Task 2: Discovery across turns and the kept tool results

**Files:**
- Modify: `core/conscious/history.py` (`TOOL_RESULTS_KEPT` and its comment; the module docstring gains one sentence on tool search)
- Test: `tests/core/conscious/test_engine.py`
- Docs: `docs/llm.md` (the Compaction paragraph); `core/CLAUDE.md` needs no change (the constant names stay)

**Interfaces:**
- Consumes from Task 1:
  - the `_info(name, risk, audience)` helper in `test_engine.py`;
  - the `search_tools` wire name;
  - `audience="conscious"` for a deferred tool.
- Produces: `TOOL_RESULTS_KEPT: Final = 4`. The comment says that a tool search counts as one pair.

- [ ] **Step 1: Write the failing tests**

  All in `tests/core/conscious/test_engine.py`, modelled on `test_a_tool_result_reaches_the_next_turn` (the real agent through `_deps`, `session_mgr=_sessions()`, `domain_router=_router()`).
  - `test_a_tool_found_in_one_turn_is_still_loaded_in_the_next`
    - Registry: `[_info("home.vacuum_start", audience="conscious")]`.
    - One `Script` across both turns: `tool_call("search_tools", {"queries": ["vacuum start"]})`, `tool_call("home_vacuum_start", {"entity_id": "vacuum.hall"})`, `"Started, sir."`, `tool_call("home_vacuum_start", {"entity_id": "vacuum.hall"})`, `"Started again, sir."`.
    - Turn 2's first request is `script.requests[3]`. Assert:
      - `"home_vacuum_start" in script.offered(3)`;
      - the router was awaited twice;
      - `script.call_count == 5` (no second search).
  - `test_a_session_stored_before_tool_search_replays_with_its_tools_withheld`
    - Before the turn, store `[ModelRequest(user "start the vacuum"), ModelResponse(ToolCallPart("home_vacuum_start", {...}, tool_call_id="c1")), ModelRequest(ToolReturnPart("home_vacuum_start", "ok", tool_call_id="c1")), ModelResponse(TextPart("Started, sir."))]` with `SessionManager.store_run("sess-1", messages, "sir")`.
    - Then run one turn with `Script("It is running, sir.")`. Assert:
      - the reply is published;
      - `"home_vacuum_start" not in script.offered(0)`;
      - request 0's messages include the stored `ToolCallPart`.
  - Extend whichever test asserts the history constants, or add `test_history_bounds` asserting `TOOL_RESULTS_KEPT == 4`.

  Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q -p no:randomly tests/core/conscious/test_engine.py`
  Expected: the constant test FAILS. The two replay tests may already pass, which pins behaviour Task 1 created. Say so in the report.

- [ ] **Step 2: Set `TOOL_RESULTS_KEPT = 4`**, with its comment. Add the docstring sentence:

  > a tool search's call/return pair counts as one of the kept pairs and is never blanked; SlidingWindowCompaction dropping it unloads the tools it found, and the model searches again.

- [ ] **Step 3: Run the narrow tests, then the gate.** Expected: all PASS. That includes `test_a_long_session_is_compacted_before_it_is_stored`, which reads the constant.

- [ ] **Step 4: Docs.** Update `docs/llm.md`'s Compaction paragraph to match: keep 4, a search counts as a pair, compaction can unload found tools.

- [ ] **Step 5: Commit**

  ```bash
  git add core/conscious/history.py tests/core/conscious/test_engine.py docs/llm.md
  git commit -m "feat(conscious): keep a search's found tools across turns and one more result pair"
  ```

### Task 3: Concurrent side-effecting calls through the real engine

**Files:**
- Test: `tests/core/conscious/test_effects.py` (the real-engine section: `_engine`, `_pending`, `_deliver`, `_request`)
- Modify `_engine` to take the registry tools as a parameter, with the current single tool as the default.
- No production change is expected. If a test exposes a defect, fix it in the module that owns it and say so in the report.

**Interfaces:**
- Consumes:
  - from Task 1, `_info(..., audience=...)` and `search_tools`;
  - `EffectLedger(redis).read(event_id) -> list[Effect]`;
  - `Effect(name, args, status)`;
  - the published response's `actions_taken` field, in `USER_RESPONSES_STREAM` entries' `event` JSON.

- [ ] **Step 1: Write the tests**

  In each, the registry is `[_info("home.vacuum_start", audience="conscious"), _info("home.light_turn_on")]`. The request comes from `_request()`, delivered with `_pending` and `_deliver`.
  - `test_two_side_effects_in_one_response_both_act_and_settle`
    - `Script(tool_call("search_tools", {"queries": ["vacuum start"]}), tool_calls(("home_vacuum_start", {"entity_id": "vacuum.hall"}), ("home_light_turn_on", {"entity_id": "light.porch"})), "Done, sir.")`, at `delivery=1`.
    - Assert:
      - the router was awaited twice, with tool names `{"home.vacuum_start", "home.light_turn_on"}`;
      - `sorted(await EffectLedger(redis).read(request.event_id), key=lambda e: e.name)` equals the two `Effect(..., "ok")`;
      - the published reply is `"Done, sir."`, and its `actions_taken` holds both registry names (in any order);
      - nothing is left pending.
  - `test_a_redelivery_after_two_concurrent_side_effects_acts_on_neither`
    - Model it on `test_a_redelivery_after_a_crash_answers_from_the_ledger_without_running`.
    - `Script(search, the same two-call response, ModelHTTPError(503, "m"))`.
    - Run `engine.process_request(request)` directly: it raises, and that is the crashed delivery. Then `_deliver(..., delivery=2)` through a spy whose `process_request` is an `AsyncMock`.
    - Assert:
      - the router was awaited exactly twice in total;
      - the spy's `process_request` was never awaited;
      - the one published reply is the ledger's partial-turn reply, naming both actions;
      - nothing is left pending.
  - `test_one_failed_call_of_two_concurrent_side_effects_leaves_the_other_settled_ok`
    - The router fails `home.vacuum_start` and succeeds `home.light_turn_on`: a `side_effect` keyed on `action.tool_name`, returning `MagicMock(status="error", error="vacuum stuck")` or the success mock.
    - Script: search, the two-call response, `"The light is on; the vacuum is stuck, sir."`.
    - Assert:
      - the ledger statuses by name are `{"home.vacuum_start": "failed", "home.light_turn_on": "ok"}`;
      - `actions_taken` holds both;
      - the reply is published.

  Run: `PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -q -p no:randomly tests/core/conscious/test_effects.py`
  Expected: PASS. These tests pin behaviour that should already hold. Any failure is a real defect: investigate it with superpowers:systematic-debugging before changing code.

- [ ] **Step 2: Gate**, then commit:

  ```bash
  git add tests/core/conscious/test_effects.py
  git commit -m "test(conscious): concurrent side-effecting calls through the real engine"
  ```

### Task 4: Live verification (controller)

The controller runs this task, not an implementer. At most 4 requests are in flight against the local vLLM.

- [ ] **Step 1: Evals, branch against master.**
  - Run `uv run alfred evals run home_control conversation --epochs 5`, with `ALFRED_EVALS_HOME_SERVICE` set to the `evals-main` home-service worktree at `origin/main`.
  - Run it once on this branch and once on a detached `master` worktree.
  - Expected: no golden's pass rate drops beyond run-to-run noise.
- [ ] **Step 2: Requests per turn and input tokens.**
  - Use a scratchpad script (not committed) over both runs' `.eval` logs: `inspect_ai.log.read_eval_log`, `sample.store["evidence"]["llm_calls"]`.
  - Count System 2 requests per turn for each golden, grouping requests by the number of user-text messages they carry.
  - Count the System 2 tool definitions and their prompt tokens per request.
  - **Ruling rule:** raise `REQUEST_LIMIT` only if a golden's turn on the branch reaches 8 or more requests where master's did not.
- [ ] **Step 3: File the follow-up issue.**
  - Title: "System 2 tool search: embedding strategy (bge-m3) if keyword search misses".
  - Body: Summary, Context and Acceptance; `priority: medium`; cite the eval misses, if any.
- [ ] **Step 4: Gate and secret hygiene.**
  - Run the full gate on the branch head.
  - Run the two runbook greps (`~/code/alfred-deploy/PWA-EXPOSURE-RUNBOOK.md` §Secret hygiene; run them from there, never quoted). Expected: no matches.
- [ ] **Step 5: Ask the owner.** Pushing, the PR (template plus attribution line) and posting the scorecards to #317 need the owner's go. A merge deploys.
- [ ] **Step 6: After the merge.**
  - Send one production turn that needs a deferred tool, read-only if possible, and one that needs only reflex tools.
  - From Langfuse, read the tool count and input tokens of each request, plus the search call.
  - Post them to #317 against the 2026-10-15 measurement (121 tools, 18–25k input tokens).
