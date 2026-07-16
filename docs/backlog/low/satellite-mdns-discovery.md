# Satellite mDNS Auto-Discovery

## Summary

Discover Wyoming satellites on the network via zeroconf/mDNS instead of (or in addition
to) the static `config/satellites.yaml`.

## Context / Motivation

Deferred from the v1 voice satellite design
(`docs/superpowers/specs/2026-07-15-voice-satellite-design.md` §9). Static YAML is fine
for a handful of devices but manual. `wyoming-satellite` advertises itself via zeroconf
(this is how Home Assistant finds satellites), so discovery is mostly client-side work in
the bridge plus an admin UI flow to assign a discovered device to an HA area.

## Acceptance Criteria

- A new satellite appearing on the trusted network shows up in the admin UI (Mission
  Control) as unassigned.
- Assigning it a name + area persists it (single source of truth stays consistent with
  the YAML/Redis config pattern used by TriggerStore).
- Removal/offline devices are reflected without polling loops.
