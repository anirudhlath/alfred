# Context Provider — Deferred Work

## ~~Agent-Scoped Context Visibility~~ DONE
**Completed:** 2026-03-19 (phase3-prerequisites branch)
`ContextReader` now scans all `alfred:context:*` keys via Redis SCAN.

## Option C Entities
Extend `get_context()` in home-service features to include system entities:
automations, scripts, input_booleans. These are "Option C" from the design spec — entities
the LLM should know about but that aren't direct tools.
