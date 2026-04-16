# D3 + D4: Librarian Pattern Detection & Contextual Decay

**Date:** 2026-04-16
**Status:** Approved
**Scope:** Complete the partially-implemented D3 (pattern detection lifecycle) and D4 (contextual decay) features in the Librarian consolidation pipeline.

## Background

PR #15 (Phase 3 Memory Completion) landed the foundation for both features — `_detect_patterns()`, `_update_routine_lifecycle()`, and `_apply_decay()` all have working implementations. However, several gaps remain that prevent the full fluid → crystallized intelligence lifecycle (Pillar 5) from functioning end-to-end.

### What Exists

- `_detect_patterns()` — LLM call that identifies repeated patterns and creates `RoutineSpec(state="candidate")` entries
- `_update_routine_lifecycle()` — hit/miss tracking, dormant/archived transitions
- `_apply_decay()` — basic migration pressure formula, hot→cold migration via `copy_to_cold_and_remove()`
- `RoutineStore` — YAML-backed storage with in-memory cache
- `match_trigger_pattern()` — time-window matching for routine patterns
- `ContextIndexManager.index_routine()` — method exists but is never called
- Config knobs in `AlfredConfig` — `decay_migration_threshold`, `pattern_min_occurrences`, `pattern_min_days`, `pattern_confidence_threshold`, `routine_decay_per_cycle`, `routine_archive_threshold`, `routine_suggestion_cooldown_hours`

### Gaps Addressed

| Gap | Feature |
|-----|---------|
| `retrieval_count` and `last_retrieved` never persisted to hot store on recall | D4 |
| Decay formula simpler than spec (no log frequency, no exp recency) | D4 |
| No compression at cold migration | D4 |
| Routines not indexed in `idx:context` after detection | D3 |
| Archived routines not removed from context index | D3 |
| No suggestion flow — Conscious Engine never surfaces candidates to user | D3 |
| `last_suggested` field never written | D3 |
| Routines never promoted to `state="active"` with `ActionPayload` | D3 |
| No Trigger Engine integration for crystallized execution | D3 |

## Approach

Bottom-up (data-first): fix the data foundation, then build features on top. Each layer is solid before the next depends on it.

**Order:**
1. Retrieval stats persistence (foundation for decay)
2. Upgraded decay formula (uses real retrieval data)
3. Compression at cold migration (reduces cold store noise)
4. Routine indexing in context index (makes routines discoverable)
5. Suggestion flow in Conscious Engine (surfaces routines to user)
6. Proactive suggestion via notifications (surfaces routines when no conversation)
7. Trigger Engine → Reflex promotion (crystallized execution)

---

## 1. Retrieval Stats Persistence

### Problem

`EpisodicMemory.recall()` increments `retrieval_count` and sets `last_retrieved` in the returned Python objects but never writes them back to the Redis hot store. The decay formula operates on stale zeros.

### Design

Add an `update_metadata` method to the `VectorStore` ABC:

```python
async def update_metadata(
    self,
    id: str,
    fields: dict[str, str | float | int],
) -> None:
    """Update specific metadata fields in-place (no re-embedding)."""
    ...
```

In `RedisVectorStore`, implement as `HSET` on the entry's Redis hash key.

In `EpisodicMemory.recall()`, after building the results list, fire-and-forget updates for each hot-store result:

```python
for search_result, source_store in merged:
    if source_store == "hot":
        await self._hot.update_metadata(search_result.id, {
            "retrieval_count": search_result.metadata.retrieval_count + 1,
            "last_retrieved": now_timestamp,
        })
```

Only hot-store entries are updated — cold store entries are archival.

**Impact:** ~1-10 small Redis writes per recall. Negligible compared to the embedding + search cost.

---

## 2. Upgraded Decay Formula

### Problem

Current formula: `age_days * (1 - significance) * (1 / (retrieval_count + 1))` — a simple product that doesn't match the spec's subtractive model where high significance and frequent retrieval actively resist migration pressure.

### New Formula

