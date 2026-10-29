# Reflex anticipation, slice 2 ("Reflex asks, and acts on nothing yet") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reflex runs on Pydantic AI and never proposes reversing a change a person just made. It sees the
weather and the sun. Once the owner switches it from `shadow` to `ask`, it asks about what it would do,
through a notification that expires. Each yes, no or silence is recorded for slice 3. The model and the
output shape are then chosen by comparing candidates on goldens and on a private replay set.

**Architecture:**
- Reflex's model call becomes a Pydantic AI agent (component 12 of the adoption design). Its text reply
  is still parsed by `parse_decision`, its prose moves to the prompt store, and Ollama goes.
- After the model answers, Reflex reduces each proposal to one **outcome**: `none`, `invalid`,
  `would_reverse`, `shadow`, `asked` or `held`. The outcome reads two things: a per-entity record of the
  last change a person made (`core/reflex/reversal.py`), and the owner's settings, a JSON document in
  Redis (`core/reflex/settings.py`).
- An ask is a `ParkedAction` from Reflex in the existing pending store, with a 10-minute TTL. It is
  announced by an IMPORTANT notification that the PWA's Door already understands. It is answered by the
  Door, or by a reply that the approval classifier reads. Its answer goes to `alfred:reflex:answers`.
- Evals gain a check that reads Reflex's outcome from the decision log. Later they gain a per-role model
  setting, so candidates can be compared.

**Tech Stack:** Python 3.13, Pydantic v2, pydantic-ai-slim 2.55.0 (`[openai,openrouter]`), redis-py
asyncio (fakeredis in tests), pytest-asyncio, the evals harness (`evals/harness/`), and
alfred-home-service.

**Spec:** `docs/superpowers/specs/2026-10-24-reflex-anticipation-design.md`:
- "Slice 2" (lines 188–297), "Constraints" (106–121) and "Measuring" (373–386).
- For the Pydantic AI move, component 12 of `docs/superpowers/specs/2026-10-08-pydantic-ai-adoption-design.md`
  (lines 606–622). Where the two differ, the anticipation spec wins: the output shape is chosen by evals
  and is not fixed in advance.

**Phases, branches and PRs.** Each PR starts from `origin/master` (or `origin/main`) after the PR before
it has merged. Never stack PRs.

| Phase | Tasks | PR | Branch | Starts |
|---|---|---|---|---|
| A: Reflex on Pydantic AI | 1–4 | A, alfred | `feat/reflex-pydantic-ai` (this worktree; the plan rides in it) | now |
| B: never reverse a person | 5–8 | B, alfred | `feat/reflex-never-reverse` | after A merges |
| C: asks | 9–14 | C, alfred | `feat/reflex-asks` | after B merges |
| D: scripts | 15 | D, home-service | `feat/reflex-scripts` | after C merges |
| D: scripts | 16 | E, alfred | `feat/reflex-script-goldens` | after D merges |
| E: choosing the model | 17–20 | F, alfred | `feat/reflex-model-choice` | not before **2026-11-09** |

The 2026-11-09 date gives two weeks of decision log since slice 1 deployed (2026-10-26 23:35Z). After
Phase E come the owner's switch to `ask` and a week in production (Task 21).

Worktrees go in `~/code/.worktrees/alfred/<slug>/` and `~/code/.worktrees/home-service/reflex-scripts/`.
In a new alfred worktree, run `uv venv --python 3.13 && uv sync --all-extras` first.

## Rulings made while planning (for the owner to check)

1. **Six PRs, not one.** Each PR ships and is measured on its own. PR A is component 12 proper.
2. **Signal is not an ask channel in this slice.**
   - Why: Signal inbound is not wired. `SignalBridge.forward_inbound` has no production caller, and the
     runner never starts the bridge's inbound side. Production also sets no Signal number.
   - Where asks go instead: the notification dispatcher, like any IMPORTANT notification. That means the
     websocket to an open PWA, where the Door answers them, and APNs. The PWA's Web Push, which another
     session is building, will reach them with the PWA closed, with no change here.
   - A reply answers an ask on any channel that an IMPORTANT notification reaches: `web_pwa`, `ios` and
     `signal`. So Signal works the day its inbound lands.
   - Cost if wrong: one more task later, a Signal inbound receiver.
3. **Mode is `shadow` or `ask`.** Slice 3 adds `act`, because slice 3 defines acting.
4. **Settings are one JSON document** at `alfred:reflex:settings`, read on every decision. They are edited
   through `GET`/`PUT /api/admin/reflex/settings` and `python -m core.reflex.settings`. A missing or
   unreadable document means the defaults, with mode `shadow`.
5. **What counts as reversing.**
   - Two conditions: the entity's latest change was a person's (`person`, `outside`, or no context)
     within the window; and the proposal would leave the entity in a state other than the one that
     person set.
   - When the person changed only attributes, setting its brightness or volume also counts.
   - Changes by Alfred or an automation clear the record.
   - An unknown service aimed at such an entity counts as a reversal (fail closed).
6. **Expired asks are settled lazily.**
   - Settlement happens on the next Reflex decision, the next reply, the next Door decision and the next
     report.
   - The ask is recorded with its deadline as its time.
   - There is no timer and no sweeper: the repo rule is no polling.
7. **Shadow mode still reports some limits.** It reports the limits that do not depend on asks having been
   sent (tool off, quiet hours, DND) as the reason on a `shadow` outcome. That way the switch to `ask`
   can be judged from shadow data.
8. **Answers are kept by count, not age:** maxlen 5,000, about two years at the cap. Slice 3's silences run
   90 days.
9. **The reply acknowledgement is fixed copy**, not a System 2 run. Yes: `Okay.` No: `Okay, I'll leave it.`
10. **In slice 2 the router admits only confirmed elevated Reflex actions.** The allowed-script list
    changes nothing at the router until slice 3, because nothing acts alone before then. In slice 2 the
    list is recorded and editable.
11. **Scripts enter the House block only in PR E**, once home-service gives Reflex a script tool.

## Global Constraints

- **Code:** Python 3.13+, Pydantic v2, `mypy --strict`, ruff with line length 100. All I/O is async.
- **Latency:** event to decision stays under 500 ms. Nothing in the decision path calls System 2. Parking
  an ask and notifying happen after the decision is made.
- **Shadow by default:** mode ships as `shadow`. Nothing Reflex proposes executes until a person says yes
  to an ask.
- **Never reverse a person:**
  - window 60 minutes (setting `reverse_window_minutes`);
  - the causes `person` and `outside`, and a change with no context, count as a person's;
  - the outcome is `would_reverse`, listing the entities it would have reversed.
- **Asks:**
  - expiry 600 s (`ask_expiry_seconds`); critical confirmations keep 300 s;
  - urgency IMPORTANT;
  - one open ask at a time;
  - the same tool and target are not asked about again within 2 hours (`repeat_hours: 2`);
  - at most 6 a day, counted by the owner's local date (`daily_ask_cap: 6`);
  - none during quiet hours, `23:00`–`07:00` in the owner's time zone by default (`quiet_hours_start`,
    `quiet_hours_end`);
  - none while DND is active;
  - an ask goes out only for a tool that matches one of the `ask_tools` patterns (default empty).
- **Exact words:**
  - outcome kinds: `none`, `invalid`, `would_reverse`, `shadow`, `asked`, `held`;
  - held reasons: `tool_off`, `quiet_hours`, `dnd`, `ask_open`, `asked_recently`, `daily_cap`;
  - answers: `yes`, `no`, `expired`.
- **Who may answer:** only a "sir" speaker answers an ask by reply, and a guest never does. A Door decision
  is the signed-in owner's.
- **Elevated tools:** of the non-benign tools, only scripts may reach Reflex, and only as asks. Critical
  tools never reach it.
- **Keys and streams:**
  - `alfred:reflex:settings` and `alfred:reflex:person_changes`;
  - `alfred:reflex:decided:<event_id>`;
  - `alfred:reflex:ask:open`, `alfred:reflex:asked:<tool>:<targets>` and `alfred:reflex:asks:<local date>`;
  - `alfred:reflex:answered:<ask_id>` and `alfred:reflex:answers`.

  Each is a constant in `shared/streams.py`.
- **Merges and posts:** every merge deploys and needs the owner's go. Ask the owner before pushing,
  opening a PR, posting publicly, or editing issues.
