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
- `parse_decision()` validates the tool against Reflex's own tool list; anything else becomes an `invalid` proposal carrying the raw text, never a silent drop
- Records what it saw and decided on `alfred:reflex:observations` (proposals for act/ask/invalid, debounced passive observations for none) and counts every decision in `alfred:reflex:decisions:<UTC date>`. The Memory Ingestor writes episodic memory and ignores proposals. Reflex never writes the scratchpad
- On the state-change path, `unavailable`/`unknown` (and a missing old state) never reach the SLM — `core/reflex/availability.py` drops or bridges them before the attention gate. TriggerFired events are not bridged: triggers read the raw stream
- Target latency: sub-500ms event → decision
- All inference calls MUST use @track_latency and @track_tokens decorators
- Never call the cloud LLM (System 2) from the reflex path
- Backend via `REFLEX_BACKEND` (`openai` = vLLM in production, `ollama` default); model via `OPENAI_COMPAT_MODEL` / `OLLAMA_MODEL`
- Starts with no tools — discovers them dynamically via TTL-based cache refresh (5 min)
