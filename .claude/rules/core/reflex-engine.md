---
paths:
  - "core/reflex/**"
---

# Reflex Engine Rules

The Reflex Engine (System 1) is the fast-path SLM that processes events.

- MUST be eval-able: structured (event, preferences) in → structured action out
- No side effects during inference — side effects happen when the action is executed
- Reads preferences from core/memory/preferences/ (read-only)
- Appends observations to scratchpad via Redis List (never direct file write)
- Target latency: sub-500ms event → action
- All inference calls MUST use @track_latency and @track_tokens decorators
- Never call the cloud LLM (System 2) from the reflex path
- Uses Ollama for local inference — model configured via OLLAMA_MODEL env var
