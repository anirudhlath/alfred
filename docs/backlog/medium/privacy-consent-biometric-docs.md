# Add Privacy/Consent/Biometric Docs (GDPR/BIPA) + Voiceprint Deletion

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** medium
**Severity (audit):** medium
**Source:** Public-release readiness audit 2026-07-18 (findings #40, #68)

## Summary
Alfred has **zero** privacy, consent, or biometric-data documentation despite being an
always-on household audio system that enrolls and stores biometric voiceprints. A `git grep`
across all tracked `*.md` in `alfred`, `alfred-ios`, and `home-service` finds no data-protection
sense of GDPR, BIPA, data-retention, or consent/recording disclosure anywhere. `anirudhlath/alfred`
is **already public** (v0.1.0 shipped 2026-07-16), so this is post-exposure, not pre-release; but
the voiceprint feature itself lives on the unmerged `feature/voice-satellite-bridge` branch (PR #29),
so the docs and a deletion path should land **before** that branch reaches public master. Compounding
it, the branch has no code path — endpoint, UI, or API — to list or delete an enrolled voiceprint.

## Context / Motivation
**Finding #40 — missing privacy/consent/biometric docs (medium).** Confirmed the expected zero-hit
result: `git grep` across all tracked `*.md` in `alfred` (both `master` and
`origin/feature/voice-satellite-bridge`), `alfred-ios`, and `home-service` finds **no** occurrence of
GDPR, BIPA, data-retention, or consent/recording disclosure in the data-protection sense. The only
matches are unrelated: `biometric` hits are Face ID / passkey QA steps; `privacy` hits are the
internal guest-boundary eval (`PrivacyLeakScore`); `recording` hits are QA test-step phrasing. Yet
PR #29 ships SpeakerID processing ECAPA-TDNN voiceprints on always-listening wake-word satellite
devices. LOC: `alfred/README.md`, `alfred/docs/` (all tracked `*.md`), `alfred-ios/README.md`,
`home-service/` (all tracked `*.md`).

**Finding #68 — no voiceprint deletion/reset path (low).** Enrollment stores **only** a
mean-normalized 192-dim float32 embedding in the Redis hash `alfred:identity:voiceprint`
(`shared/streams.py`); raw enrollment audio is decoded to PCM in memory, embedded, and discarded.
Scope/mitigation: nothing hits disk or tracked paths, so embeddings **cannot leak into git via normal
operation**. However, commit `e008c04` removed `SpeakerID.delete()` and `enrolled_identities()` as
"dead API surface", so on the branch there is no endpoint, no UI, and no code path to list or delete a
voiceprint — the only removal mechanism left is a manual `redis HDEL`. LOC:
`core/voice/speaker_id.py`, `core/channels/web_server.py:602-618` (`/api/voice/enroll`), and the
existing backlog item `docs/backlog/low/satellite-multi-user-voice-identity.md` — all on
`origin/feature/voice-satellite-bridge`.

FIX guidance: for #40, before the branch goes public add a PRIVACY section to the README (or a
`PRIVACY.md`) stating (1) speaker enrollment creates biometric voiceprint embeddings (ECAPA, 192-dim)
stored in Redis hash `alfred:identity:voiceprint` and how to delete them (`redis HDEL` until a
management UI ships), and (2) that satellites are always-listening wake-word devices. For #68, either
restore delete/list + a settings-UI control before public release, or (minimum) document the manual
`HDEL` erasure procedure in the new PRIVACY section and raise the backlog ticket from low to high so
the erasure path ships with the multi-user identity work.

## Acceptance Criteria
- [ ] A PRIVACY section (in the README) or a dedicated `PRIVACY.md` exists in `alfred`, with parallel coverage referenced from `alfred-ios/README.md` and `home-service/` docs.
- [ ] The doc discloses always-listening wake-word satellite behavior and addresses consent, recording, data-retention, and GDPR/BIPA in the data-protection sense (so `git grep` for these terms no longer returns zero data-protection hits).
- [ ] The doc states that speaker enrollment creates biometric voiceprint embeddings (ECAPA-TDNN, 192-dim, mean-normalized float32) stored in the Redis hash `alfred:identity:voiceprint`, and notes that raw enrollment audio is embedded in memory and discarded (never written to disk or tracked paths).
- [ ] The doc documents the manual erasure procedure (`redis HDEL` on `alfred:identity:voiceprint`) as the interim deletion path until a management UI ships.
- [ ] Voiceprint deletion is shipped one of two ways before `feature/voice-satellite-bridge` merges to public master: (a) restore `SpeakerID.delete()` / `enrolled_identities()` plus a settings-UI list/delete control, or (b) document the manual `HDEL` procedure (above) as the minimum.
- [ ] If path (b) is taken, `docs/backlog/low/satellite-multi-user-voice-identity.md` is raised from low to high so the erasure path ships with the multi-user identity work.
