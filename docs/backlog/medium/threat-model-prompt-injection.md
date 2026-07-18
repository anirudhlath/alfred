# Document Threat Model & Prompt-Injection Defense for Tool-Enabled LLM

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** medium
**Severity (audit):** medium
**Source:** Public-release readiness audit 2026-07-18 (findings #15)

## Summary
The Conscious Engine feeds untrusted external content into the LLM context and then
executes whatever tool the model calls, including home-domain effectors and calendar
CRUD, with no documented threat model and only a trivially bypassable regex denylist as
"defense." A malicious calendar invite, weather payload, or guest Signal message can
therefore steer the model into acting on the home. Because `anirudhlath/alfred` is
**already public** (v0.1.0 shipped 2026-07-16), this is post-exposure hardening of a
live attack surface, not a pre-release cleanup — the tool-wired LLM path is already
reachable in the published tree.

## Context / Motivation
- `core/conscious/engine.py` (engine.py:400-468, 624-658) assembles LLM context from
  untrusted external sources — calendar event titles/descriptions, weather payloads,
  and guest Signal message bodies — and then executes any tool the model calls,
  including home-domain effectors and calendar CRUD.
- The only injection defense is `core/integrations/sanitizer.py` (sanitizer.py:13-47):
  a regex denylist of ~11 English phrases (`'ignore previous instructions'`, etc.). It
  is trivially bypassed (obfuscation and other evasions) and is a speed bump, not a
  control.
- Scope gap: the sanitizer is applied to **integration response data only**. Untrusted
  content that does not arrive as an integration response — e.g. guest Signal message
  bodies — is not filtered by it at all.
- Severity rationale: an LLM with authority over home control, calendar CRUD, and
  integrations, driven partly by attacker-controllable text, with no documented trust
  boundaries and no confirmation step on high-impact actions.

## Acceptance Criteria
- [ ] A `SECURITY.md` (or dedicated threat-model doc) exists documenting the
  prompt-injection threat model and trust boundaries — enumerating the untrusted content
  sources (calendar event titles/descriptions, weather payloads, guest Signal message
  bodies) and the high-impact tool surfaces they can reach (home-domain effectors,
  calendar CRUD).
- [ ] Irreversible / high-impact tool calls require explicit user confirmation when the
  triggering content is untrusted.
- [ ] The `core/integrations/sanitizer.py` regex denylist is documented and treated as a
  speed bump, not relied on as the primary prompt-injection control.
