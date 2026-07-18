# README Screenshots + Demo GIF

## Summary

Add visual proof to the README: 2–3 screenshots of the Mission Control SPA
(chat, system/telemetry page, settings with integration cards) and one short
GIF of a marquee flow. Today the README is text + mermaid only.

## Context / Motivation

Launch-readiness assessment (2026-07-18): the SPA is genuinely
screenshot-worthy and visual assets are the highest-leverage cheap marketing
artifact — they anchor the repo page, launch posts, and any future landing
page. Candidate GIF flows already proven live: sovereign-service credential
flow (service self-describes → card appears → credentials heal on restart,
demoed 2026-07-17) or sensor→notification-in-~1s (verified live in PR #22).

## Acceptance Criteria

- [ ] `docs/media/` (or similar) with optimized screenshots (light or dark,
      consistent theme; no real credentials/PII in frame).
- [ ] One GIF or short MP4 (<15s) of a marquee flow, embedded near the top of
      the README.
- [ ] README hero section shows at least one visual above the fold.
- [ ] Assets reproducible: a note documenting how each was captured (page,
      viewport, seed data) so they can be refreshed after UI changes.
