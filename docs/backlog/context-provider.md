# Context Provider — Deferred Work

## Agent-Scoped Context Visibility
Currently `ContextReader` hardcodes `service_name="home-service"` — replace with multi-service
scan (e.g. `SCAN alfred:context:*`) so the Reflex Engine sees context from all registered services.

## Option C Entities
Extend `get_context()` in home-service features to include system entities:
automations, scripts, input_booleans. These are "Option C" from the design spec — entities
the LLM should know about but that aren't direct tools.
