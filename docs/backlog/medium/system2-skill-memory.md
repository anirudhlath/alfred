# System 2 Skill Memory (Librarian-Curated Method Documents)

## Summary
Add a fourth flavor of memory: skill documents — crystallized *methods* for the Conscious Engine ("how I structure the morning briefing", "what to check before suggesting a routine"), authored and revised by the Librarian when it detects the Conscious Engine solving the same kind of problem repeatedly.

## Context
Alfred crystallizes *automations* (RoutineSpec YAML → System 1 execution) but has no way to persist *reasoning methods* for System 2 — semantic memory holds facts and preferences, not procedures. Hermes Agent's skill documents showed the value of prompt-space procedural memory; the Alfred-native framing is completing the fluid→crystallized lifecycle at the reasoning tier, not just the reflex tier.

Design constraints:
- **Librarian-curated first** — skills are promoted from detected patterns in Conscious Engine problem-solving (scratchpad/episodic history), same lifecycle as routines (suggested → active → archived, confidence-scored). Hand-authored skills are an escape hatch, not the primary mechanism.
- Pillar 3 is not violated: skills are single-agent reasoning context, never inter-agent messages.
- Skills are *methods* (natural-language guidance for System 2); routines are *automations* (deterministic YAML for System 1). A skill may reference primitives to compose but never encodes hardcoded IF/THEN — that's what routines are for.
- Retrieval should reuse the involuntary/deliberate recall model: index skills in `idx:context` (like routines) so relevant skills surface via semantic search during context assembly, plus a deliberate `recall_skills` path if needed.

## Acceptance Criteria
- `SkillSpec` model (Markdown body + YAML frontmatter: name, description, confidence, lifecycle status, usage stats) stored under procedural memory alongside routines
- Librarian consolidation detects repeated System 2 solution patterns and drafts skill documents (parallel to routine pattern detection)
- Skills indexed in `idx:context` with `type="skill"`; relevant skills surface in Conscious Engine context assembly via existing two-stage recall
- Skill lifecycle mirrors routines: suggestion → user-visible acceptance or confidence-based promotion, decay on non-use, archival
- Usage tracking: when a skill was in context for a request, record it (feeds Librarian revision decisions)
- Hand-authored skills can be dropped into the skills directory and get indexed on consolidation
- Eval scenario: a task the Conscious Engine solves better with a relevant skill in context than without
- `docs/skill-memory.md` per the document-new-features convention

## Dependencies
- Librarian consolidation pipeline + routine lifecycle — already built (reuse the promotion/decay machinery)
- ContextIndexManager — already built
- Relates to: `medium/routine-self-iteration-from-outcomes.md` (same outcome-driven revision idea, applied to skills), `low/agentskills-routine-interop.md` (skill docs are the natural agentskills.io export unit)
