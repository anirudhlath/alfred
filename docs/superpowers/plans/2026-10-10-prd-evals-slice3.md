# PRD Eval Suite Slice 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate guest identity boundaries, critical-action consent, and credential privacy against the real throwaway stack.

**Architecture:** Extend the recording proxy and pure checks, capture pending-action snapshots between conversation turns, and seed fake credentials before boot. Goldens drive actual UserRequests and observe the fake HA; they do not repair product behavior or bypass the LLM confirmation path.

**Tech Stack:** Python 3.13, Pydantic v2, Inspect AI, pytest, Redis, encrypted cryptfile keyring, Docker, local OpenAI-compatible inference.

**Spec:** `docs/superpowers/specs/2026-10-06-prd-eval-suite-design.md`, slice 3.

## Global Constraints

- Shared vLLM: at most four concurrent upstream requests across proxy and judge; one eval container at a time.
- No real devices: only fake HA, fixed fake credentials, and temporary eval storage. Never read operator env or keyring to seed a run.
- No production behavior changes. Confirmed gaps remain visible in scorecards; guest enforcement is pending as the PRD says.
- Preserve shipped/pending distinctions. Onboarding's capture of guest choices is deterministic and stays covered by its existing tests; this does not imply runtime enforcement.
- Public artifacts contain fictional fixtures only. Existing sensitive findings remain in their private advisory; never create a public exposure issue.
- Existing worktree and branch are retained. Baseline for this continuation is `699e406`.
- Run narrow tests during implementation; the controller runs full gates sequentially at completion. Use uv and locked tools.
- Do not save a first baseline before the owner reviews the new goldens. Run results and Inspect logs remain local.
- Before any public push, run the exposure runbook's Secret hygiene checks; never copy those commands into repository files.

## Review Focus

- Secrets inside tool schemas, assistant function arguments, structured content, and non-message request fields must be detected.
- An absent role call or missing snapshot is missing evidence, never a vacuous privacy/boundary pass.
- Mixed identities in one conversation require checks scoped to each step, including negative indexes.
- No matching or multiple matching pending actions must produce a product failure, while malformed Redis records remain harness errors.
- Duplicate confirmations must not cause duplicate HA execution; credential preparation must survive stack restart and never select the host keyring.

## Decisions carried forward

- The existing `prompt_not_contains` check is extended; it was already built in slice 2.
- Session history stores user/final assistant text, not tool turns. Explicit-ID confirmation therefore uses a bounded placeholder referencing an earlier pending snapshot. Natural-language confirmation remains a separate golden that can expose the product gap.
- Pending snapshots plus per-step HA evidence prove interception and execution. No new ACTIONS_STREAM collection/check is required.
- Goldens for capture-only onboarding policy use existing deterministic coverage; enforcement behavior is evaluated as pending.

### Task 1: Complete privacy recording and fake-credential preparation

**Files:** Modify `evals/harness/evidence.py`, `proxy.py`, `checks/llm.py`, `checks/__init__.py`, `world.py`, `stack.py`; create a focused credential-preparation module under `evals/harness/`; extend `tests/evals/harness/test_checks.py`, `test_proxy.py`, `test_world.py`, `test_stack.py`, and add real temporary-keyring tests.

**Interfaces:** `LlmCall.request_body: dict[str, Any]` with an empty default; `World.credentials: dict[str, dict[str, str]]` with an empty default; a credential-preparation function accepting only the new eval data directory and fake world credentials. `ToolOfferedParams(tool, role='system2', at_step=None)` drives `tool_offered` and `tool_not_offered`.

- [ ] Write behavioral failures for a secret in tool schema, tool-result content, assistant function arguments and a non-message field; include upstream-error recording, no matching role, and legacy Evidence fallback.
- [ ] Capture the complete validated request before forwarding; recursively search string leaves/keys using existing literal/regex Needle semantics. Keep current messages and offered names backward compatible.
- [ ] Write and pass tests for offered/not-offered catalog checks with normalized tool names, all relevant calls, no role calls, mixed actors and negative step indexes. Add step scope to existing tool-call/argument checks only where the goldens need it.
- [ ] Prepare fake credentials before docker up in an isolated interpreter whose data path, cryptfile backend and eval passphrase are set before importing `shared.secrets`. Use structured stdin, not shell interpolation. Verify with a separate interpreter that the real container-format keyring reads the seeded secret and a restart re-seeds it; leave an unrelated keyring fixture unchanged.
- [ ] Run focused tests, locked ruff/format and relevant mypy checks. Commit only owned files and report exact red/green results.

