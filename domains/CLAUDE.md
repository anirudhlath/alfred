# Domains

Organizational boundaries. Each domain has sub-agents that manage its concerns.

- `home/` — Smart home domain (Phase 1: home_agent.py)
- `media/` — Media domain (Phase 2: media_agent.py)

Sub-agents are Alfred's internal staff. They route actions to external microservices via MCP tool calls. All communication is Pydantic-validated JSON.
