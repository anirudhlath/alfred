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
   that changed state over 82.3 days was exported as 46,911 consecutive transitions, one
   CSV row each (`ts_utc, entity_id, old_state, new_state`). The export is private home
   data and is not in the repo. Each row was fed, in time order, through the production
   `bridge_availability` with an in-memory Redis double and a synthesized stream entry ID.
   The replay counted what passed, what was bridged, and whether anything that passed
   still had `unavailable`, `unknown` or no state on either side. It was run against the
   bridge as merged, the replay-safe version keyed on stream entry IDs; the first,
   simpler version gave identical counts. The harness:

   ```python
   class FakeRedis:  # HGET/HSET/GET/SET over dicts, bytes out like the real pool
       def __init__(self): self.h, self.s = {}, {}
       async def hget(self, _k, f): return None if f not in self.h else self.h[f].encode()
       async def hset(self, _k, f, v): self.h[f] = v
       async def get(self, k): return None if k not in self.s else self.s[k].encode()
       async def set(self, k, v, **_kw): self.s[k] = v

   redis = FakeRedis()
   for i, row in enumerate(sorted(csv.DictReader(open(path)), key=itemgetter("ts_utc"))):
       event = StateChangedEvent(
           source="home-service", domain="home", entity_id=row["entity_id"],
           old_state=row["old_state"] or None, new_state=row["new_state"],
           timestamp=datetime.fromisoformat(row["ts_utc"]),
       )
       entry_id = f"{int(event.timestamp.timestamp() * 1000)}-{i}"
       passed = await bridge_availability(redis, event, entry_id)
       # count: passed is None (dropped), passed is not event (bridged), and whether
       # passed.old_state or passed.new_state is unavailable/unknown/None (leaked)
   ```
2. **Production (H3).** One week after the merge deploys, count passive observations per
   day in the hot store, and the largest share any single entity holds. Post the numbers
   on #265.
3. **Stored history.** What the stores already held predates the bridge.
   `core/memory/migrate_observations.py` deletes every stored observation whose transition
   touches `unavailable` or `unknown`, and stamps the rest. It was dry-run against the
   production stores, and every planned rewrite was checked for a leftover availability
   state.

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

### Stored history (dry run, 2026-10-05)

| Store | Observations | Blips to delete | To stamp | Planned rewrites still naming `unavailable`/`unknown` |
|---|---|---|---|---|
| Hot | 4,359 | 2,811 (64.5%) | 1,548 | 0 |
| Cold | 2,455 | 1,190 (48.5%) | 1,265 | 0 |

No observation was missing a timestamp. The busiest entity left in either store is a
streaming box, at 7.6% (hot) and 9.9% (cold) of what remains. The migration is applied
after the deploy, from a backup of both stores.

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

- Redelivery. The runner ACKs only on success, and the replay has no failures. Replays
  are keyed on the stream entry ID, so a return is decided once, and a late replay
  cannot roll the known state back.
- `None → state` returns. HA re-adds every entity this way after a restart, but the
  recorder export has no such rows.

The first return after the deploy for each entity is dropped, because the bridge has no
known state yet. This costs at most one change per entity.
