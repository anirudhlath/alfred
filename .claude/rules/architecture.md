# Architecture Rules — The Five Pillars

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
- Tools are defined via `BaseFeature` + `@tool` — this is the ONLY tool pattern
- `AlfredClient.discover_features()` scans packages for `BaseFeature` subclasses and auto-registers tools
- Registration: `client.register()` writes to Redis `alfred:tool_registry`; `client.unregister()` on shutdown
- Never hardcode tool names or service lists — Reflex Engine reads from `ToolRegistry` at runtime

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

## 5. Fluid Intelligence
- Alfred solves novel problems by composing general-purpose **primitives** (triggers, state, notifications, actions, memory), not by requiring purpose-built tools for every task
- **Primitives vs. Effectors:** Primitives are internal cognitive building blocks that can be composed. Effectors are interfaces to external systems (HA, Signal, weather API) that cannot be replicated through composition. Build effectors for external systems. Build primitives for everything else. Never build an effector for something that can be composed from primitives.
- Before adding a new tool, ask: "Is this an effector (reaching outside the system) or am I hardcoding what should be composed from existing primitives?"
- **Fluid → Crystallized lifecycle:** System 2 (Conscious Engine) composes primitives to solve novel problems (fluid intelligence). The Librarian detects repeated patterns and promotes them to procedural memory (crystallized intelligence). System 1 (Reflex Engine) executes crystallized patterns in <500ms without reasoning.
- Never hardcode what should be learned — if a behavior can emerge from composition + pattern detection, let it emerge
