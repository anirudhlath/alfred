# Log a warning on adapter/service name collisions in GET /api/integrations

## Summary
`GET /api/integrations` (`core/channels/web_server.py::list_integrations`) concatenates
two independently-named lists: in-process adapters
(`IntegrationRegistry.available()`) and registry-declared sovereign services
(`list_service_manifests`). If an adapter and a service happen to share a `name`
(e.g. someone names a sovereign service `weather`), the merged list silently contains
two entries with the same `"name"` field, and nothing in the response says so. The
frontend card list that would have rendered both indistinguishably is gone — the hard
cut removed it, and PWA phase 1 reads `/api/integrations` only to find home-service in
the setup gate — so the rendering half of this question moves to the Workshop (phase 2).
The server-side warning below stands on its own regardless.

## Context
Names are chosen independently: adapter names come from `IntegrationRegistry`
registration keys (`core/integrations/*.py`), service names come from whatever a
sovereign service's `AlfredClient(service_name=...)` declares. Nothing today prevents
the two namespaces from colliding. This is a low-severity, unlikely-in-practice issue
(current adapters are weather/apple_calendar/apple_health/robinhood, current services
are home-service/signal-bridge) — no functional bug, just a silent dual-listing that
would be confusing to debug if it ever happened.

## Acceptance Criteria
- `list_integrations` logs a `logger.warning` when an adapter name and a service name
  from the merged result collide, naming the colliding value.
- No behavior change to the response body — this is observability only.