```python
from math import exp, log2

age_factor = min(days_old / 30.0, 1.0)                     # linear, caps at 1.0
retrieval_recency = exp(-days_since_last_retrieved / 7.0)   # exponential decay
retrieval_frequency = min(log2(count + 1) / 5.0, 1.0)      # logarithmic, caps at 1.0

migration_pressure = (
    age_factor
    - significance * 2.0          # high significance resists
    - retrieval_recency * 1.5     # recent retrieval resists
    - retrieval_frequency * 1.0   # frequent retrieval resists
)
```

### Behavior Examples

| Entry | age_factor | significance resist | recency resist | frequency resist | pressure | Result |
|-------|-----------|-------------------|---------------|-----------------|----------|--------|
| 30d old, low sig (0.1), never retrieved | 1.0 | -0.2 | ~0 | 0 | ~0.8 | Migrates (threshold < 1.0) |
| 30d old, high sig (0.8), never retrieved | 1.0 | -1.6 | ~0 | 0 | -0.6 | Stays |
| 7d old, retrieved yesterday, count=3 | 0.23 | varies | -1.3 | -0.4 | deeply negative | Stays |
| 60d old, sig 0.3, last retrieved 30d ago, count=1 | 1.0 | -0.6 | -0.02 | -0.2 | ~0.18 | Borderline |

### Fallback for `last_retrieved=0`

Entries written before the stats fix will have `last_retrieved=0.0`. Treat `days_since_last_retrieved` as equal to `days_old` (worst case — no recency protection). This ensures pre-existing entries decay naturally rather than being artificially preserved.

### Configuration

`decay_migration_threshold` remains configurable in `AlfredConfig` (default `1.0`). The subtractive formula means the threshold can be lowered for more aggressive decay.

---

## 3. Compression at Cold Migration

### Problem

Cold storage accumulates individual entries. Five "kitchen light toggled" events from the same evening should be one summary.

### Design

After identifying entries that exceed the decay threshold in `_apply_decay()`:

1. **Group** decayed entries by `(shared_entity, date)` — same calendar day, at least one shared entity in their `entities` list. Entries with no entities are not grouped (migrated individually).

2. **Compress** groups with 2+ entries via a single LLM call per group:
   - Prompt: "Summarize these related home automation events into a single concise paragraph. Also provide a semantic_key (a short phrase for vector search)."
   - Input: timestamped summaries of all entries in the group
   - Output: `{ "summary": "...", "semantic_key": "..." }`

3. **Write** the summary as a new `EpisodicEntry` to cold storage:
   - `id`: new UUID
   - `entities`: union of all entries' entities
   - `significance`: max significance from the group
   - `timestamp`: earliest timestamp in the group
   - `source`: `"librarian"`
   - `semantic_key`: from LLM response
   - `retrieval_count`: sum of all entries' counts
   - `valence`: `"neutral"`

4. **Mark originals:** Write each original entry to cold with `compressed_into=<summary_id>` and `compressed="yes"` in `ContextMetadata`. Then delete from hot store.

5. **Single entries** (no group) are migrated individually as today — no compression overhead.

### Recall Behavior

- **Involuntary recall** (automatic): compressed originals are excluded via the existing `include_compressed` filter. The summary entry is searchable.
- **Deliberate recall** (`memory_recall_memories`): passes `include_compressed=True`, so originals are still accessible if the user asks for detail.

### LLM Cost

One call per entity-day group, only during consolidation (hourly at most). Typically a handful of groups per cycle.

---

## 4. Routine Indexing in Context Index

### Problem

`_detect_patterns()` saves routines to `RoutineStore` but never calls `context_index.index_routine()`. Routines are invisible to involuntary recall — the Conscious Engine never sees them unless the user explicitly uses the memory recall tool.

### Design

**On routine creation** (in `_detect_patterns()`, after `self._routines.save(candidate)`):

```python
content = (
    f"Routine ({candidate.state}): {candidate.name} — {candidate.trigger_pattern}. "
    f"Steps: {'; '.join(s.description for s in candidate.steps)}. "
    f"Confidence: {candidate.confidence:.2f}"
)
await self._context_index.index_routine(
    id=candidate.name,
    content=content,
    confidence=candidate.confidence,
)
```

