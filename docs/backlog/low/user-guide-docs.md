# User Guide — Day-to-Day Documentation Set

## Summary

Documentation for USING Alfred (resident persona), not building it. All 13
existing docs are contributor docs; zero cover daily use.

## Context / Motivation

Launch-readiness assessment (2026-07-18). The PRD's Capability Catalog
(docs/PRD.md §4) is the natural index: each user-facing capability row should
eventually link to a how-to. Without this, every capability is discoverable
only by reading source or asking the developer.

## Acceptance Criteria

- [ ] `docs/guide/` (or similar) covering at minimum: talking to Alfred
      (channels: web, iOS, Signal, voice satellites), reminders and triggers
      ("tell me when the dryer finishes"), what the Settings pages mean
      (integrations, voice enrollment, devices), guest access model,
      notifications and DND behavior, and a troubleshooting page.
- [ ] Written for the resident persona — no stream names, consumer groups, or
      internal component names in prose.
- [ ] PRD Capability Catalog rows link to their guide pages (per the existing
      "keep the PRD current" design principle).
- [ ] Sequence after HA Plans 2–3 so home-control and confirmation flows are
      documented once, correctly.
