# Satellite Music / Media Playback

## Summary

Play music and long-form audio (podcasts, radio) on satellites — "Hey Alfred, play some
jazz in the kitchen."

## Context / Motivation

Deferred from the v1 voice satellite design
(`docs/superpowers/specs/2026-07-15-voice-satellite-design.md` §9). The v1 audio path is
short spoken replies; music needs continuous streaming, volume/skip/stop controls, a media
source integration (adapter pattern — e.g. Music Assistant, Spotify, local library), and
likely better speakers. Evaluate delegating playback to an existing media stack (Music
Assistant + Squeezelite/snapcast on the Pi) rather than streaming through the bridge.

## Acceptance Criteria

- Voice-initiated playback of a named artist/genre/playlist on the requesting satellite.
- Volume, pause/resume, stop, and skip by voice.
- Wake word and announcements still work during playback (playback ducks under TTS).
- Media source is an adapter behind a registry — no hardcoded provider.