**On archive** (in `_update_routine_lifecycle()`, when transitioning to `archived`):

```python
await self._context_index._store.delete(routine.name)
```

**On dormant:** Keep indexed — dormant routines may recover if the pattern resumes.

This lets involuntary recall surface routines when the user's message is semantically related (e.g., "it's getting dark" matches an evening lighting routine).

---

## 5. Suggestion Flow in Conscious Engine

### Problem

Candidate routines are detected and stored but never surfaced to the user. The fluid → crystallized lifecycle is broken at the suggestion step.

### Design — Two Paths

#### Path A: During Conversation (Involuntary Recall Enhancement)

In `ConsciousEngine.process()`, after involuntary recall and before the LLM call:

1. Load all `candidate` and `active` routines from `RoutineStore`
2. Filter to those whose `trigger_pattern` matches the current time window (via `match_trigger_pattern()`)
3. Filter by `last_suggested` cooldown: skip if suggested within `routine_suggestion_cooldown_hours` (default 24)
4. Append matching routines to the `relevant_context` list with a hint:
   ```
   [routine-suggestion] You've noticed a pattern: {name} ({trigger_pattern}).
   Steps: {steps}. Confidence: {confidence:.0%}.
   If appropriate, suggest this to sir and ask if they'd like Alfred to handle this automatically.
   ```
5. After the LLM response, update `last_suggested = now` on all surfaced routines via `RoutineStore.save()`

#### Path B: Proactive Notification (No Conversation Active)

New method `check_routine_suggestions()`:

1. Called periodically from the conscious process background loop (every 15 minutes)
2. Same logic: match `trigger_pattern` to current time, check `last_suggested` cooldown
3. Matching candidates → publish NORMAL-urgency notification via `NotificationPublisher`:
   > "I've noticed you usually {routine description} around {time}. Want me to start doing this automatically?"
4. Update `last_suggested` after publishing

Notifications flow through the existing dispatch infrastructure — Signal, WebSocket, and APNs adapters all deliver as normal. On iOS, this appears as a standard push notification.

### User Response Handling

| Response | Action |
|----------|--------|
| Accepts (in conversation) | Conscious Engine creates a trigger via `TriggerFeature.create_trigger()`, updates routine `state="active"` |
| Rejects | `state="archived"`, removed from context index |
| Ignores (no response after suggestion) | `confidence -= 0.05` per suggestion cycle; below `routine_archive_threshold` (default 0.3) → `state="archived"` |

---

## 6. Trigger Engine → Reflex Promotion (Crystallized Intelligence)

### Problem

When a user accepts a routine, it should become fully autonomous — no Conscious Engine involvement. This is the final step of the fluid → crystallized lifecycle (Pillar 5).

### Design

**Promotion (on user acceptance):**

1. Conscious Engine calls `TriggerFeature.create_trigger()` using the routine's `trigger_pattern` as the time condition and the steps as the `ActionPayload`
2. The trigger's metadata includes `routine_name` for lifecycle correlation
3. Routine `state` updated to `"active"`, `RoutineStep.action` fields populated with the `ActionPayload`

**Execution:**

1. Trigger Engine evaluates the trigger — when conditions match, it fires
2. Since `action` is set on the trigger, it emits an `ActionRequest` directly to `ACTIONS_STREAM` (existing behavior for triggers with actions)
3. Domain agent (e.g., `HomeAgent`) receives the `ActionRequest` and executes via the microservice
4. No SLM reasoning needed — true <500ms crystallized execution

**Full Lifecycle:**

```
Scratchpad observations
  → Librarian _detect_patterns() — LLM identifies repeated behavior
  → RoutineSpec(state="candidate") saved + indexed
  → Conscious Engine surfaces suggestion (conversation or notification)
  → User accepts
  → TriggerFeature.create_trigger() — real trigger created
  → Trigger Engine fires at pattern time
  → ActionRequest → Domain Agent executes
  → No reasoning, no LLM — pure crystallized execution
```

No new infrastructure needed — the entire promotion path composes existing primitives (triggers, actions, domain agents). This is Pillar 5 in action.

