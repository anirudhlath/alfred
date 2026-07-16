# Satellite Multi-Turn Hands-Free Follow-Up

## Summary

After Alfred replies on a satellite, keep the mic open briefly so the user can follow up
("...and the bedroom too") without repeating the wake word.

## Context / Motivation

Deferred from the v1 voice satellite design
(`docs/superpowers/specs/2026-07-15-voice-satellite-design.md` §9). This is a large part
of what makes commercial assistants feel conversational. Requires a re-listen window after
TTS playback, VAD-based silence bail-out, and session continuity in the bridge (the
SessionManager already keys sessions — the bridge must reuse the session across turns).

## Acceptance Criteria

- After a reply finishes playing, the satellite listens for a short window (~5s,
  configurable) without requiring the wake word; an LED/earcon indicates listening.
- Silence during the window returns the satellite to idle without a spurious request.
- Follow-up utterances land in the same Conscious Engine session (context carries over).
- No always-on streaming: the window is bounded and event-driven, not polling.
