# agentskills.io Interop for Crystallized Routines

## Summary
Add an export (and optionally import) path between Alfred's RoutineSpec YAML and the agentskills.io open skill-document standard, so crystallized routines can interoperate with the broader agent-skills ecosystem.

## Context
Hermes Agent stores its learned procedures as skill documents compatible with the agentskills.io standard, which is emerging as a shared format across agent frameworks (searchable, shareable, versioned). Alfred's procedural memory (RoutineSpec YAML) is functionally equivalent — trigger conditions + composed actions learned from patterns — but in a private format.

Interop would let Alfred:
- Publish anonymized/scrubbed routines as shareable skill docs
- Import community skill docs as *routine suggestions* (entering the normal suggestion → acceptance flow, never auto-promoted — imported skills must not bypass the fluid→crystallized lifecycle or Pillar 3's deterministic schemas)

This is ecosystem interop, not core intelligence — low priority per the foundational-focus philosophy.

## Acceptance Criteria
- Review the agentskills.io spec and document the mapping RoutineSpec ↔ skill doc (fields that translate, fields that don't)
- `export` converts a RoutineSpec to a valid agentskills.io skill document, stripping PII (entity IDs, names, locations) behind an explicit allowlist
- (Optional) `import` converts a skill doc into a RoutineSpec in *suggested* status with low initial confidence
- Round-trip test: export → import preserves trigger conditions and action semantics
- Imported routines pass Pydantic validation and go through the standard suggestion lifecycle

## Dependencies
- RoutineSpec lifecycle (suggested/active/archived) — already built
- Routine self-iteration ticket (nice-to-have: outcome history could inform exported skill quality notes)
