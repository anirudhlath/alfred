---
id: EXP-010
title: Availability Bridging over Recorded Home History
status: in-progress
start_date: 2026-10-04
end_date:
---

# EXP-010: Availability Bridging over Recorded Home History

Slice 1 of #265. Most of what episodic memory stored about the home was devices dropping
off the network and coming back (#203). This experiment checks the bridge that removes
them, `core/reflex/availability.py`, against real history before it ships. It then
checks it again in production after a week.

## Hypothesis

**H1.** Dropping every transition into `unavailable`/`unknown`, and turning each return
into one change only when the state differs from the last real one, removes all
availability transitions. No real change is lost.

**H2.** What remains is the real-change rate, on the order of 150 a day, and no single
entity dominates it.

**H3.** In production, a week after the deploy, passive observations per day fall to
about that rate, and no flaky device dominates the hot store.

## Method

1. **Offline replay (H1, H2).** HA's recorder history for the 77 attention-set entities
   that changed state over 82.3 days was turned into 46,911 consecutive
   `(old_state, new_state)` transitions with their timestamps. Each transition was fed,
   in time order, through the production `bridge_availability` with an in-memory Redis
   double. The replay counted what passed, what was bridged, and whether anything that
   passed still had `unavailable`, `unknown` or no state on either side.
2. **Production (H3).** One week after the merge deploys, count passive observations per
   day in the hot store, and the largest share any single entity holds. Post the numbers
   on #265.

## Results

### Offline replay

| Measure | Value |
|---|---|
| Transitions | 46,911 |
| Touching `unavailable`/`unknown` | 34,346 (73.2%) |
| Passed the bridge | 12,808 (27.3%), about 156 a day |
| Of those, bridged returns (`off → unavailable → on` stored as `off → on`) | 243 |
| Passed with `unavailable`/`unknown`/no state on either side | 0 |
| Largest single-entity share of what passed | 17.4% (a media player) |

The pass count matches the exploration's independent count in #265 exactly (12,808).

### Production

Pending: one week after deploy.

## Analysis

H1 holds on the replay. Nothing that passed touches an availability state. The 243
bridged returns are the changes a plain drop would have lost; most are TVs and streaming
boxes that were turned on or off while their integration was reconnecting.

H2 holds. About 156 changes a day pass. The busiest entity is a media player with
genuine play/pause churn, not a flapping device. Folding brief pause→resume pairs is a
later slice in #265.

Two behaviours are not exercised by the replay, so unit tests cover them
(`tests/core/reflex/test_availability.py`):

- Redelivery. The runner ACKs only on success, and the replay has no failures.
- `None → state` returns. HA re-adds every entity this way after a restart, but the
  recorder export has no such rows.

The first return after the deploy for each entity is dropped, because the bridge has no
known state yet. This costs at most one change per entity.
