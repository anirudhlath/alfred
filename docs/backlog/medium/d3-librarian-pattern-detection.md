# D3: Librarian Pattern Detection

## Summary
Detect repeated patterns in episodic memory and promote to procedural memory with full lifecycle.

## Status
Partly built. No code ever sets a routine to `active` or turns one into a trigger, so the
candidate → active step never happens (`core/memory/schemas.py:72` declares the state;
nothing assigns it). D31, D32, D35 and actionable notification responses wait on it.
The rest landed in the D3+D4 PR. See spec: `docs/superpowers/specs/2026-04-16-d3-d4-pattern-detection-decay-design.md`

## What Was Built
- Pattern detection via LLM (already existed from PR #15)
- Routine indexing in `idx:context` for involuntary recall
- Suggestion flow: conversation hints + proactive notifications
- Confidence decay on ignored suggestions
- Archive removes from context index
- ~~Trigger Engine promotion for crystallized execution~~ — not built: see Status
