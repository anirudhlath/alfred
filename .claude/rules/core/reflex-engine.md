---
paths:
  - "core/reflex/**"
---

# Reflex Engine Rules

The Reflex Engine (System 1) is the fast-path SLM that processes events.

- MUST be eval-able: structured (event, preferences) in → structured action out
- No side effects during inference — side effects happen when the action is executed
- Reads preferences from core/memory/preferences/ (read-only)
- Reads tools from ToolRegistry (Redis `alfred:tool_registry`) — NEVER hardcode tool names
- Builds system prompt dynamically from registered tool metadata
- Validates SLM-returned target_service against registered services
- Appends observations to scratchpad via Redis List (never direct file write)
- Target latency: sub-500ms event → action
- All inference calls MUST use @track_latency and @track_tokens decorators
- Never call the cloud LLM (System 2) from the reflex path
- Uses Ollama for local inference — model configured via OLLAMA_MODEL env var
- Starts with no tools — discovers them dynamically via TTL-based cache refresh (5 min)
