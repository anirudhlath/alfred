---
paths:
  - "core/reflex/**"
---

# Reflex Engine Rules

The Reflex Engine (System 1) is the fast-path SLM that decides about events.

- MUST be eval-able: structured (event, preferences, live state) in → `ReflexProposal` out
- No side effects during inference. In shadow mode (#285) nothing a proposal names is executed; #286 adds execution
- Reads preferences from core/memory/preferences/ (read-only)
- Reads tools from ToolRegistry (Redis `alfred:tool_registry`) — NEVER hardcode tool names; only `audience == "reflex"` tools reach the prompt
- The prompt is built in `core/reflex/prompt.py`, ordered stable → volatile (rules + tools, Preferences, Now, House, What changed) so vLLM's prefix cache holds. Keep it near 1,000 tokens — never dump all of live state into it
- Reads only the well-known live-state attributes (`friendly_name`, `area`, plus per-domain details) — see docs/live-state.md
- The request's response format (`reply_format()` in `core/reflex/agent.py`) is built per call from the tools the prompt showed: `tool_name` is an enum of exactly those, so the server never lets the model name another. Never fall back to a bare JSON-object format. The schema decides the reply's shape: change the reply line of `reflex-rules` and `reply_format()` together (`tests/core/reflex/test_agent.py` checks the repo default against it)
- `parse_decision()` still validates the tool against Reflex's own tool list; anything else becomes an `invalid` proposal carrying the raw text, never a silent drop
- Never reverse a person, in code, after the model answers (`core/reflex/reversal.py`): the runner notes every house-device change into `alfred:reflex:person_changes` before the attention gate, the engine reads the window (`reverse_window_minutes` from `alfred:reflex:settings`) and the recent changes before it builds the prompt, and `reversed_entities` turns a reversing proposal into a `would_reverse` outcome. A Redis error there propagates (fail closed). The prompt's `Leave alone` line is a courtesy to the model, not the rule
- Every decision ends in one `ReflexOutcome` (`core/reflex/outcome.py`): none, invalid, would_reverse, shadow. The engine returns it in a `Decided`; its only write is `PersonChanges.recent()` pruning expired records, compare-and-delete so a change noted meanwhile survives. The runner records it on the decision log and counts it as `outcome:<kind>`. Only a published outcome (not none, not would_reverse) becomes a proposal on the observations stream
- Records what it saw and decided on `alfred:reflex:observations` (proposals for act/ask/invalid, debounced passive observations for none and would_reverse) and counts every decision in `alfred:reflex:decisions:<UTC date>`. The Memory Ingestor writes episodic memory and ignores proposals. Reflex never writes the scratchpad. Every decision that reached the model is also kept whole, with its prompt and cause, in `alfred:reflex:decision_log` for up to 30 days or 10,000 entries, whichever comes first (`core/reflex/decision_log.py`; private, best-effort, and slice 2's replay set, read by time with `read(since=, until=)` and replayed by `python -m core.reflex.replay`, which writes nothing and prints counts only)
- A redelivered event is not inferred again: the runner checks `alfred:reflex:decided:{event_id}` after the attention gate and before the engine, and sets it only after the decision is counted and published (a failed or raised decision leaves no marker). `TriggerFired` uses its own `event_id`. The marker makes a second decision about one event unlikely, not impossible: a crash before it is set decides the event again and records it twice, and the replay drops the duplicates, keeping each `event_id`'s first record
- On the state-change path, `unavailable`/`unknown` (and a missing old state) never reach the SLM — `core/reflex/availability.py` drops or bridges them before the attention gate. TriggerFired events are not bridged: triggers read the raw stream
- Target latency: sub-500ms event → decision
- All inference calls MUST use @track_latency and @track_tokens decorators
- Never call the cloud LLM (System 2) from the reflex path
- Decides through the Pydantic AI agent in `core/reflex/agent.py` (`build_reflex_agent` / `decide`), built from `REFLEX_MODEL` (blank = `vllm:` + `OPENAI_COMPAT_MODEL`) via `resolve_model`; the process stays off, and alive until shutdown, when no model resolves. Reflex needs an OpenAI-compatible server — there is no other backend
- Starts with no tools — discovers them dynamically via TTL-based cache refresh (5 min)
