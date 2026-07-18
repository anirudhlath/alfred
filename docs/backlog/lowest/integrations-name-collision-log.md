# Log a warning on adapter/service name collisions in GET /api/integrations

## Summary
`GET /api/integrations` (`core/channels/web_server.py::list_integrations`) concatenates
two independently-named lists: in-process adapters
(`IntegrationRegistry.available()`) and registry-declared sovereign services
(`list_service_manifests`). If an adapter and a service happen to share a `name`
(e.g. someone names a sovereign service `weather`), the merged list silently contains
two entries with the same `"name"` field — the frontend `IntegrationCard` list would
render both with no indication anything is wrong.

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
