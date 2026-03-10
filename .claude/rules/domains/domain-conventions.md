---
paths:
  - "domains/**"
---

# Domain Sub-Agent Conventions

- Each domain is an organizational boundary (home, media, security, etc.)
- Sub-agents within a domain subscribe to the Event Bus for relevant topics
- Sub-agents translate high-level actions into microservice-specific MCP tool calls
- All communication uses Pydantic-validated JSON payloads (Pillar 3)
- Sub-agents escalate anomalies to Alfred core via typed escalation events
- Sub-agents maintain domain-specific state if needed
