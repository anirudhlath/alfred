# D8: System 2 Observation of System 1 Actions

## Overview

The Reflex Engine (System 1) and Conscious Engine (System 2) currently operate in complete isolation. Reflex processes home automation events and executes actions, but the Conscious Engine has zero visibility into what Reflex did. D8 adds an observation pipeline so that System 2 can recall Reflex actions and the Librarian can detect patterns in reflexive behavior.

### Scope

- **In scope:** Passive awareness (recall Reflex actions when relevant), active evaluation (Librarian detects patterns in Reflex behavior)
- **Out of scope:** Real-time override of Reflex actions (deferred — low value given current Reflex scope, and the observation stream provides the foundation if needed later)

### Brain Analogy

The architecture mirrors how the human brain handles reflexive vs conscious processing:

| Alfred Component | Brain Analogue | Role |
|---|---|---|
| Reflex Engine | Basal ganglia + amygdala | Fast, reflexive responses — acts without consulting the cortex |
| Conscious Engine | Prefrontal cortex | Accesses *memories* of what happened, doesn't monitor reflexes in real-time |
| Memory Ingestor | Hippocampus | Encodes all experiences into episodic memory regardless of origin |
| Librarian | Sleep consolidation | Replays episodic memories, detects patterns, strengthens important ones |

Key principle: System 2 does NOT have a direct wire from System 1. It accesses reflex outcomes through **memory**. The Memory Ingestor is the bridge.

---

## 1. Event Schema

New Pydantic model in `bus/schemas/events.py`:

```python
class ReflexObservation(BaseEvent):
    observation_id: str  # auto-generated UUID
    origin: Literal["state_change", "trigger_fired"]  # "origin" not "source" — BaseEvent already has source
    trigger_event: dict[str, Any]  # originating event payload
    action: ActionRequest
    result: ActionResult
    decision_context: str | None = None  # SLM reasoning (if available)
```

Note: inherits from `BaseEvent` (gets `event_id`, `timestamp`, `source` for free). The field is named `origin` (not `source`) to avoid collision with `BaseEvent.source`.

Published by the Reflex Engine after every action execution. Carries the full context: what triggered the action, what action was taken, the outcome, and optionally the SLM's reasoning.

---

## 2. Observation Stream

New Redis stream constant in `shared/streams.py`:

```python
REFLEX_OBSERVATIONS_STREAM = "alfred:reflex:observations"
```

### Publishing

Reflex publishes a `ReflexObservation` to this stream in two code paths:

1. **`core/reflex/runner.py`** — after processing `HOME_STATE_STREAM` events (state changes)
2. **`core/reflex/__main__.py`** — after handling `TriggerFired` events

In both cases, the current plain-text `lpush` to `SCRATCHPAD_QUEUE` is **replaced** with a structured `xadd` to `REFLEX_OBSERVATIONS_STREAM`. The Conscious Engine's own scratchpad writes remain unchanged.

### Note on Reflex Input Generalization

The Reflex Engine is currently hardwired to `HOME_STATE_STREAM` — it only handles home automation events. For a general ambient system, Reflex should accept state changes from any domain. This is a separate architectural concern tracked in `docs/backlog/` and does not affect D8. The observation pipeline is downstream of action execution, so it will carry over naturally when Reflex inputs are generalized.

---

## 3. Memory Ingestor

New module: `core/memory/ingestor.py`

A lightweight async consumer that reads `REFLEX_OBSERVATIONS_STREAM` and writes structured entries to episodic memory.

### Responsibilities

- Consumer group: `memory-ingestor`
- For each `ReflexObservation`:
  1. Compute a text summary for embedding (e.g., `"[reflex] motion in hallway → turn_on light.hallway → success"`)
  2. Call `EpisodicMemory.store()` with structured metadata:
     - `source="reflex"`
     - `tool_name`, `parameters`, `result_status`
     - `decision_context` (if present)
  3. `SignificanceScorer` scores the entry as usual — routine Reflex actions score low (correct behavior)

### Lifecycle

- Started by the unified runner (`runner/__main__.py`) as a background task alongside other services
- Processes events as they arrive (no batching) — observations are immediately searchable in episodic memory
- Uses `ensure_consumer_group` for idempotent group creation (existing pattern)

