# Tiered autonomy: deferred hardening items

**Priority:** low
**Source:** HA Plan 3 (`feat/attention-tiered-autonomy`) whole-branch review — final
fix-before-merge pass. The two safety-critical items from that review (atomic
`confirm_pending_action` via `GETDEL`, dispatch-side sir-only gate on action tools in
`ConsciousEngine._dispatch_tool_call`) shipped in the same branch; everything below was
explicitly deferred as non-blocking.

## Summary

A grab-bag of hardening and consistency follow-ups surfaced while reviewing the
attention-set / dispatch-layer risk enforcement / pending-action confirmation flow
(`docs/autonomy.md`). None of these are exploitable today given current call sites and
consumers — each item below explains why it's currently inert or low-severity — but
they should be closed out before the surface area they touch grows.

## Items

### 1. Generalize the dispatch-side identity check beyond action tools
`ConsciousEngine._dispatch_tool_call()` now re-checks `is_sir` for `ACTION_TOOL_NAMES`
(confirm_pending_action / attention_*) as defense-in-depth against a guest-turn model
hallucinating a sir-only tool call. Memory tools and any other future sir-gated tool
category rely solely on the manifest-level gate (never offered to guest turns), with no
matching dispatch-time check. **Acceptance:** extract a single `_require_sir(name,
is_sir)` helper (or a declarative "sir-only tool names" set spanning action + memory
tools) and apply it uniformly, so a new sir-gated tool category can't reintroduce the
manifest-only gap this fix just closed for action tools.

### 2. `web_server.py` confirm endpoint: auth + type consistency
`core/channels/web_server.py` `confirm_action()` (`POST /api/actions/{request_id}/confirm`)
manually checks `getattr(request.state, "authenticated", False)` instead of using
`Depends(require_authenticated)` like the rest of the authenticated surface, and extracts
the Redis handle as `r: aioredis.Redis[Any] = app.state.redis  # type: ignore[type-arg]`.
**Acceptance:** switch to `Depends(require_authenticated)` for consistency with other
authenticated routes, and drop the `type: ignore` by typing the extraction as
`r: aioredis.Redis = app.state.redis` (matching the pattern used elsewhere in the file).
Behavior-neutral — cleanup only.

### 3. Trigger-fired domain actions bypass DomainRouter 🔒-adjacent (not sensitive, but security-relevant)
`core/triggers/engine.py` (`_fire()`) XADDs `ActionRequest` events straight to
`alfred:actions` (`ACTIONS_STREAM`) for triggers with `action` set — it never goes
through `DomainRouter.route()`, so the risk lookup and critical-action interception in
`core/routing/domain_router.py` never runs for trigger-originated actions. **Currently
inert:** no consumer in the codebase executes an unconfirmed `target_service`-bearing
entry from `alfred:actions` outside of `_consume_internal_actions` (which only acts on
`confirmed=True` domain actions or recognized internal action names) — a trigger-fired
domain action with `confirmed=False` is effectively a dead letter today, not a silent
critical-action execution. **Acceptance:** if/when trigger→domain critical execution is
wired up (e.g. a trigger action that unlocks a door), it MUST route through
`DomainRouter` (directly, or by having the trigger engine call it in-process) so critical
actions still require confirmation. Add a test asserting a critical-risk trigger action
is intercepted, not executed directly, before wiring any trigger action consumer.

### 4. AttentionSet cooldown keyed by entity_id only
`core/reflex/attention.py` / the per-entity 5s cooldown in `core/reflex/runner.py` keys
solely on `entity_id`. Two different domains sharing an entity_id spelling (unlikely but
possible with third-party integrations) would share a cooldown bucket. **Acceptance:**
key the cooldown on `(domain, entity_id)` instead. Also add a regression test for the
inverse case that's currently untested: "cooldown allows fire again after the window
elapses" (today's tests cover suppression within the window, not recovery after it).

### 5. `tool_risk()` wrong-shape-but-valid JSON could raise
`core/routing/risk.py` `tool_risk()` parses the tool manifest JSON from
`alfred:tool_registry` to find a tool's declared risk. Valid JSON with an unexpected
shape (e.g. `tools` present but not a list, or a tool entry that isn't a dict) is not
currently guarded and could raise inside `DomainRouter.route()`'s enforcement path —
turning a "fail-safe to benign-default" lookup into an unhandled exception that would
surface as a 500 instead of a routed/rejected action. **Acceptance:** add a structural
guard (defensive `isinstance` checks / `try/except` around the shape-dependent access)
so a malformed manifest degrades to the documented "benign" default instead of raising.

### 6. Silent unregistered-service confirm in `_consume_internal_actions`
`core/conscious/__main__.py` `_consume_internal_actions()` routes confirmed domain
actions through `DomainRouter`, which already returns an `ActionResult` with
`status="error"` when `target_service` has no registered agent — but the consumer does
not log that outcome, so a confirmed critical action against a since-deregistered or
misspelled service fails silently from an operator's point of view. **Acceptance:** warn-log
(`log.warning(...)`) when the routed result for a confirmed action comes back
`status="error"`, including `request_id` and `target_service`, so this is visible in
normal service logs rather than requiring a Redis inspection.

### 7. `action_tools.py` attention tools use bracket access on `params`
`dispatch_action_tool()` in `core/conscious/action_tools.py` does
`params["domain"]` / `params["entity_id"]` (bracket access, not `.get()`) for
`attention_add`/`attention_remove`. A malformed tool call from the LLM (missing key) raises
a bare `KeyError` that bubbles up as an opaque `Error executing action tool: 'domain'` to
the caller (caught by `ConsciousEngine._dispatch_tool_call`'s broad `except Exception`,
so it doesn't crash the process, but the message is unhelpful for debugging a bad tool
call). **Acceptance:** switch to `.get()` with an explicit validation error message
(e.g. `{"error": "attention_add requires 'domain' and 'entity_id'"}`) so malformed calls
fail with an actionable message instead of a raw `KeyError` repr.
