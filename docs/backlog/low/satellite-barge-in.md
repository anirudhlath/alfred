# Satellite Barge-In

## Summary

Allow the user to interrupt Alfred while a reply or announcement is playing by saying the
wake word ("Hey Alfred, stop").

## Context / Motivation

Deferred from the v1 voice satellite design
(`docs/superpowers/specs/2026-07-15-voice-satellite-design.md` §9). Requires wake word
detection to stay active during playback and acoustic echo cancellation good enough that
the satellite doesn't wake itself with its own speaker output — the ReSpeaker 2-Mic HAT
has no hardware AEC, so this may need software AEC or a hardware revisit.

## Acceptance Criteria

- Saying "Hey Alfred" during TTS playback stops playback and opens a listening session.
- The satellite does not self-trigger from its own speaker output (validated with the
  wake word spoken *by* Alfred's own TTS voice as a worst case).
- Works for both reply playback and proactive announcements.
