# Multi-User Voice Identity End-to-End

## Summary

A recognized non-"sir" voiceprint is currently downgraded to guest: the satellite pipeline
passes `identity_claim=match.identity` (e.g. `guest_bob`) with its confidence, but
`IdentityGate.resolve`'s satellite branch only special-cases `identity_claim == "sir"` —
any other enrolled identity falls through to plain guest, discarding name and confidence.

## Context / Motivation

Deliberate v1 scope (final review 2026-07, whole-branch Minor #2): the enrollment API
accepts any `[a-z0-9_-]+` identity, but the web enrollment card hardcodes `"sir"`, so
non-sir voiceprints can only be created via direct API calls today. When household members
get their own enrollments, the gate, context assembly ("You are speaking with …"),
and preference scoping all need to understand named non-sir identities. Related: add
voiceprint management (`delete`/`list` methods on `SpeakerID` plus endpoints — earlier
drafts carried these methods, removed as dead code pending this work). Also add the
untested IdentityGate satellite edges (confidence + non-sir claim; authenticated
satellite) as tests.

## Acceptance Criteria

- `IdentityGate` resolves an enrolled non-sir voiceprint to that identity with its
  voice-ID confidence (policy for risk_clearance decided and documented).
- Enrollment UI supports choosing/naming an identity; voiceprint list + delete exposed
  (uses `SpeakerID.enrolled_identities()` / `delete()`).
- Conscious context reflects the named speaker without leaking sir's personal data to
  non-sir identities.
- Tests cover: non-sir match end-to-end, confidence+non-sir claim, authenticated satellite.
