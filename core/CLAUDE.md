# Core — Alfred OS

This directory contains Alfred's brain:
- `reflex/` — System 1 SLM engine (fast event → action loop)
- `memory/` — Markdown preferences + scratchpad
- `triggers/` — Dynamic trigger engine (Phase 2)
- `conscious/` — System 2 cloud LLM (Phase 3)
- `voice/` — Voice I/O adapters (Phase 3)
- `librarian/` — Nightly preference consolidation (Phase 3)

See path-scoped rules in .claude/rules/core/ for component-specific constraints.