- **The repo is public:**
  - no person, household or device names, hostnames, IPs, domains or keys in repo files, issues or PR
    text;
  - goldens use the eval world's generic names;
  - the shadow log, the decision log, replay records and labels stay on the box, outside any repo;
  - before every commit and push, run the two greps in the deploy directory's PWA exposure runbook,
    section "Secret hygiene". Both must print nothing. Never quote those greps in a committed file;
  - no model identifiers in commits, PRs, code comments or docs.
- **Repo rules:**
  - no hardcoded tool lists: tool patterns and the allowed scripts are settings, and the code names no
    tool;
  - no polling;
  - `create_redis()` for clients;
  - `alfred:events` XADDs pass maxlen;
  - new features documented in `docs/<feature>.md` (this slice's is `docs/reflex-anticipation.md`);
  - `docs/PRD.md` kept current;
  - `alfred:triggers` only through `TriggerStore`.
- **The local vLLM is shared with production.**
  - Replays and eval runs send one request at a time. State the bound before each run.
  - Swapping the served model needs the owner's go.
  - Hosted models need the owner's agreed cost first. Only generic goldens go to them, never replay
    records.
- **Gates:**
  - **alfred**, once per task before its commit:
    ```
    ruff check . && ruff format --check . && mypy --strict alfred_cli/ alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/ && .venv/bin/python -m pytest -x -q
    ```
    During iteration, run only the task's test files.
  - **web**, Task 3 only: `cd web && npm run lint && npm run test && npm run build`.
  - **home-service:**
    ```
    uv run ruff check . && uv run ruff format --check . && uv run mypy app/ alfred_ext/ && uv run pytest -q
    ```
- **Evals:** `ALFRED_EVALS_HOME_SERVICE=~/code/.worktrees/home-service/evals-main uv run alfred evals run reflex`.

## Review Focus

1. **A reply while an ask is open goes to the right place.** If a System 2 run is parked in another
   conversation, the reply answers the ask. If the run is parked in this conversation, the run wins.
   Task 12.
2. **A late Door decision runs nothing.** When the decision arrives after the ask expired, the answer is
   recorded once, as `expired`. Task 12.
3. **A redelivered state change never asks twice.** This covers a crash between parking the ask and
   ACKing the entry. Tasks 4 and 11.
4. **A room-wide proposal is still a reversal.** If a person just turned off one lamp in a room, a
   proposal that names the whole room reverses that lamp, even though the room's other lamps were not
   touched. Task 5.
5. **A settings document the owner broke reads as the defaults, mode `shadow`.** That includes bad JSON,
   an unknown mode and wrong types. It logs one warning, and the PUT endpoint refuses it with 422.
   Tasks 5 and 9.

---

## Phase A — Reflex on Pydantic AI (PR A)

### Task 1: Reflex's prose comes from the prompt store

**Files:**
- Create: `shared/prompts/defaults/reflex-state-change.md`, `shared/prompts/defaults/reflex-trigger.md`,
  `shared/prompts/defaults/reflex-rules.md`, `core/reflex/prose.py`,
  `tests/core/reflex/fixtures/prompt_state_change.txt`, `tests/core/reflex/fixtures/prompt_trigger.txt`
- Modify:
  - `shared/prompts/names.py`;
  - `core/reflex/prompt.py`: remove `STATE_CHANGE_INTRO`, `TRIGGER_INTRO` and `DECISION_RULES`;
  - `core/reflex/engine.py`;
  - `core/reflex/__main__.py`: build `PromptStore.from_config(config, redis=r)`;
  - `docs/llm.md`: the prompt-store table.
- Test: `tests/core/reflex/test_prompt_store_parity.py`, plus the existing `tests/core/reflex/test_prompt_*.py`

**Interfaces:**
- Produces:
  - in `shared/prompts/names.py`: `REFLEX_STATE_CHANGE = "reflex-state-change"`,
    `REFLEX_TRIGGER = "reflex-trigger"` and `REFLEX_RULES = "reflex-rules"`, each with `frozenset()`
    variables in `PROMPTS`;
  - in `core/reflex/prompt.py`: `@dataclass(frozen=True) class ReflexProse: intro: str; rules: str`.
    `build_state_change_prompt(...)` and `build_trigger_prompt(...)` gain a required keyword
    `prose: ReflexProse` and keep every other parameter;
  - in `core/reflex/prose.py`:
    `async def fetch_prose(store: PromptStore, origin: Literal["state_change", "trigger_fired"]) -> ReflexProse`;
  - `ReflexEngine.__init__` gains the keyword `prompts: PromptStore | None = None`. None means the repo
    defaults (`repo_text`), so existing tests construct engines unchanged.

- [ ] **Step 1: Capture today's prompts.** Before changing any code, render one state-change prompt and
  one trigger prompt with today's builders, from the inputs `tests/core/reflex/test_prompt_assembly.py`
  already uses. Write them byte for byte to the two fixture files.
- [ ] **Step 2: Write the failing tests** in `test_prompt_store_parity.py`:
  - `test_repo_prose_renders_todays_prompts`: builds both prompts from the repo defaults with the same
    inputs and asserts each equals its fixture file exactly;
  - `test_reflex_prompts_take_no_variables`: asserts `PROMPTS[name] == frozenset()` for the three names.
- [ ] **Step 3: Run them.** `.venv/bin/python -m pytest tests/core/reflex/test_prompt_store_parity.py -v`
  must FAIL on the missing names.
- [ ] **Step 4: Move the prose.**
  - Copy the three constants' text verbatim into the `.md` files, and check how `repo_text` treats a
    trailing newline.
  - The engine fetches the prose on every decision. The store serves from its 60 s cache, so the hot
    path makes no network call.
- [ ] **Step 5: Run the tests.** `tests/core/reflex/` must PASS.
- [ ] **Step 6: Gate and commit:** `refactor(reflex): Reflex's prose comes from the prompt store`.

### Task 2: The Reflex agent

**Files:**
- Create: `core/reflex/agent.py`
- Modify:
  - `shared/config.py`: `reflex_model`, env `REFLEX_MODEL`. When unset it is `"vllm:" + OPENAI_COMPAT_MODEL`;
    when both are blank it is `""`;
  - `core/reflex/engine.py`: call the agent instead of `inference.infer`;
  - `core/reflex/__main__.py`: `apply_model_env(config)`, then `resolve_model("REFLEX_MODEL", config.reflex_model)`.
    When that gives None, log ERROR and exit non-zero;
  - `.env.example`.
- Test: `tests/core/reflex/test_agent.py`, `tests/shared/test_reflex_config.py`, and the patch points in
  `tests/core/reflex/test_engine_decisions.py` and `tests/core/reflex/test_audience_filter.py`

**Interfaces:**
- Produces `def build_reflex_agent(model: Model) -> Agent[list[ToolInfo], ReflexProposal]` in
  `core/reflex/agent.py`:
  - no instructions;
  - `output_type=TextOutput(_parse)`, where `_parse(ctx: RunContext[list[ToolInfo]], text: str)` returns
    `parse_decision(text, ctx.deps)`;
  - model settings `temperature=0.0`, `max_tokens=150` and `extra_headers=role_headers(SYSTEM1)`;
  - a JSON-object response format;
  - the `instrumentation()` capability and `defer_model_check=True`, as in `core/conscious/agent.py`.
- Produces `async def decide(agent: Agent[list[ToolInfo], ReflexProposal], prompt: str, tools: list[ToolInfo]) -> ReflexProposal`:
  - one run, with the whole prompt as the single user message;
  - `@track_latency` and `@track_tokens` are fed from `result.usage()`;
  - transport errors propagate as today, so the runner leaves the entry pending and reclaim replays it.
- `ReflexEngine` takes `agent=` in place of the module-level inference. `process_event` and
  `process_trigger_fired` keep their signatures.

- [ ] **Step 1: Write the failing tests** in `test_agent.py`:
  - `test_the_reply_text_is_parsed`: a `FunctionModel` returns
    `'{"decision": "ask", "tool": "home.light_turn_on", "parameters": {"target": "Lamp"}, "reason": "dark"}'`.
    Expect `decision == "ask"` and `action.source == "reflex-engine"`.
  - `test_an_unparseable_reply_is_invalid_and_not_retried`: the model returns `"hello"`. Expect
    `decision == "invalid"`, `problem == "not JSON"`, and exactly one model call.
  - `test_the_request_matches_todays_wire_form`: an `httpx.MockTransport` behind an OpenAI-compatible
    provider captures the request. Expect a body with `temperature == 0`, `max_tokens == 150`,
    `response_format == {"type": "json_object"}` and `messages == [{"role": "user", "content": prompt}]`,
    and the header `X-Alfred-Role: system1`.
  - `test_a_keyed_provider_sends_its_key`: the same capture with a provider given `api_key="k"` carries
    `Authorization: Bearer k`.
- [ ] **Step 2: Write the failing config tests** in `test_reflex_config.py`:
  - unset `REFLEX_MODEL` with `OPENAI_COMPAT_MODEL=m` gives `"vllm:m"`;
  - an explicit value is kept;
  - both blank give `""`.
- [ ] **Step 3: Run both test files.** They must FAIL: there is no module and no field yet.
- [ ] **Step 4: Implement the agent.** If pydantic-ai has no setting for the response format, send it
  through `extra_body`. The wire test is the proof.
- [ ] **Step 5: Run the tests.** `tests/core/reflex/` and `tests/shared/test_reflex_config.py` must PASS.
- [ ] **Step 6: Gate and commit:** `feat(reflex): Reflex decides through a Pydantic AI agent`.

### Task 3: Ollama leaves

**Files:**
- Delete: `core/reflex/inference.py`, `core/reflex/ollama_client.py`, `core/reflex/openai_client.py`,
  `core/reflex/tests/test_ollama_warmup.py`, `tests/core/reflex/test_inference_backends.py`.
- Modify:
  - `shared/config.py`: drop `ollama_host`, `ollama_model` and `reflex_backend`. Keep
    `openai_compat_host`, now only the `VLLM_BASE_URL` fallback, and `openai_compat_model`, now only the
    `REFLEX_MODEL` default.
  - `shared/gateway.py`: drop `OLLAMA_HOST`.
  - `core/channels/admin_api.py`: no Ollama probe; `reflex.model` is the `REFLEX_MODEL` value; the
    overview's `inference` key goes.
  - `web/src/lib/types.ts` and `web/src/test/fixtures.ts`: drop `inference`. Nothing renders it.
  - `alfredctl/doctor.py` (`_check_reflex`): checks that `REFLEX_MODEL` resolves. With `--online`, it
    checks that the vLLM base URL lists the model. It warns when `REFLEX_BACKEND` or any `OLLAMA_*` is
    still set: "no longer read".
  - `conftest.py`: drop `_default_reflex_backend`.
  - `scripts/smoke-test.sh`.
  - `evals/harness/stack.py`: `REFLEX_MODEL=vllm:<model>` instead of `REFLEX_BACKEND` and
    `OPENAI_COMPAT_*`.
  - Tests:
    - `tests/integration/test_reflex_end_to_end.py`: patch the agent instead;
    - `tests/core/channels/test_admin_api.py`;
    - `tests/alfredctl/test_doctor.py` and `tests/alfredctl/test_launch.py`;
    - `tests/shared/test_agents.py`;
    - `tests/evals/harness/test_stack.py`.
  - `.env.example`.
  - Docs, where they describe Reflex's inference:
    - `CLAUDE.md`: the Tech Stack line and the `REFLEX_BACKEND` gotcha. The gotcha becomes the agent;
    - `core/CLAUDE.md` and `.claude/rules/core/reflex-engine.md`;
    - `README.md`;
    - `docs/llm.md`: drop "until PR 8";
    - `docs/architecture.md`, `docs/admin-api.md`, `docs/deployment.md`, `docs/containerization.md`;
    - `docs/event-bus.md`, `docs/trigger-engine.md`, and the qa-backlog notes.

- [ ] **Step 1: Write the failing tests:**
  - `test_doctor_warns_about_leftover_ollama_settings`;
  - `test_overview_reports_the_reflex_model_and_no_inference_probe`;
  - `test_stack_sets_reflex_model` asserts `env["REFLEX_MODEL"] == "vllm:test-model"` and
    `"REFLEX_BACKEND" not in env`, with that model name as a test value.
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Delete and edit as listed.**
- [ ] **Step 4: Check for leftovers.** `git grep -n -i -e ollama -e REFLEX_BACKEND -- ':!docs/superpowers' ':!research'`
  must print nothing. If `docs/PRD.md` names Ollama, it changes in this task too.
- [ ] **Step 5: Gate, then the web gate, then commit:** `refactor(reflex)!: drop Ollama; Reflex needs an OpenAI-compatible server`.
  - The PR body's operator note: production keeps working with no `.env` change, because `REFLEX_MODEL`
    defaults from `OPENAI_COMPAT_MODEL`. Leftover `REFLEX_BACKEND` and `OLLAMA_*` lines are ignored, and
    the doctor names them.

### Task 4: The decision log reads by time, an event is decided once, and a replay

**Files:**
- Modify:
  - `core/reflex/decision_log.py`;
  - `core/reflex/runner.py`;
  - `core/reflex/__main__.py` (`_handle_trigger_fired`);
  - `core/reflex/prompt.py`: `parse_tools`, beside the other round-trip readers;
  - `shared/streams.py`: `REFLEX_DECIDED_PREFIX = "alfred:reflex:decided:"`;
  - `docs/attribution.md`: the decision-log read.
- Create: `core/reflex/replay.py` (run as `python -m core.reflex.replay`), and `docs/reflex-anticipation.md`
  with an overview and a "Replay" section. Later phases add their sections to the same file.
- Test: `tests/core/reflex/test_decision_log.py`, `tests/core/reflex/test_redelivery.py`,
  `tests/core/reflex/test_replay.py`

**Interfaces:**
- `DecisionLog.read(*, since: datetime, until: datetime | None = None) -> list[DecisionRecord]`:
  - raises `ValueError` for a naive `since` or `until`;
  - XRANGE from `f"{ms(since)}-0"` to `f"{ms(until)}-+"`, or `+` when `until` is None. Each record is
    still filtered on its own `time`, because a record's time and its id can differ by the XADD's
    latency.
- In `core/reflex/runner.py`: `async def already_decided(redis: AioRedis, event_id: str) -> bool` and
  `async def mark_decided(redis: AioRedis, event_id: str) -> None`. The marker is
  `REFLEX_DECIDED_PREFIX + event_id`, set with `SET NX EX REPLAY_MARKER_TTL_SECONDS`.
- `process_stream_entry` checks `already_decided` after the attention gate and before the engine runs. On
  a hit it logs INFO and returns False, and the entry is ACKed. It marks the event after the decision is
  counted and published, so a crash before that replays the decision. `_handle_trigger_fired` does the
  same around `process_trigger_fired`.
- `def parse_tools(prompt: str) -> list[ToolInfo]` reads the `Tools:` lines that `render_tools` writes:
  name, parameter names and target service.
- The replay CLI:
  ```
  python -m core.reflex.replay run --since ISO --until ISO --records FILE --limit N --model MODEL --out FILE
  ```
  - `--until`, `--records`, `--limit` and `--out` are optional. Records come from the live log, or from
    a JSONL file of `DecisionRecord` dumps.
  - Each record's prompt runs through `decide()` with `parse_tools(record.prompt)`, one at a time.
  - It prints counts only: a table of recorded × replayed decision, the agreement, the invalid rate, and
    model latency p50 and p95.
  - `--out` writes one JSONL line per record. That file is private.

- [ ] **Step 1: Write the failing tests:**
  - `test_read_refuses_a_naive_since`;
  - `test_read_starts_at_since` (entries added with explicit ids before and after `since`);
  - `test_a_redelivered_event_is_decided_once` (the same `event_id` twice gives one model call);
  - `test_a_crash_before_marking_decides_again`;
  - `test_trigger_fired_is_decided_once`;
  - `test_parse_tools_reads_render_tools` (round trip);
  - `test_replay_counts`: three records and a `FunctionModel` give the recorded × replayed table, and
    nothing in the output names an entity.
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** They must PASS.
- [ ] **Step 5: Gate and commit:** `feat(reflex): decide each event once; replay recorded decisions`.
- [ ] **Step 6: The merge gate for PR A, which needs the owner's go to run against production data.**
  - Dump the production decision log since `2026-10-26T23:35Z` with the code production already runs.
    Use `docker exec alfred python -c` around `DecisionLog.read`, writing JSONL to
    `~/code/alfred-deploy/reflex-replay/records-<date>.jsonl` with mode 0600.
  - From this worktree, run `uv run python -m core.reflex.replay run --records <file> --limit 500 --model vllm:<served model>`,
    with `VLLM_BASE_URL` set to the local server.
  - The bound is at most 500 requests, one at a time.
  - The replayed counts must sit within run-to-run noise of the recorded ones. vLLM is not deterministic
    at temperature 0, so compare counts, never single records.
  - Post the counts only to #317, with the owner's OK. The owner decides the merge.

**PR A:**
- Title: `feat(reflex): Reflex on Pydantic AI, Ollama removed`.
- Body per `.github/pull_request_template.md`: `Refs #317`, the operator note from Task 3, and a
  post-deploy step: `alfred prompts push` for the three Reflex prompts, at the owner's go.

## Phase B — Never reverse a person, the weather and the sun (PR B)

### Task 5: Settings, the person-change record and the reversal rule

**Files:**
- Create: `core/reflex/settings.py`, `core/reflex/reversal.py`
- Modify: `shared/streams.py`: `REFLEX_SETTINGS_KEY = "alfred:reflex:settings"` and
  `REFLEX_PERSON_CHANGES_KEY = "alfred:reflex:person_changes"`
- Test: `tests/core/reflex/test_settings.py`, `tests/core/reflex/test_reversal.py`

**Interfaces:**
- Settings:
  - `class ReflexSettings(BaseModel)`, with `extra="forbid"`. This phase's only field is
    `reverse_window_minutes: int = Field(60, ge=1, le=1440)`. Task 9 adds the rest.
  - `async def load_settings(redis: AioRedis) -> ReflexSettings`. A missing document gives the defaults.
    An unreadable or invalid one gives the defaults and one WARNING per distinct bad value, remembered
    in the module, so each decision does not log again.
  - `async def save_settings(redis: AioRedis, settings: ReflexSettings) -> None`.
- `class PersonChange(BaseModel)`: `time: datetime`, `cause: Cause | None`, `old_state: str | None`,
  `new_state: str`, `context_id: str | None`, `attributes_only: bool`. The last is True when
  `old_state == new_state`.
- `class PersonChanges`, built with `(redis: AioRedis, *, clock: Callable[[], datetime])`:
  - `async def note(self, event: StateChangedEvent, cause: Cause | None) -> None`:
    - a cause of `person`, `outside` or None writes the entity's field in the hash;
    - `alfred` or `automation` deletes the field;
    - an attributes-only change whose context id matches the stored record's, within the window, leaves
      the record as it is. It is part of the same action, such as a bulb reporting its brightness after
      being turned on;
    - best effort: it logs a warning and never raises.
  - `async def recent(self, window: timedelta) -> dict[str, PersonChange]` reads the hash and deletes
    the fields older than the window.
- Pure functions in `core/reflex/reversal.py`:
  - `def resolve_targets(tool_name: str, target: str, snapshot: Mapping[str, LiveEntity]) -> list[str]`:
    - the tool's HA domain is the longest snapshot domain `d` for which the tool's last dotted segment
      starts with `f"{d}_"`. No match gives `[]`;
    - the target is resolved, in order, as an entity id in the snapshot, then a friendly name (casefold)
      among that domain's entities, then an area name (casefold), which gives that domain's entities in
      that area;
    - nothing matched gives `[]`.
  - `def reversed_entities(action: ActionRequest, changes: Mapping[str, PersonChange], snapshot: Mapping[str, LiveEntity]) -> list[str]`:
    - the service is the tool's segment after `f"{domain}_"`;
    - `SERVICE_STATES: Final = {"turn_on": "on", "turn_off": "off", "media_play": "playing", "media_pause": "paused"}`;
    - an action sets attributes when its service is `volume_set` or its parameters carry `brightness_pct`;
    - for each resolved target that has a change record, it is reversed when:
      - the record is attributes-only, and the action sets attributes or leads to a state other than
        `new_state`; or
      - the record is a state change, and the action's state is known and differs from `new_state`; or
      - the record is a state change, and the service is neither in `SERVICE_STATES` nor an attribute
        setter (fail closed).
    - The result is sorted and unique.

- [ ] **Step 1: Write the failing tests in `test_reversal.py`.** Each case has its own test; ids are the
  apartment world's.

  | A person's change | The proposal | Reversed |
  |---|---|---|
  | turned `light.living_room_lamp` off 10 min ago | `home.light_turn_on`, target the lamp | `[lamp]` |
  | the same, 61 min ago | the same | `[]` |
  | turned the lamp on | `home.light_turn_on` with `brightness_pct: 30` | `[]` |
  | changed only the brightness (on → on) | `brightness_pct: 80` | `[lamp]` |
  | started the TV playing | `home.media_player_media_pause` | `[tv]` |
  | paused the TV | `home.media_player_media_play` | `[tv]` |
  | cause `outside`, or no context | `home.light_turn_on`, target the lamp | `[lamp]` |
  | turned the lamp on, then an automation turned it off (note sees both) | `home.light_turn_off` | `[]` |
  | turned the lamp off; another lamp in the room is untouched | target `"Living room"` | `[lamp]` only |
  | turned the lamp off | target the lamp's friendly name | `[lamp]` |
  | turned the lamp off | an unknown service (`home.light_toggle`) | `[lamp]` |
  | turned the lamp on; then an attributes-only change shares the turn-on's context id | dim it | `[]` |
- [ ] **Step 2: Write the failing tests in `test_settings.py`.** Each of these reads as the defaults:
  - a missing document;
  - `"{"` (bad JSON);
  - `{"reverse_window_minutes": 0}`;
  - `{"reverse_window_minutes": "x"}`;
  - `{"surprise": 1}`.

  Also: a bad value logs once over two reads, and saving then loading round-trips.
- [ ] **Step 3: Run both files.** They must FAIL.
- [ ] **Step 4: Implement.**
- [ ] **Step 5: Run them.** They must PASS.
- [ ] **Step 6: Gate and commit:** `feat(reflex): know what a person just changed`.

### Task 6: Every decision ends in one outcome

**Files:**
- Create: `core/reflex/outcome.py`
- Modify:
  - `bus/schemas/events.py`: the outcome model, beside `ReflexProposal`, because `ReflexObservation`
    carries it and bus never imports core;
  - `core/reflex/engine.py`: it returns a `Decided` and no longer writes the decision log;
  - `core/reflex/runner.py`: `note` after `record_change`; it records, counts and publishes;
  - `core/reflex/__main__.py`: the trigger path does the same; build `PersonChanges` and pass `redis=`;
  - `core/reflex/decision_log.py`;
  - `core/reflex/prompt.py`: the "Leave alone" line;
  - `core/reflex/shadow_report.py`: an "Outcomes" section;
  - `docs/reflex-anticipation.md`: "Never reverse a person";
  - `docs/attribution.md`: the record's new fields.
- Test: `tests/core/reflex/test_outcome.py`, `tests/core/reflex/test_engine_decisions.py`,
  `tests/core/reflex/test_prompt_sections.py`, `tests/core/reflex/test_shadow_report.py`

**Interfaces:**
- In `bus/schemas/events.py`:
  - `OutcomeKind = Literal["none", "invalid", "would_reverse", "shadow", "asked", "held"]`;
  - `HeldReason = Literal["tool_off", "quiet_hours", "dnd", "ask_open", "asked_recently", "daily_cap"]`;
  - `class ReflexOutcome(BaseModel)`: `kind: OutcomeKind`, `reason: HeldReason | None = None`,
    `reversed: list[str] = []`, `ask_id: str | None = None`;
  - `ReflexObservation` gains `outcome: ReflexOutcome | None = None`. Observations written before this
    change still parse.
- In `core/reflex/outcome.py`:
  - `def first_outcome(proposal: ReflexProposal, reversed: Sequence[str]) -> ReflexOutcome`:
    - `none` gives `none`, and `invalid` gives `invalid`;
    - `act` or `ask` with a non-empty `reversed` gives `would_reverse`;
    - otherwise it gives `shadow`. Task 10 refines `shadow`.
- `@dataclass(frozen=True) class Decided`: `proposal: ReflexProposal`, `outcome: ReflexOutcome`,
  `prompt: str`, `recent: dict[str, PersonChange]`. `ReflexEngine.process_event` and
  `process_trigger_fired` return it. The engine gains `redis: AioRedis | None = None` and
  `person_changes: PersonChanges | None = None`; with None, there are no changes and the defaults apply.
- `DecisionRecord` gains `outcome: ReflexOutcome | None = None` and `recent: dict[str, PersonChange] = {}`.
  Records written before this change still parse.
- The runner, for each origin, after the engine:
  - `DecisionLog.record(origin, event, decided)`: the signature changes to take the `Decided`;
  - `count_decision(redis, proposal.decision)` as today;
  - `count_outcome(redis, outcome)`: `HINCRBY` on the same daily hash, field `f"outcome:{kind}"`, plus
    `f"{kind}:{reason}"` when a reason is set;
  - `publish_proposal(..., outcome=outcome)` only when the outcome is `shadow`, `asked` or `held`, or
    the decision is `invalid`. `publish_proposal` gains the keyword `outcome: ReflexOutcome | None = None`
    and puts it on the observation, so the shadow log says "would have asked" for a `held`. A
    `would_reverse` is observed passively like a `none`, as the spec says it becomes "none";
  - `mark_decided`.
- `render_now(now: datetime, tz_name: str, entities: Mapping[str, LiveEntity] | None, leave_alone: Sequence[str] = ()) -> str`.
  When `leave_alone` is non-empty, it appends a line, with each entity named as the House block names it, sorted:
  `Leave alone, a person just changed: Lamp (Living room), TV (Living room)`

- [ ] **Step 1: Write the failing tests:**
  - `test_first_outcome` covers the four cases;
  - `test_a_reversing_proposal_becomes_would_reverse`: a person turned the lamp off, and the model
    proposes turning it on. The outcome is `would_reverse` with `["light.living_room_lamp"]`. The record
    carries the outcome and `recent`. No proposal is published. `outcome:would_reverse` is 1;
  - `test_trigger_fired_proposals_are_checked_too`;
  - `test_the_prompt_lists_what_to_leave_alone`, and it is absent with no changes;
  - `test_old_records_still_read` (a record without `outcome`);
  - `test_a_published_proposal_carries_its_outcome` (kind `shadow`), and an old observation without
    `outcome` still parses;
  - `test_shadow_report_prints_outcomes`.
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Implement.** The prompt-store parity test from Task 1 must still pass: no changes means no
  line.
- [ ] **Step 4: Run them.** `tests/core/reflex/` must PASS.
- [ ] **Step 5: Gate and commit:** `feat(reflex): never propose reversing a person's change`.

### Task 7: Reflex sees the weather and the sun

**Files:**
- Modify:
  - `core/reflex/attention_seed.yaml`: `domains` gains `sun` and `weather`;
  - `core/reflex/prompt.py` (`render_now`);
  - `evals/harness/worlds/apartment.yaml`: `weather.home`, state `sunny`, friendly name `Home`;
  - every reflex golden that sets `settle: 1` with the comment that Reflex does not attend to the sun:
    drop the `settle` so the driver waits for the sun's call;
  - `docs/architecture.md` (the attention seed) and `docs/reflex-anticipation.md`.
- Test: `tests/core/reflex/test_prompt_sections.py`, `tests/core/reflex/test_attention.py`,
  `tests/evals/test_goldens_load.py`

**Interfaces:** `render_now` appends ` · {condition}` after the sun:
- the condition comes from the first `weather.*` entity in the snapshot, by entity id;
- it is the entity's state with `-` replaced by a space;
- it is omitted when the state is `unavailable` or `unknown`;
- example: `Thu 8 Oct, 15:10 (afternoon) · sun up · partlycloudy`.

- [ ] **Step 1: Write the failing tests:**
  - `test_now_carries_the_weather`: `partlycloudy` stays as it is; `clear-night` becomes `clear night`;
    `unavailable` is omitted; and with no weather entity the line is unchanged;
  - `test_a_new_sun_and_weather_entity_are_attended`: neither entity was seen before, and both pass via
    the seed;
  - the round trip in `test_goldens_load.py` covers `weather.home`.
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Implement, and edit the goldens.**
- [ ] **Step 4: Run** `tests/core/reflex/` and `tests/evals/`. They must PASS.
- [ ] **Step 5: Gate and commit:** `feat(reflex): watch the sun and the weather`.
- [ ] **Step 6: Write the operator step into PR B's body, to run after the deploy with the owner's go.**
  Production saw `sun.sun` and its weather entities before the seed changed, so the seed never adds
  them. Allow each one with `PUT /api/admin/attention/home`; the weather ids come from the live state.
  Check with `GET /api/admin/attention`.

### Task 8: Evals see Reflex's outcome; the goldens

**Files:**
- Modify:
  - `evals/harness/bus.py`: `async def decision_records(self, since: datetime) -> list[DecisionRecord]`,
    from the stack's Redis;
  - `evals/harness/evidence.py`: the records about each step's change;
  - `evals/harness/checks/reflex.py` and `evals/harness/checks/__init__.py`: the new check
    `reflex_outcome`;
  - `evals/coverage.yaml`: row `{id: 4.4.never-reverse, section: "4.4", prd: "Never reverses a person", suites: [reflex], tests: [tests/core/reflex/test_reversal.py]}`;
  - `docs/PRD.md`: a 4.4 row "Never reverses a person", Shipped, citing PR B and the spec. Bump the
    date line;
  - `docs/evals.md`: the check row and its prose. Fix the stale reflex line in Key paths;
  - `tests/evals/test_goldens_load.py`: counts and the new world entities.
- Modify these goldens:
  - `judgment_arrive_after_dark.yaml`: its light-off steps become `by: automation`;
  - every other shipped golden that changes an entity and then expects an act or ask on it. Step 1
    lists them.
- Create these goldens in `evals/suites/reflex/`:
  - `never_reverse_person_off.yaml`: `person.alex` turns `light.living_room_lamp` off at 20:00 with both
    people home. Expect `reflex_outcome: {target: light.living_room_lamp, outcome: [none, would_reverse]}`.
  - `never_reverse_outside.yaml`: arrive_after_dark with the light-off steps `by: outside`. Expect
    `reflex_outcome: {target: light.living_room_lamp, outcome: [none, would_reverse]}`.
  - `never_reverse_alfred_is_not_a_person.yaml`: a `user` step asks Alfred to turn the lamp off, then
    `person.alex` arrives after dark. Expect `prompt_contains` `by Alfred`, and a system1 prompt for the
    last step that does not contain `Leave alone, a person just changed: Lamp`. Use the negative form of
    `prompt_contains` if one exists. If not, add `absent: true` to `prompt_contains`, with a test.
  - `judgment_movie_any_player.yaml`: a second player, `media_player.bedroom_tv` in the Bedroom, joins
    the world. At 21:00 `person.sam` starts it playing, with the bedroom lamp on (set `by: automation`
    earlier). Expect `reflex_decision: {decision: [act, ask], tool: [home.light_turn_on, home.light_turn_off], target: bedroom}`.
  - `prompt_weather.yaml`: `weather.home` set to `rainy` by `outside`. Expect `prompt_contains`
    `{role: system1, text: "rainy"}`.

**Interfaces:** `reflex_outcome: {outcome: <kind or list>, target: <entity or area id, optional>, at_step: <int, default the last ha_event>}`:
- It judges the decision-log records about that step's change: same entity, same new state, and `time`
  at or after the step's start.
- Without `target`, every such record's outcome must be in the set.
- With `target`, only the records whose proposal resolves to the target are judged, using
  `evals/harness/reflex.targets()`. None of them proposing it is a pass.
- No record at all is an `error`, as `reflex_decision` treats no call.

- [ ] **Step 1: Find the goldens to re-cause.** List every shipped golden whose expectation acts or asks
  on an entity an earlier step changed with the cause `outside` or `person`. Re-cause those steps
  `by: automation`, unless the golden is about a person's change.
- [ ] **Step 2: Write the failing harness tests.** In `tests/evals/harness/test_checks_reflex.py`, cover
  `reflex_outcome` in each direction: a pass, a fail, `target` filtering, the vacuous pass, and the error
  on no record. Add a loader test for the new goldens.
- [ ] **Step 3: Run them.** They must FAIL.
- [ ] **Step 4: Implement the check and the goldens.**
- [ ] **Step 5: Run** `.venv/bin/python -m pytest tests/evals`. It must PASS.
- [ ] **Step 6: Run the reflex suite.** The bound is one suite, sequential. Every shipped golden must pass;
  report any that do not, with its evidence.
- [ ] **Step 7: Gate and commit:** `test(evals): Reflex's outcome is judged; never-reverse goldens`.

**PR B:**
- Title: `feat(reflex): never reverse a person; watch the sun and the weather`.
- Body: `Refs #317`, plus the operator step from Task 7.
- After the deploy: a few days of `shadow_report` outcome counts (`would_reverse` against the baseline
  ~45%). Post counts only, with the owner's OK.

## Phase C — Asks (PR C)

### Task 9: The owner's Reflex settings

**Files:** `core/reflex/settings.py` (every field, plus a CLI `python -m core.reflex.settings`),
`core/channels/admin_api.py`, `docs/admin-api.md`, `docs/reflex-anticipation.md` (a settings table)
- Test: `tests/core/reflex/test_settings.py`, `tests/core/channels/test_admin_reflex_settings.py`

**Interfaces:**
- `ReflexSettings` fields, exactly:

  | Field | Type and default | Bounds |
  |---|---|---|
  | `mode` | `Literal["shadow", "ask"] = "shadow"` | |
  | `ask_tools` | `list[str] = []` | fnmatch patterns over tool names |
  | `allowed_scripts` | `list[str] = []` | entity ids |
  | `reverse_window_minutes` | `int = 60` | 1–1440 |
  | `ask_expiry_seconds` | `int = 600` | 60–3600 |
  | `repeat_hours` | `float = 2.0` | 0–168 |
  | `daily_ask_cap` | `int = 6` | 0–50 |
  | `quiet_hours_start` | `str = "23:00"` | `HH:MM` |
  | `quiet_hours_end` | `str = "07:00"` | `HH:MM`; equal to the start means no quiet hours |

- The admin API is gated by session like the other controls:
  - `GET /api/admin/reflex/settings` returns the document, or the defaults;
  - `PUT /api/admin/reflex/settings` takes a whole document, validates it, and saves it. It answers 422
    and saves nothing when the document is invalid.
- The CLI has `show`, `set <field> <value>` (the value is read as JSON when it parses, else as a string),
  `allow-script <entity_id>` and `disallow-script <entity_id>`. Allow and disallow are idempotent.

- [ ] **Step 1: Write the failing tests:**
  - a GET and PUT round trip;
  - `PUT {"mode": "act"}` gives 422 and the stored document is unchanged;
  - `PUT` without a session gives 401;
  - the CLI `set mode ask`;
  - allow and disallow are idempotent;
  - the extended loader cases (Review Focus 5).
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** They must PASS.
- [ ] **Step 5: Gate and commit:** `feat(reflex): the owner's Reflex settings`.

### Task 10: The ask gate and asking

**Files:**
- Create: `core/reflex/asks.py`, `core/reflex/answers.py`
- Modify: `core/routing/pending.py`: `store_pending_action(redis, action, *, ttl_seconds: int = PENDING_TTL_SECONDS)`;
  `shared/streams.py`: the ask keys
- Test: `tests/core/reflex/test_asks.py`, `tests/core/routing/test_pending.py`

**Interfaces:**
- `class OpenAsk(BaseModel)`:
  - `ask_id: str`, which is the action's `request_id`;
  - `tool_name: str` and `targets: list[str]`;
  - `origin: Literal["state_change", "trigger_fired"]`;
  - `asked_at: datetime` and `deadline: datetime`;
  - `noticed: str`, the event line or the trigger's name;
  - `script: bool`, true when the tool's risk is `elevated`.

  It lives in `core/reflex/answers.py`, so that `pending.py` can import it without a cycle.
- `async def gate(redis: AioRedis, settings: ReflexSettings, outcome: ReflexOutcome, proposal: ReflexProposal, *, targets: Sequence[str], tz_name: str, now: datetime, dnd: DNDChecker) -> ReflexOutcome`:
  - It returns `outcome` unchanged unless the kind is `shadow`.
  - It checks, in this order:
    1. `tool_off`: no `ask_tools` pattern matches the tool;
    2. `quiet_hours`: in the owner's zone. A start later than the end wraps midnight;
    3. `dnd`: `is_active()`;
    4. `ask_open`: after `settle_expired`;
    5. `asked_recently`: the key `alfred:reflex:asked:{tool}:{",".join(sorted(targets))}` exists;
    6. `daily_cap`: the count at `alfred:reflex:asks:<local YYYY-MM-DD>` has reached the cap.
  - A hit returns `held` with that reason. Passing all of them returns `asked`.
  - In `shadow` mode, only checks 1–3 run, and the result is kind `shadow` with that reason, or with
    none.
  - It reads and never writes, except that `settle_expired` may settle an expiry.
- `async def open_ask(redis: AioRedis, notifier: Notifier, proposal: ReflexProposal, *, settings: ReflexSettings, origin: Literal["state_change", "trigger_fired"], noticed: str, targets: Sequence[str], names: Mapping[str, str], script: bool, now: datetime, tz_name: str) -> OpenAsk`:
  - `store_pending_action(redis, action, ttl_seconds=settings.ask_expiry_seconds)`;
  - SET `alfred:reflex:ask:open` with EX of the expiry plus 86,400 s, so a late settlement still finds it;
  - SET the repeat key with EX `repeat_hours * 3600`;
  - INCR the daily count, with EX 2 days;
  - `notifier.publish(title, body, source="reflex-engine", urgency=Urgency.IMPORTANT, metadata={**confirmation_metadata(...), "kind": "reflex_ask"})`.
- `def ask_text(tool_name: str, parameters: Mapping[str, Any], *, noticed: str, reason: str, names: Sequence[str], script: bool) -> tuple[str, str]`:
  - the title is `Shall I {action}?`. The action is plain words: the service and the targets' friendly
    names, for example `turn on Lamp`. For a script it is `run the script {name}`;
  - the body is `{noticed}. {reason} Reply yes or no.`, clipped to 300 characters with the last sentence
    kept.
- `async def settle_expired(redis: AioRedis, *, now: datetime) -> OpenAsk | None`. When the open ask's
  deadline has passed, it calls `record_answer(redis, ask, "expired", at=ask.deadline, channel=None)` and
  returns the ask. `record_answer` is from Task 12; until that task, `settle_expired` deletes the open key
  only.
- `def in_quiet_hours(local: time, start: str, end: str) -> bool`.

- [ ] **Step 1: Write the failing tests:**
  - each held reason on its own;
  - the order: in quiet hours with an ask open, the reason is `quiet_hours`;
  - shadow mode gives only reasons 1–3;
  - at the defaults in `America/New_York`: 22:59 asks, 23:00 holds, 06:59 holds, 07:00 asks;
  - `start == end` never holds;
  - the daily cap counts by local date: a count from the previous local day does not hold;
  - `open_ask` stores the pending action with a TTL of 600, writes the open key, the repeat key and the
    count, and publishes one IMPORTANT notification whose metadata carries `pending_action_id` and
    `kind: reflex_ask`;
  - `ask_text` for a light gives `Shall I turn on Lamp?` and a body ending `Reply yes or no.`;
  - for a script, the title names `run the script Movie night`;
  - `store_pending_action` keeps 300 s by default.
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** They must PASS.
- [ ] **Step 5: Gate and commit:** `feat(reflex): asks and the limits that keep them few`.

### Task 11: Reflex asks, for state changes and fired triggers

**Files:** `core/reflex/runner.py`, `core/reflex/__main__.py`. The Reflex process builds its notifier and
`DNDChecker`, which it already has.
- Test: `tests/core/reflex/test_runner_asks.py`

**Interfaces:**
- After the engine and before the record, the runner, for each origin:
  1. Resolve the proposal's targets and names from the snapshot. Look up the tool's risk with `tool_risk`.
     `script` is true when the risk is `elevated`.
  2. Run `outcome = await gate(...)`.
  3. If `asked`, call `open_ask(...)` and set `outcome.ask_id`.
  4. Then record, count, publish and `mark_decided`, as Task 6 orders them.
- In `ask` mode, a model `act` goes out as an ask.
- A crash after `open_ask` and before `mark_decided` replays the decision. The gate then finds `ask_open`,
  so nothing is asked twice.

- [ ] **Step 1: Write the failing tests:**
  - in ask mode, with an enabled tool in the daytime: one pending action from `reflex-engine` with a TTL
    of about 600; one IMPORTANT notification; the record's outcome is `asked` with the `ask_id`;
  - each held reason is recorded as `held` and counted under `held:<reason>`;
  - shadow mode parks nothing and the outcome is `shadow`;
  - a model `act` in ask mode asks;
  - a fired trigger's proposal asks the same way;
  - the same entry delivered again, after `open_ask` raised just after parking, sends one notification
    in all (Review Focus 3);
  - `would_reverse` is never asked about.
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/core/reflex/`. It must PASS.
- [ ] **Step 5: Gate and commit:** `feat(reflex): ask instead of proposing into the void`.

### Task 12: Answers: the Door, a reply, and silence

**Files:**
- Modify:
  - `core/reflex/answers.py`;
  - `core/routing/pending.py` (`decide`);
  - `core/conscious/engine.py`;
  - `core/conscious/__main__.py` (`_report_outcome`);
  - `core/channels/web_server.py`: the Door routes pass `channel="door"`;
  - `docs/autonomy.md`: Reflex asks.
- Test: `tests/core/reflex/test_answers.py`, `tests/core/routing/test_pending.py`,
  `tests/core/conscious/test_engine_reflex_asks.py`, `tests/core/conscious/test_internal_actions.py`

**Interfaces:**
- `AnswerKind = Literal["yes", "no", "expired"]`.
- `class ReflexAnswer(BaseModel)`: `ask_id`, `answer: AnswerKind`, `time: datetime`, `channel: str | None`,
  `tool_name`, `targets`, `origin`, `noticed`.
- `async def record_answer(redis: AioRedis, ask: OpenAsk, answer: AnswerKind, *, at: datetime, channel: str | None) -> bool`:
  - `SET NX alfred:reflex:answered:<ask_id>` with EX 2 days. When that fails, it returns False:
    the ask was already answered;
  - XADD a `ReflexAnswer` to `alfred:reflex:answers` (maxlen 5,000, approximate);
  - delete `alfred:reflex:ask:open` when it still names this ask;
  - a `yes` with `ask.script` adds the targets to `allowed_scripts` (load, change, save);
  - it returns True.
- `decide(redis, record_id, decision, *, channel: str | None = "door") -> bool`:
  - After it claims a `ParkedAction` whose `action.source == "reflex-engine"`, it reads the open ask with
    that id and records `yes` or `no`.
  - When the claim fails, it calls `settle_expired` and returns False.
  - Deny still republishes nothing.
- `ConsciousEngine._answer_reflex_ask(request: UserRequest, who: str) -> str | None` runs only when
  `_resume_for` returned None, so a parked run in this conversation wins. It needs all of these:
  - `who == "sir"`;
  - `request.channel in ASK_REPLY_CHANNELS`, which is `frozenset({"web_pwa", "ios", "signal"})`: the
    channels an IMPORTANT notification reaches, never speech;
  - a classifier;
  - after `settle_expired`, an open ask whose pending record still reads.

  It classifies with `ParkedCall(tool_call_id=ask_id, tool_name, target_service, parameters, reason)`,
  built from the parked action:
  - `other` gives None, a normal turn;
  - `approve` and `deny` call `decide(..., channel=request.channel)`. If that returns True, the reply is
    `Okay.` or `Okay, I'll leave it.`. If it returns False, None.

  `process_request` returns `self._reply(request, ack, [])` for an ack.
- `_report_outcome` sends no "Done" for a successful action whose `source` starts with `reflex`. A failure
  still sends "That didn't work".

- [ ] **Step 1: Write the failing tests:**
  - A Door confirm on an open ask: `yes` is recorded once, the confirmed action is on `ACTIONS_STREAM`,
    and the open key is gone.
  - A Door deny: `no` is recorded, and nothing is republished.
  - A Door confirm after the TTL lapsed: `decide` is False, and `expired` is recorded once, with the
    deadline as its time (Review Focus 2).
  - A reply `yes please` from sir on `web_pwa`: approve; `yes` is recorded; the action is republished;
    the reply is `Okay.`.
  - A reply from a guest is a normal turn, and nothing is recorded.
  - A reply in a conversation with its own parked run resumes that run and leaves the ask open.
  - A reply in another conversation, while a run is parked elsewhere, answers the ask (Review Focus 1).
  - The classifier says `other`: a normal turn.
  - A reply on the `voice` or `satellite` channel: a normal turn.
  - A `yes` on a script ask puts the script in `allowed_scripts`.
  - `_report_outcome`: a Reflex success sends no notification; a failure sends "That didn't work".
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the touched test files.** They must PASS.
- [ ] **Step 5: Gate and commit:** `feat(reflex): yes, no and silence are answers`.

### Task 13: The router lets an answered Reflex script through

**Files:** `core/routing/domain_router.py`, `docs/autonomy.md`
- Test: `tests/core/routing/test_router_enforcement.py`

**Interfaces:** the rule `action.source.startswith("reflex") and risk != "benign"` now rejects unless
`risk == "elevated" and action.confirmed`. A critical Reflex action is rejected even when confirmed.
Everything else is as today.

- [ ] **Step 1: Write the failing tests:**
  - Reflex, elevated, confirmed: executes;
  - Reflex, elevated, unconfirmed: `autonomy_violation`;
  - Reflex, critical, confirmed: `autonomy_violation`;
  - System 2 is unchanged.
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** They must PASS.
- [ ] **Step 5: Gate and commit:** `feat(routing): an answered Reflex script may run`.

### Task 14: The report, the docs, and the ask goldens

**Files:**
- Modify:
  - `core/reflex/shadow_report.py`: an "Asks" section with asked, held by reason, and answers
    (yes/no/expired) from the counters and `alfred:reflex:answers`. It settles an expired ask first;
  - `docs/reflex-anticipation.md`: asks, answers and settings;
  - `docs/notifications.md`: ask notifications;
  - `docs/PRD.md`: the 4.4 tiered-autonomy row's ask half, and the date;
  - `evals/coverage.yaml`;
  - `docs/evals.md`.
- Harness: `evals/harness/scenario.py`, `driver.py` and `bus.py` gain the step
  `reflex_settings: {mode: ask, ask_tools: [...]}`. It PUTs the admin API, and it is restored after the
  golden, as `dnd` is.
- Create these goldens:
  - `asks_ask_mode_asks.yaml`: the tv_night_dims shape, at an hour outside the default quiet hours, with
    `ask_tools: ["home.light_*"]`. Expect `reflex_outcome: {outcome: [asked, none]}`. When asked, the
    harness sees a pending action from `reflex-engine` (`bus.pending_actions`).
    Superseded during execution: the goldens switch a light that starts off, by an automation, at 02:00 with quiet hours moved, because the model answers `none` in this shape.
  - `asks_dnd_holds.yaml`: `dnd: on`, then the same events. `reflex_outcome` gains an optional `reason`;
    expect `{outcome: [held, none], reason: dnd}`.
- Test: `tests/core/reflex/test_shadow_report.py`, `tests/evals/harness/test_driver_reflex_settings.py`,
  `tests/evals/test_goldens_load.py`

- [ ] **Step 1: Write the failing tests:** the report's ask section, the step's PUT and its restore, and
  `reason` in `reflex_outcome`.
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Implement, write the goldens, and update the docs.**
- [ ] **Step 4: Run** `tests/core/reflex/` and `tests/evals/`. They must PASS.
- [ ] **Step 5: Run the reflex suite** (one suite, sequential).
- [ ] **Step 6: Gate and commit:** `feat(reflex): report asks and answers; ask goldens`.

**PR C:**
- Title: `feat(reflex): Reflex asks, behind a shadow switch`.
- Body: `Refs #317`; it ships in `shadow`, and the switch is the owner's (Task 21).

## Phase D — Scripts (PR D in home-service, PR E in alfred)

### Task 15 (home-service): Reflex gets the script tool, still elevated

**Files:**
- `config/reflex_tools.yaml`: `script: {turn_on: []}`;
- `app/capability_generator.py`: `REFLEX_DOMAINS` gains `script`. `_reflex_specs(domain, services, index)`
  takes its risk from `self._risk_map.domain_tool_risk(domain, index)` instead of always `"benign"`, so
  light, switch, media_player and scene stay benign;
- the README's audience note.
- Test: `tests/test_capability_generator.py`: `home.script_turn_on` has audience `reflex` and risk
  `elevated`, and `home.light_turn_on` stays `benign`.

- [ ] **Step 1: Write the failing test.**
- [ ] **Step 2: Run it.** It must FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run it.** It must PASS.
- [ ] **Step 5: Run the home-service gate, then commit:** `feat(reflex): Reflex may ask to run a script`.

**PR D:**
- The merge deploys through alfred's dispatch, with the owner's go.
- Reflex is in `shadow` or `ask` by then. A script is only ever asked about, and only once
  `home.script_*` is in `ask_tools`.

### Task 16 (alfred, PR E): scripts in the House, the script golden

After PR D merges, move `~/code/.worktrees/home-service/evals-main` to the merged `main`.

**Files:**
- `core/reflex/prompt.py`: `HOUSE_DOMAINS` gains `script`;
- `evals/harness/worlds/apartment.yaml`: `script.movie_night`, "Movie night", and the `script.turn_on`
  service;
- the golden `scripts_only_asked.yaml`. Its settings are mode `ask` and `ask_tools: ["home.script_*", "home.light_*"]`.
  `person.sam` starts the TV playing a movie at 21:00. Expect:
  - `reflex_outcome: {target: script.movie_night, outcome: [asked, held, none]}`;
  - the fake HA records no `script.turn_on` call. Use the harness's existing service-call check, or add
    one;
- `tests/evals/test_goldens_load.py`.

- [ ] **Step 1: Write the failing loader test**, with the round trip for `script.movie_night`.
- [ ] **Step 2: Run it.** It must FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/core/reflex/` and `tests/evals/`. They must PASS.
- [ ] **Step 5: Run the reflex suite.**
- [ ] **Step 6: Gate and commit:** `test(evals): a script is only ever asked about`.

## Phase E — Choosing the model and the output shape (PR F), not before 2026-11-09

### Task 17: Replay labels and scores

**Files:** `core/reflex/replay.py`, `docs/reflex-anticipation.md` ("Replay")
- Test: `tests/core/reflex/test_replay.py`

**Interfaces:**
- `replay label --since --until --records FILE --out labels.jsonl`. Each record gets `ask` only when all
  of these hold:
  - its recorded proposal was an act or ask;
  - it reverses nothing. Use the record's `recent` when it has one. Otherwise rebuild the person changes
    from the log's earlier records within the window;
  - it does not repeat the same tool and target within 2 hours;
  - it falls outside quiet hours.

  Every other record gets `none`. Labels stay private: 0600, under `~/code/alfred-deploy/reflex-replay/`.
  The owner reviews the `ask` list privately.
- `replay score --labels FILE --model MODEL --output text|tool|native|json` re-runs each prompt one at a
  time. It prints counts only:
  - wrong asks: the label is `none` and the model acts or asks;
  - missed asks: the label is `ask` and the model gives none;
  - would_reverse: the candidate's proposal reverses something, using the record's changes. This must
    be 0;
  - invalid replies;
  - latency p50 and p95.

- [ ] **Step 1: Write the failing tests:** labels from a small log covering a reversal, a repeat, quiet
  hours and an ask; scores from a `FunctionModel`; and output that names no entity.
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** They must PASS.
- [ ] **Step 5: Gate and commit:** `feat(reflex): label and score the replay set`.

### Task 18: Evals pick a model per role

**Files:**
- `evals/cli.py`: `--reflex-model`, which defaults to `--model`;
- `evals/harness/orchestrate.py`: `RunOptions` and `RunMeta` keep a model per role;
- `evals/harness/stack.py`: `StackConfig.reflex_model`; `REFLEX_MODEL=vllm:<reflex model>` still points at
  the proxy;
- `evals/harness/proxy.py`: a `system1` request goes to `--reflex-upstream`, which defaults to the vLLM
  URL. It carries `Authorization: Bearer $ALFRED_EVALS_REFLEX_API_KEY` when that is set;
- `evals/harness/preflight.py`: checks only a local upstream's models;
- `evals/harness/report.py`: the scorecard names the model for each role;
- `docs/evals.md`.
- Test: `tests/evals/harness/test_stack.py`, `test_proxy.py`, `test_orchestrate.py` and `test_report.py`;
  `tests/evals/test_cli.py`.

- [ ] **Step 1: Write the failing tests:**
  - with `--reflex-model`, the env has `REFLEX_MODEL=vllm:<that>` and System 2 is unchanged;
  - the proxy sends a `system1` request to the reflex upstream with the key, and any other role to vLLM
    without one;
  - the scorecard names both models.
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** They must PASS.
- [ ] **Step 5: Gate and commit:** `feat(evals): a model per role, and a hosted Reflex through the proxy`.

### Task 19: Output shapes

**Files:** `core/reflex/agent.py`, `shared/config.py` (`REFLEX_OUTPUT`: `text`, `tool`, `native` or
`json`; default `text`), `core/reflex/replay.py` and `evals/cli.py` (`--reflex-output`),
`docs/reflex-anticipation.md`
- Test: `tests/core/reflex/test_agent.py`

**Interfaces:**
- `build_reflex_agent(model, output: ReflexOutput = "text")`:
  - `text` is today's;
  - `tool` is `ToolOutput(ReflexReply)`;
  - `native` is `NativeOutput(ReflexReply)`;
  - `json` is `PromptedOutput(ReflexReply)`.
- `class ReflexReply(BaseModel)` has `decision`, `tool`, `parameters` and `reason`.
- Every shape converts to a `ReflexProposal` through one function that applies `parse_decision`'s checks:
  the tool must be one of Reflex's, and the parameters must be an object.
- Note (added after PR A): `text` already sends a per-call JSON schema, `reply_format(tools)`, whose
  `tool_name` is an enum of the prompt's tools. A shape compared against it keeps that enum, or its
  invalid rate is measured against a weaker request.

- [ ] **Step 1: Write the failing tests:** each shape with a `FunctionModel`, an unknown tool → `invalid`
  in every shape, and a bad `REFLEX_OUTPUT` → a startup error.
- [ ] **Step 2: Run them.** They must FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them.** They must PASS.
- [ ] **Step 5: Gate and commit:** `feat(reflex): output shapes to compare`.

### Task 20: The comparison

- [ ] **Step 1: Agree the bounds with the owner.**
  - Which candidates run.
  - Local candidates run one request at a time. Any swap of the served model needs the owner's go.
  - Hosted candidates run only within a cost the owner agrees to, on generic goldens only.
- [ ] **Step 2: Score each candidate and shape.** Run the reflex suite and `replay score`.
- [ ] **Step 3: Build one table, counts only.** Its columns: wrong asks, missed asks, would_reverse (must
  be 0), invalid replies, and latency p50 and p95. With the owner's OK, post it to #317.
- [ ] **Step 4: The owner picks.** Set `REFLEX_MODEL` and `REFLEX_OUTPUT` in production's `.env` at the
  owner's go, and restart.
- [ ] **Step 5: PR F.** Title: `feat(reflex): choose Reflex's model and output by evals`. Merge at the
  owner's go.

## Go live

### Task 21: The owner switches to `ask`, and the done check

- [ ] **Step 1: Confirm with the owner how an ask will reach them.** Either the PWA is open, so the Door
  shows it, or the PWA's Web Push has landed.
- [ ] **Step 2: The owner chooses the tool patterns and the mode.** For example lights, media players and
  scripts:
  ```
  docker exec alfred python -m core.reflex.settings set ask_tools '["home.light_*", "home.media_player_*", "home.script_*"]'
  docker exec alfred python -m core.reflex.settings set mode ask
  ```
- [ ] **Step 3: A week later, measure.** Run `docker exec alfred python -m core.reflex.shadow_report --days 7`.
  The targets:
  - no ask that would reverse a person's change;
  - asks a day at or under the cap of 6;
  - most answers are yes;
  - invalid replies near zero;
  - event to decision p50 under 500 ms.
- [ ] **Step 4: Report.**
  - Post the counts and the verdict to #317, with the owner's OK.
  - Ask the owner before closing #317, or before rewriting or closing #286 and #287.
