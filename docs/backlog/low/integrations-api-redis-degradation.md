# GET /api/integrations should degrade gracefully when Redis is unavailable

## Summary
`GET /api/integrations` (`core/channels/web_server.py::list_integrations`) builds its
response from two sources: in-process adapters (`IntegrationRegistry.available()`, no
Redis dependency) and registry-declared sovereign services
(`list_service_manifests(app.state.redis)`, which does `HGETALL alfred:tool_registry`).
If Redis is down or unreachable, the `HGETALL` raises and the whole endpoint 500s —
even though the adapter half of the response requires no Redis at all.

## Context
Sovereign services (home-service, signal-bridge, ...) are declared entirely via the
Redis-backed tool registry, so losing Redis genuinely means we can't know about them.
But integration adapters (weather, calendar, health, robinhood) are configured
in-process and their credentials live in the OS keyring, not Redis — the Settings page
should still be able to show and edit adapter credentials during a Redis outage. Today
a transient Redis blip makes the entire integrations page unusable instead of just
hiding the service cards.

## Acceptance Criteria
- `list_integrations` catches Redis errors from `list_service_manifests` (or the
  `HGETALL` call it wraps) and falls back to returning adapters only, logging a
  warning.
- A Redis outage does not turn `GET /api/integrations` into a 500 — it degrades to the
  adapter-only list.
- Covered by a test that mocks `app.state.redis.hgetall` to raise and asserts the
  response still contains adapter entries with HTTP 200.
