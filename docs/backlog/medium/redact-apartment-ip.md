# Redact Real Apartment HA IP from Docs & History

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** medium
**Severity (audit):** medium
**Source:** Public-release readiness audit 2026-07-18 (findings #9)

> 🔒 **Sensitive:** describes a live exposure on an already-public repo — do NOT file as a public GitHub issue until remediated.

## Summary
The owner's live apartment Home Assistant LAN address, `192.168.50.159:8123`, is
committed to tracked docs and recent git history on `master`, explicitly labeled as the
real apartment HA. Because `anirudhlath/alfred` is **already public** (v0.1.0 shipped
2026-07-16), this is a **post-exposure** cleanup, not pre-release prevention — the IP is
already fetchable from published revisions, so redacting the working tree alone leaves it
recoverable from history. It is an RFC1918 private-range address (not internet-routable),
which is why this rates medium rather than high; it still leaks the owner's internal
network topology.

## Context / Motivation
The IP appears ~20 times across three tracked markdown docs, labeled as real: the spec
says *"connects to the real apartment HA (http://192.168.50.159:8123)"* and plan2's manual
QA steps say *"Real apartment Home Assistant reachable (e.g. http://192.168.50.159:8123)"*.
A per-revision history scan shows the spec carries the IP in **~82 revisions** and plan1 in
**~80 revisions**, all since 2026-07-15 — so a working-tree-only edit is insufficient.

Locations flagged (LOC):
- `docs/superpowers/specs/2026-07-15-real-home-ha-integration-design.md` (spec, ~82 revisions; history commit `ed70384` + `multiple-revisions-since-2026-07-15`)
- `docs/superpowers/plans/2026-07-15-ha-plan1-sovereign-credentials.md` (plan1, ~80 revisions)
- `docs/superpowers/plans/2026-07-15-ha-plan2-home-service-rewrite.md` (plan2, manual QA step)
- `docs/superpowers/plans/2026-07-15-ha-plan3-attention-autonomy.md`
- `docs/superpowers/plans/2026-07-16-voice-satellite-bridge-plan.md`
- `tests/core/channels/test_service_credentials.py` (also in history commit `f6321eb`)
- `tests/core/channels/test_service_integrations_api.py`

The D describes ~20 occurrences concentrated in the three core markdown docs; the LOC list
additionally flags the two test files and two more plan docs, so the redaction sweep must
cover every listed location, not just the three.

## Acceptance Criteria
- [ ] Replace all occurrences of `192.168.50.159` with a placeholder (e.g. `http://homeassistant.local:8123` or `192.0.2.10`) across the three core docs (spec, plan1, plan2) in the working tree.
- [ ] Replace the IP in the remaining flagged locations: plan3, the voice-satellite-bridge plan, `tests/core/channels/test_service_credentials.py`, and `tests/core/channels/test_service_integrations_api.py`.
- [ ] `git grep '192.168.50.159'` on the working tree returns zero matches.
- [ ] Purge the IP from git history — since it entered only in the last ~3 days of commits (all since 2026-07-15), rewrite via `git filter-repo` (or squash/rewrite the affected recent commits) so it no longer appears in `git log -p -S '192.168.50.159'`.
- [ ] Because the repo is already public, treat as coordinated disclosure: complete the redaction + history rewrite and force-push before this ticket is referenced from any public GitHub issue.
