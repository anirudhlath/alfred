# Reflex Input Generalization

## Summary
The Reflex Engine is currently hardwired to `HOME_STATE_STREAM` — it only handles home automation events. For a general ambient system, Reflex should accept state changes from any domain through a unified or pluggable input mechanism.

## Context
Identified during D8 (System 2 Observation) design. The observation pipeline (D8) is downstream of action execution, so it naturally carries over when inputs are generalized. This ticket is about the input side.

## Acceptance Criteria
- Reflex Engine can consume events from multiple domain streams (not just home)
- New domains can register their state streams without modifying Reflex code
- Existing home automation flow continues working unchanged
- Domain-specific event parsing is pluggable (each domain may have different event shapes)
