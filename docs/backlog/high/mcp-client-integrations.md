# MCP Client Support in IntegrationRegistry

## Summary
Add a generic `MCPIntegrationAdapter` that connects to an MCP server, discovers its tools, and auto-registers them in the IntegrationRegistry as function-calling tools for the Conscious Engine. One adapter = N integrations; stop hand-writing a bespoke adapter per external service.

## Context
Every integration today (weather, calendar, health, robinhood) is a hand-written adapter with its own API quirks. MCP is now the industry-standard tool-server protocol with a large ecosystem of maintained servers (task managers, finance, music, docs). Consuming them extends the existing integration plane — these are *effectors* per Pillar 5, with typed JSON schemas per Pillar 3, auto-discovered per the no-hardcoding rule.

Boundary decisions (from design discussion, 2026-07-15):
- MCP tools are **Conscious-only** — they never enter the SDK ToolRegistry that Reflex reads. MCP round-trips can't serve the sub-500ms System 1 path, and third-party servers must not join the nervous system.
- MCP does NOT replace alfred-sdk: SDK = membership protocol for sovereign apps (bus events, ActionRequests, Reflex tool registry); MCP = calling tools Alfred doesn't own, at the edge.
- **Trust boundary:** Alfred has physical actuators. Servers must be explicitly allowlisted in config; tool *results* are untrusted input (prompt-injection vector for an ambient agent that controls the home).

## Acceptance Criteria
- `MCPIntegrationAdapter` connects to a configured MCP server (stdio + streamable HTTP transports), lists tools, and exposes them through the IntegrationRegistry with correct JSON schemas
- Server credentials/endpoints declared via `CredentialSchema` → keyring + settings UI, consistent with existing adapters
- Servers are allowlisted explicitly in config — no auto-discovery of unlisted servers; per-server enable/disable
- Tool schemas validated with Pydantic before exposure; malformed tools skipped with a warning, not a crash
- MCP tools appear to the Conscious Engine identically to native integration tools (no special-casing in the agentic loop)
- MCP tools are NOT registered in the SDK ToolRegistry and are invisible to Reflex
- Per-server tool-level opt-out (e.g. expose read tools, block action tools) for untrusted servers
- Graceful degradation: unreachable server at startup logs a warning and retries in background; does not block the conscious process
- Tests: mocked MCP server fixture covering discovery, tool call round-trip, credential population, allowlist enforcement, and failure modes
- `docs/mcp-integrations.md` with architecture overview and security model per the document-new-features convention

## Dependencies
- IntegrationRegistry + CredentialSchema (D25) — already built
- Python MCP SDK (`mcp` package) — check latest docs via context7 before implementation
