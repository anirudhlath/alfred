# Presence-Aware Announcement Targeting

## Summary

Deliver spoken notifications only to the satellite(s) in rooms where someone actually is,
instead of the v1 broadcast-to-all policy.

## Context / Motivation

Deferred from the v1 voice satellite design
(`docs/superpowers/specs/2026-07-15-voice-satellite-design.md` §9). Per the fluid
intelligence pillar this must **compose from existing primitives** — HA motion/presence
events already on the bus, plus last-satellite-interaction recency — rather than adding a
hardcoded presence subsystem. A room-occupancy signal is also useful beyond announcements
(context assembly, Reflex).

## Acceptance Criteria

- An URGENT spoken notification plays only in room(s) with recent presence signal; if no
  presence signal exists, falls back to broadcast (never silently drops).
- Presence inference uses existing bus events (HA motion, satellite interactions) — no new
  polling loops and no bespoke presence service.
- Targeting policy is observable in logs (which rooms, why).