### Single Responsibility

The Memory Ingestor lives in `core/memory/` because it is part of the memory system, not the Conscious Engine. The Conscious Engine does not consume the observation stream directly — it accesses Reflex actions through normal episodic memory search.

---

## 4. Passive Awareness (Involuntary Recall)

**No code changes required.**

Context assembly already performs semantic search on episodic memory during involuntary recall. Since the Memory Ingestor stores Reflex observations with proper embeddings and metadata, they surface naturally when relevant.

Example flow:
1. User asks "what happened while I was asleep?"
2. Context assembly runs episodic search → matches Reflex observations from overnight
3. Conscious Engine includes them in its response

### Volume Management

High-frequency Reflex actions (motion sensors firing repeatedly) could flood episodic memory. Existing mechanisms handle this:
- `SignificanceScorer` assigns low scores to routine repeated actions
- Librarian consolidation applies contextual decay — low-significance entries decay faster
- No special handling needed beyond what exists

---

## 5. Active Evaluation (Librarian Enhancement)

The Librarian's existing two-call consolidation pipeline (analysis → consolidation) receives a targeted prompt addition.

### What the Librarian Looks For

When processing episodic entries tagged `source="reflex"`:

- **Repeated patterns** — same trigger → same action happening frequently (e.g., cat motion → kitchen lights 4x/week at 3 AM)
- **Questionable decisions** — actions that seem mismatched to context (e.g., turning on all lights for brief motion)
- **Routine candidates** — patterns stable enough to promote to procedural memory

### Output (Existing Types)

No new output types — uses existing Librarian capabilities:
- Semantic insight written to profile (e.g., "cat frequently triggers kitchen motion sensor at night")
- Routine candidate with lifecycle metadata
- Decay recommendation for low-value repetitive observations

### Implementation

Prompt tweak to the consolidation call. No new infrastructure, no additional LLM calls. The Librarian already has pattern detection, conflict resolution, and routine lifecycle machinery.

---

## 6. Data Flow Summary

```
Before D8:
  Reflex → SCRATCHPAD_QUEUE (plain text) → ScratchpadWriter → scratchpad.md → Librarian → Episodic

After D8:
  Reflex → REFLEX_OBSERVATIONS_STREAM (structured) → Memory Ingestor → EpisodicMemory.store()
                                                                              ↓
                                                          Conscious Engine reads via episodic search
                                                          Librarian consolidates patterns during cycle
```

---

## 7. Testing Strategy

- **Unit: ReflexObservation schema** — validation, serialization, edge cases (missing decision_context)
- **Unit: Memory Ingestor** — mock Redis stream + mock EpisodicMemory, verify `store()` called with correct metadata and embedding text
- **Unit: Reflex Engine** — verify `ReflexObservation` published to `REFLEX_OBSERVATIONS_STREAM` (replaces scratchpad write assertions)
- **Integration: end-to-end pipeline** — Reflex processes state change → observation lands in stream → Ingestor stores to episodic → episodic search returns it
- **Librarian: pattern detection** — verify consolidation prompt analyzes `source="reflex"` entries, produces semantic insights or routine candidates

Existing test fixtures cover the memory side: `mock_embedder`, `mock_vector_store` from root `conftest.py`.

---

## 8. Files Changed

| File | Change |
|---|---|
| `bus/schemas/events.py` | Add `ReflexObservation` model |
| `shared/streams.py` | Add `REFLEX_OBSERVATIONS_STREAM` constant |
| `core/reflex/runner.py` | Replace scratchpad write with observation stream publish |
| `core/reflex/__main__.py` | Replace scratchpad write with observation stream publish (TriggerFired path) |
| `core/memory/ingestor.py` | New — Memory Ingestor consumer |
| `core/memory/__init__.py` | Export ingestor if needed |
| `runner/__main__.py` | Add Memory Ingestor as background task |
| `core/librarian/consolidator.py` | Add Reflex pattern analysis to consolidation prompt |
| `docs/backlog/medium/d8-system2-observation-system1.md` | Update or close |
| `docs/backlog/*/reflex-input-generalization.md` | New backlog ticket for generalizing Reflex inputs |
