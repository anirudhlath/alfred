# Revoke Leaked APNs Signing Key Still Served by GitHub

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** highest
**Severity (audit):** blocker
**Source:** Public-release readiness audit 2026-07-18 (findings #0, #1, #2, #41)

> 🔒 **Sensitive:** describes a live exposure on an already-public repo — do NOT file as a public GitHub issue until remediated.

## Summary
The real Apple APNs signing key `secrets/AuthKey_2U36353CR2.p8` was committed to `anirudhlath/alfred` around 2026-05-03 (commit `7d16513`). History was rewritten on 2026-07-13 (commit `94abfe4`) so no reachable ref contains it today, but the rewrite did **not** remove it from GitHub: the 257-byte private-key blob still survives in unreachable pre-rewrite commits and is downloadable by anyone who has the SHA. Because the repo is already public, this is **post-exposure response** (revoke + purge), not pre-release cleanup — the key must be assumed compromised. Revoking the Apple key is the mandatory first action; a GitHub Support garbage-collection request is the secondary one.

## Context / Motivation
- **The exposure is still live (re-probe CONFIRMED, finding #2).** `gh api repos/anirudhlath/alfred/commits/2cc1132e` returns the full pre-rewrite commit (dated 2026-06-11), and the contents API at that ref still lists `secrets/AuthKey_2U36353CR2.p8` (257 bytes, blob `7d5245757a9974ba6224115147c814b9185c4b06`, begins `-----BEGIN PRIVATE KEY-----`). Anyone holding the SHA can download it — and the SHA appears in old PR/commit references, notification emails, and any pre-rewrite clone. There is no evidence of key revocation or of a GitHub Support GC request.
- **How it got here (finding #1).** The key was committed at `7d16513` ("Personal-use setup — APNs key file checked into repo at `secrets/`"). The 2026-07-13 rewrite (`94abfe4`) purged it from reachable history, but the blob survives in ~74 unreachable pre-rewrite commits that GitHub still serves by SHA.
- **Scope limit — the current tree is clean (finding #0).** The audit lead claiming `.gitignore` has no `secrets/` entry is **FALSE**: `.gitignore` line 48 is `secrets/` (added in commit `94abfe4`). `git check-ignore` confirms the `.p8` is ignored, `git ls-files` shows nothing under `secrets/`, and `git log --all --diff-filter=A -- secrets/* *.p8` returns empty — the key was never committed on any current/reachable ref. The key ID `2U36353CR2` appears in zero tracked files today (only the generic template `secrets/AuthKey_<APNS_KEY_ID>.p8` in `.env.example:62`). The remaining working-tree copy is untracked and gitignored. So remediation targets the unreachable pre-rewrite objects GitHub still serves, not the working tree.
- **Associated identifiers (finding #41, low).** Commit `7d16513` also hardcoded `team_id="A3PH6PGY29"`, `key_id="2U36353CR2"`, and `bundle_id="com.anirudhlath.alfred"` into `core/channels/web_server.py`; commit `e1098d5` later moved them to env vars. Both commits are in **reachable** master history and will be published. These are identifiers, not the secret (the `.p8` is the secret), and the Team ID is semi-public via app bundles — but the Key ID pairs with the leaked `.p8` to form a complete credential. Leaving them in reachable history is acceptable **once the `.p8` is revoked**, which makes the Key ID inert.

## Acceptance Criteria
- [ ] APNs key `2U36353CR2` is revoked in the Apple Developer portal (Certificates, Identifiers & Profiles → Keys) and a replacement APNs key is issued (web-only, no API/CLI).
- [ ] The runtime APNs secret is updated to the new key via the keyring-based secrets manager (reconfigure via [apns-credential-setup](../high/apns-credential-setup.md)).
- [ ] A GitHub Support request is filed (support.github.com → "Remove cached commits / sensitive data") to garbage-collect / purge the unreachable pre-rewrite objects on `anirudhlath/alfred` — OR the repo is deleted and re-created from a clean local push.
- [ ] Verified the key is no longer served: the contents API at `2cc1132e` (`gh api repos/anirudhlath/alfred/commits/2cc1132e`) no longer returns `secrets/AuthKey_2U36353CR2.p8` / blob `7d5245757a9974ba6224115147c814b9185c4b06`.
- [ ] Decision recorded on the hardcoded Team/Key/bundle IDs in reachable history (commits `7d16513`, `e1098d5`): acceptable to leave once the `.p8` is revoked (Key ID inert); if a history rewrite is performed for the purge anyway, excise these two commits' literals at the same time.
- [ ] (Defense in depth) A pre-commit secret scanner (gitleaks/trufflehog) and GitHub push protection are enabled to prevent a repeat leak.
- [ ] `research/daily/2026-07-16.md` is committed or ignored so it does not ride along in a future bulk `git add`.

## Related
- [APNs Credential Setup and E2E Testing](../high/apns-credential-setup.md) — reconfigure the NEW key here after revocation.
