# Core — Alfred OS

This directory contains Alfred's brain:
- `reflex/` — System 1 SLM engine (fast event → action loop)
  - `engine.py` — SLM inference with dynamic tool prompt
  - `tool_registry.py` — Reads tool manifests from Redis `alfred:tool_registry`
  - `runner.py` — Event loop orchestration
- `memory/` — Markdown preferences + scratchpad
- `triggers/` — Dynamic trigger engine (proactive actions)
  - `models.py` — BaseTrigger ABC, ActionPayload, TriggerContext
  - `registry.py` — TriggerRegistry (decorator-based type registration)
  - `types/` — Concrete trigger types (time, sensor, composite)
  - `store.py` — Redis CRUD + YAML snapshot/rehydration
  - `engine.py` — Evaluation loops and fire logic
  - `feature.py` — TriggerFeature (CRUD tools via BaseFeature)
  - `server.py` — HTTP endpoint for tool dispatch
- `conscious/` — System 2 cloud LLM (Phase 3)
- `voice/` — Voice I/O adapters (Phase 3)
- `librarian/` — Nightly preference consolidation (Phase 3)

## Running

```bash
uv run python -m core.reflex  # starts the Reflex Runner
uv run python -m core.triggers  # starts the Trigger Engine
```

**Fail-fast:** Runner exits with RuntimeError if no tools are registered in Redis.
Tools and system prompt are cached after first event (call `engine.reload_tools()` to refresh).

See path-scoped rules in .claude/rules/core/ for component-specific constraints.
