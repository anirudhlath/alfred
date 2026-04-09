# Domains

Organizational boundaries. Each domain has sub-agents that manage its concerns.

- `home/` — Smart home domain (`home_agent.py`)
- `media/` — Media domain (scaffolded, not yet implemented)

Sub-agents are Alfred's internal staff. They route `ActionRequest` events to external microservices via HTTP JSON-RPC. All communication is Pydantic-validated JSON.

## How HomeAgent Works

1. Receives `ActionRequest` from Reflex or Conscious Engine (via `DomainRouter`)
2. Looks up service endpoint from `alfred:tool_registry` Redis hash (cached after first lookup)
3. POSTs JSON-RPC payload (`{method, params, id}`) to service endpoint via long-lived `httpx.AsyncClient`
4. Parses response — checks body for `error` key (HTTP 200 does NOT mean success in MCP)
5. Returns `ActionResult` with `status = "success" | "error"`

## Key Patterns

- `RedisLike` protocol for testability — accepts both real and mock Redis
- Protocol-based polymorphism — HomeAgent implements `DomainAgent` protocol, no base class inheritance
- Error stratification: service-not-found → structured error (no exception); HTTP errors → caught + converted; MCP errors → detected via response body
- All error paths return `ActionResult`, never raise

## Gotchas

- Endpoint cache is never invalidated — restart HomeAgent if a service changes its endpoint
- Uses long-lived `httpx.AsyncClient` (30s timeout) — never create per-request clients
- No standalone `__main__.py` — agents run via `DomainRouter` inside Reflex/Conscious processes

## Testing

```bash
pytest domains/  # tests route success + unknown-service error handling
```
