# Architecture Rules — The Four Pillars

These are non-negotiable constraints. Every design decision must respect them.

## 1. Proactivity & Dynamic Triggers
- Triggers are created dynamically by the LLM at runtime
- Never hardcode scheduled tasks, cron jobs, or IF/THEN rules
- The Trigger Engine evaluates conditions against the Event Bus and time

## 2. Decoupled Domains
- Microservices are sovereign applications in their own repos
- They work independently without Alfred
- alfred-sdk is the ONLY bridge — apps never import from alfred/ directly
- Sub-agents in domains/ are Alfred's internal staff, not external apps
- Registration is runtime discovery via client.register()

## 3. Deterministic Communication
- All inter-agent messages are Pydantic-validated JSON
- No natural language between agents — EVER
- Alfred is a router and synthesizer, not a chat participant
- Every MCP tool call and Event Bus message has a typed schema in bus/schemas/

## 4. Stateful Memory (Librarian Pattern)
- Core preferences in Markdown + YAML frontmatter (core/memory/preferences/)
- Real-time writes go to scratchpad.md ONLY (via Redis List → async writer)
- Core preference files are NEVER edited during runtime
- The Librarian Agent consolidates nightly (Phase 3)
