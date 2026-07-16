# Voice-Driven Speaker Enrollment

## Summary

Enroll a new voiceprint entirely by voice — "Hey Alfred, learn my voice" — instead of the
web Settings page flow.

## Context / Motivation

Deferred from the v1 voice satellite design
(`docs/superpowers/specs/2026-07-15-voice-satellite-design.md` §9). v1 enrollment records
samples via the web app's mic. A voice-driven flow is friendlier for household members who
never open the web app, but needs a guided multi-step dialogue (prompt → capture N samples
→ confirm identity label) and an identity/authorization story for who may enroll whom.

## Acceptance Criteria

- A guided spoken flow captures enough samples to enroll a usable voiceprint via the
  existing `SpeakerID.enroll()` API.
- The flow confirms the identity label verbally before committing.
- Enrollment from an untrusted/unknown speaker cannot overwrite an existing enrolled
  identity without confirmation from an already-enrolled user.
