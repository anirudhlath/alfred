# Publish home-service Repository

## Summary

Make the home-service repo public. The alfred README lists it as a
prerequisite while admitting it is "separate repo, not yet public" — meaning
no outside user can actually run the full system today. Same consideration
applies to signal-bridge (optional channel, lower priority).

## Context / Motivation

Launch-readiness assessment (2026-07-18). Publishing is gated on HA Plan 2
(`docs/superpowers/plans/2026-07-15-ha-plan2-home-service-rewrite.md`) — the
rewrite replaces the legacy name-guessing implementation wholesale, so
publishing before it lands would ship code slated for deletion.

## Acceptance Criteria

- [ ] After Plan 2 merges: license aligned with alfred (AGPL + CLA), README
      with setup + credential-flow usage, no secrets in history (audit
      `.env`/token leakage in old commits — squash or rewrite if needed).
- [ ] Repo public; alfred README prerequisite note updated (drop "not yet
      public").
- [ ] CI: lint + type + test workflow mirroring alfred's.
- [ ] Decide signal-bridge publication separately (optional channel; lower
      stakes).