---

## Backlog Items

### Created by This Spec

| Priority | ID | Description |
|----------|----|-------------|
| Medium | B1 | Actionable notification responses — accept/reject routine suggestions inline from push notifications (Signal, WebSocket, APNs) without opening the app |
| High | B2 | APNs credential setup and end-to-end testing — configure Apple Push certificates/keys in Secrets Manager, validate sandbox → production |

### Files to Create

- `docs/backlog/medium/actionable-notification-responses.md`
- `docs/backlog/high/apns-credential-setup.md`

## QA Backlog Items

### Files to Create

- `docs/qa-backlog/routine-suggestion-push-notification-ios.md` — Verify routine suggestion delivered as push notification on real iOS device
- `docs/qa-backlog/routine-suggestion-tap-to-respond.md` — Tap push notification → open app → accept/reject in chat
- `docs/qa-backlog/notification-delivery-backgrounded-ios.md` — Notification delivery when iOS app is backgrounded/killed
- `docs/qa-backlog/dnd-respects-ios-notifications.md` — DND defers routine suggestion notifications on iOS

## Testing Strategy

### Unit Tests

- `test_recall_persists_retrieval_stats` — verify `update_metadata` called with correct counts after recall
- `test_decay_formula_*` — parametrized tests for the new subtractive formula (high sig stays, old low sig migrates, recently retrieved stays, etc.)
- `test_compression_groups_by_entity_date` — verify grouping logic
- `test_compression_llm_summarization` — mock LLM, verify summary entry creation and `compressed_into` marking
- `test_compression_single_entry_no_grouping` — lone entries migrate without compression
- `test_routine_indexed_on_detection` — verify `index_routine()` called after pattern save
- `test_routine_removed_on_archive` — verify context index deletion
- `test_suggestion_flow_path_a` — routine surfaced in relevant_context during conversation
- `test_suggestion_cooldown` — routine not re-suggested within cooldown window
- `test_suggestion_acceptance_creates_trigger` — verify trigger creation + state="active"
- `test_suggestion_rejection_archives` — verify state="archived" + index removal
- `test_suggestion_ignore_confidence_decay` — confidence decrements, archives at threshold
- `test_proactive_suggestion_publishes_notification` — verify NotificationPublisher called
- `test_promoted_routine_trigger_fires` — trigger evaluates and emits ActionRequest

### Integration Tests

- Full consolidation cycle: scratchpad → episodic → pattern detection → routine indexed → suggestion surfaced
- Decay cycle with real Redis: write entries, recall some, run decay, verify migration counts
- Compression with mocked LLM: verify cold store contains summary + marked originals

---

## Files Modified

| File | Change |
|------|--------|
| `core/memory/vector_store.py` | Add `update_metadata` abstract method |
| `core/memory/redis_vector_store.py` | Implement `update_metadata` (HSET) |
| `core/memory/sqlite_vec_store.py` | Implement `update_metadata` as no-op (cold store entries don't need retrieval tracking) |
| `core/memory/episodic/memory.py` | Persist retrieval stats in `recall()` |
| `core/librarian/consolidator.py` | Upgrade decay formula, add compression, index routines, remove archived from index |
| `core/conscious/engine.py` | Add routine suggestion check in `process()`, add `check_routine_suggestions()` |
| `core/conscious/context_assembler.py` | No changes — routine suggestions go into `relevant_context` list |
| `core/memory/schemas.py` | No changes — all fields already exist |
| `core/memory/vector_store.py` | Add `update_metadata` to ABC |
| `shared/config.py` | Verify all config knobs exist (they should from PR #15) |

## Files Created

- `docs/backlog/medium/actionable-notification-responses.md`
- `docs/backlog/high/apns-credential-setup.md`
- `docs/qa-backlog/routine-suggestion-push-notification-ios.md`
- `docs/qa-backlog/routine-suggestion-tap-to-respond.md`
- `docs/qa-backlog/notification-delivery-backgrounded-ios.md`
- `docs/qa-backlog/dnd-respects-ios-notifications.md`
