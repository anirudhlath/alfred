# Expose Alfred as an MCP Server

## Summary
Expose a narrow slice of Alfred's capabilities (recall memories, query live house state, send a notification) as an MCP server, so other agents on the user's machines — Claude Code, IDE agents — can talk to Alfred through the standard protocol.

## Context
alfred-sdk is the membership protocol for sovereign apps that *serve* Alfred (register tools, receive ActionRequests, feed the bus). An MCP server is the opposite direction: agents that *use* Alfred as a service — guests, not staff. They get a doorway with defined rooms: no bus access, no tool-registry registration, no ability to join the nervous system.

This is cheap to build (the tools already exist internally as Conscious Engine memory tools and admin API reads) and immediately useful for the daily Claude Code workflow — e.g. "ask Alfred what's on the calendar" or "have Alfred notify me when this build finishes" from any coding session.

## Acceptance Criteria
- MCP server (streamable HTTP transport) exposing an explicit, small tool set — initial candidates:
  - `recall_memories(query)` — wraps the existing internal memory tool
  - `get_live_state(entity_filter)` — wraps the existing live-state tool / admin reads
  - `send_notification(message, urgency)` — publishes through NotificationPublisher (INFORMATIONAL/relevant urgencies only; no URGENT from guests)
- Auth: gated to trusted network (localhost + Tailscale CGNAT via `require_trusted_network`) at minimum; token auth if MCP client support for it is straightforward
- Read/write asymmetry documented and enforced: reads are broad, writes limited to notifications — no ActionRequests, no trigger mutations, no memory writes from guests
- Server runs inside an existing process (channels is the natural host — it already owns the HTTP surface) rather than a new service
- All tool inputs/outputs Pydantic-validated (Pillar 3)
- Tests: tool round-trips, auth gating, urgency restriction
- `docs/alfred-mcp-server.md` per the document-new-features convention

## Dependencies
- Memory tools (`core/conscious/memory_tools.py`) and admin API reads — already built
- NotificationPublisher — already built
- Python MCP SDK server support — check latest docs via context7 before implementation
- Independent of `high/mcp-client-integrations.md` (client and server sides share only the protocol dependency)
