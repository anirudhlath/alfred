# D31: Routine-Aware Contextual Action Composition

## Summary
When a user's message implies a context that overlaps with known routines, Alfred should compose actions from those routines proactively — without being explicitly asked.

## Context
If a user says "I'm heading to bed" and Alfred knows about `evening_dim`, `lock_front_door`, and a preferred bedroom temperature, he should compose: *"Very well, sir. I've locked the front door and dimmed the living room lights. The bedroom is at 68 degrees."*

This is Pillar 5 (Fluid Intelligence) — composing primitives based on learned knowledge. Alfred doesn't need a hardcoded "bedtime routine" tool. He has individual capabilities and knowledge about when they co-occur. He composes them.

## Acceptance Criteria
- When active/promoted routines exist, the Conscious Engine's system prompt includes them as available context (via involuntary recall, not static injection)
- The LLM can reason about whether the user's message implies a routine context and decide to execute relevant actions
- Actions are composed from existing tools (dim_lights, lock_door, set_temperature) — no new hardcoded composite tools
- Alfred reports what he did after executing: *"I've taken the liberty of..."*
- If unsure, Alfred asks rather than acts: *"Shall I lock the front door as well?"*

## Dependencies
- D29 (routine reindex) — routines must be in `idx:context` for involuntary recall
- Active/promoted routines with sufficient confidence
