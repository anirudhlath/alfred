# Librarian: Down-weight Admin-Initiated Trigger Fires

## Summary

`TriggerFired` now carries a `fired_by: Literal["engine", "admin"]` provenance
field (default `"engine"`). Manual admin fires set `fired_by="admin"`, and this
value flows end-to-end: TriggerEngine.fire → `TriggerFired` event → Reflex
Engine's TriggerFired consumer → `ReflexObservation.trigger_event` → Memory
Ingestor → episodic memory. The Librarian's pattern detection should down-weight
or exclude observations derived from `fired_by="admin"` events so that manually
fired triggers do not crystallize into procedural routines.

## Context / Motivation

A manually fired trigger (via the admin API / Mission Control) is an operator
action, not an organic condition match. Without provenance, those fires were
indistinguishable from real engine fires, so repeated manual testing/firing of a
trigger could pollute pattern detection — the Librarian might detect a false
"routine" and promote it to crystallized (procedural) memory, which the Reflex
Engine would then execute autonomously.

The provenance field now exists at every hop. What remains is to teach the
Librarian's consolidation pipeline (`core/librarian/consolidator.py` — Reflex
action analysis + pattern detection / routine lifecycle) to read
`trigger_event.fired_by` (present on episodic entries derived from TriggerFired
observations) and treat admin-fired observations differently: exclude them from
the candidate set for routine promotion, or apply a confidence penalty so they
cannot cross the promotion threshold on their own.

## Acceptance Criteria

- The Librarian's pattern detection distinguishes admin-fired events: episodic
  observations whose originating `TriggerFired` had `fired_by="admin"` are either
  excluded from routine-promotion candidates or down-weighted so they cannot, by
  themselves, crystallize into a routine.
- Organic (`fired_by="engine"`) fires retain their current weighting — no
  regression to existing pattern detection for genuine condition matches.
- A unit test covers the down-weight/exclusion path: an admin-fired observation
  does not contribute to (or is insufficient on its own for) routine promotion.
