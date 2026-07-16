# ACTIONS_STREAM + trigger-mutation reliability

**Priority:** medium
**Source:** PR #21 review (code-architect + multi-agent review)

## Summary
`ACTIONS_STREAM` became a multi-consumer hot path in this PR (home-agent, conscious,
and the new `triggers-internal` group, plus a live telemetry tail). Several
reliability gaps around it and the trigger mutations remain. All are low-risk on the
current single-user deployment but should be closed before multi-user / long-running
production.

## Items

### 1. Stream is never trimmed
No `XADD ... MAXLEN` or periodic `XTRIM` anywhere. Every `ActionRequest` ever published
(the high-volume Reflex/Conscious/Trigger domain-action path) accumulates forever.
**Acceptance:** add `maxlen=~10_000, approximate=True` to the canonical `ACTIONS_STREAM`
`xadd` sites (or a periodic `XTRIM` task).

### 2. `triggers-internal` group replays the whole backlog on first deploy
`ensure_consumer_group` creates the group at `id="0"`, so the first start after this PR
replays the entire untrimmed historical stream. Admin trigger actions only exist from
this deploy forward. **Acceptance:** create this specific group at `id="$"` (add an
`id` param to `ensure_consumer_group` or a dedicated call).

### 3. At-most-once delivery + no pending redelivery
`actions_loop` reads only `">"` with no startup drain of `"0"` / `XAUTOCLAIM`, and acks
after handling. A crash between `XREADGROUP` and `XACK` strands the entry in the PEL
forever; the API already returned `{"status":"queued"}`. **Acceptance:** on startup,
drain the group's own pending entries before switching to `">"`, and add a periodic
`XAUTOCLAIM` (min-idle ~60s). `set_trigger_enabled` is idempotent; guard `fire_trigger`
against duplicate redelivery. Apply the same to the conscious internal-actions consumer.
Add a test that a pre-loaded pending entry is reprocessed after restart.

### 4. Unsynchronized read-modify-write on `alfred:triggers`
The actions consumer and the 1s tick loop both do whole-object get → `model_copy` →
`store.save` on the same trigger, interleaving at await points; either write can
silently revert the other. `_resolve_trigger` refreshes the cache only on a miss, so an
enable/disable can start from a stale snapshot. **Acceptance:** re-read the latest
trigger from Redis (not the cache) immediately before each `save` and merge only the
changed field, or make trigger writes field-merged via a small Lua/WATCH transaction.
Also add `effective_within_seconds` (queued ≠ applied) to the `fire_trigger` response,
matching the enable path.

### 5. Manual `run_librarian` can race the scheduler
`_run_librarian_now` calls `librarian.consolidate()` inline while
`librarian_scheduler.run()` runs the same `Librarian` instance with no mutual
exclusion; the crash-recovery drain path can hand the in-flight batch to the second
run, duplicating episodic writes, and it head-of-line-blocks the internal-actions
consumer. **Acceptance:** guard consolidation with an `asyncio.Lock` shared by the
scheduler and the manual handler (skip/queue if already running, report "already
running"), and consider running the manual consolidation as a task rather than inline.