### Task 2: Observe and drive real critical-action conversations

**Files:** Modify `evals/harness/evidence.py`, `bus.py`, `driver.py`, `scenario.py`, `checks/home.py`, `checks/__init__.py`; create `checks/pending.py`; extend associated tests and `tests/evals/harness/factories.py`.

**Interfaces:** `PendingSnapshot(step: int, actions: list[ActionRequest])` on Evidence; `Bus.pending_actions() -> list[ActionRequest]`; `UserStep.pending_action` selects `{at_step: <earlier nonnegative step>, tool: <normalized name>}`. Its user text contains the sole supported placeholder `{pending_action_id}`. `pending_action` / `pending_action_absent` checks select snapshot `at_step`, tool, optional target/parameters, and optional exact count.

- [ ] Write failures for malformed pending data, expired keys, snapshot absence versus a valid empty snapshot, wrong tool/parameters, duplicate matches, and per-turn isolation.
- [ ] Read pending keys with the canonical prefix and typed ActionRequest validation under the existing bus timeouts. Snapshot after each settled step, preserving the real IDs. Do not silently drop malformed data or mutate product routing.
- [ ] Add the two pending checks; absence of a requested snapshot fails, valid empty evidence can pass. Support normalized names and existing matching semantics.
- [ ] Test and implement the bounded user placeholder. Reject absent placeholders, future/self references and unknown selector fields at load. Use exactly one matching action from the earlier snapshot; zero/multiple matches use a fixed nonexistent eval ID, so the preceding pending/count check and final execution checks fail as product results rather than infrastructure errors.
- [ ] Extend HA checks with `at_step` windows and an exact `count` on positive checks, preserving `after_step` and rejecting conflicting selectors. Test no execution before consent, one execution after, duplicate/repeated consent and negative indexing.
- [ ] Clean up only this sample's pending IDs, using existing cancellation-safe cleanup; never clear the whole store. Test failed/cancelled play cleanup with unrelated pending entries preserved.
- [ ] Run focused tests, locked ruff/format and relevant mypy checks. Commit only owned files and report exact red/green results.

### Task 3: Ship slice-3 goldens, coverage and documentation

**Files:** Create `evals/suites/guest_boundary/*.yaml`, `critical_actions/*.yaml`, `privacy/*.yaml`; modify `evals/coverage.yaml`, `docs/evals.md`, `tests/evals/test_goldens_load.py` and associated coverage tests only as required. Add a fictional credential-bearing world fixture, keeping existing suites' world unchanged.

**Interfaces:** Use Tasks 1–2's catalog/pending/HA checks and placeholder; use existing actor, reply, notification, tool-argument and judge checks. New world credentials use the actual `apple_calendar` namespace and `username`, `password`, `caldav_url` fields, all fictional, with an unreachable loopback CalDAV URL until slice 5 supplies Radicale.

- [ ] Author shipped guest identity goldens for web and Signal, sir positive controls, a sir-to-guest turn transition, and personal-data requests. Assert sir-only memory, integration and confirmation tools are withheld on the guest turn. Guest policy enforcement/control access goldens are pending.
- [ ] Author critical-action goldens for lock unlock, garage open and alarm disarm: interception before consent, explicit-ID consent and actual execution, refusal, repeated confirmation, wrong ID and an innocuous status query. Include a separate natural 'yes' confirmation golden to expose lost cross-turn tool context. Checks assert actual outcomes rather than exact response wording.
- [ ] Author privacy goldens for normal use, requests to reveal credentials and guests, with fake secrets absent from every System 2 request and every reply. Add a raw sensor-metadata negative. Assert that a real role call was recorded.
- [ ] Update PRD mappings: built shipped identity/privacy/critical suites move to `suites`; enforcement stays `pending_suites`; capture-only guest choices map to existing onboarding tests with `not_llm`. Preserve critical row's planned PRD status while documenting that these goldens exercise already-existing code, as the spec directs.
- [ ] Extend golden validation for real fixture services/targets, pending refs and offered-tool checks without hardcoding product tool catalogs. Update docs with check syntax, confirmation fixture semantics, evidence/failure behavior, suites and known gaps; remove slice 3 from the remaining-slices list.
- [ ] Run golden-load/coverage and all eval tests; the controller then runs full lint, format, mypy and pytest, performs final branch review, and runs the three real suites at one epoch first, followed by three epochs if the first run establishes useful product results. Record model/commit provenance, counts, errors and gaps. Save no first baseline.
- [ ] Commit only owned files. Leave work reviewable on the existing branch; publishing and deployment follow the owner's established authorization rules.
