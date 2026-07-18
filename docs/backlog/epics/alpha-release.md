# Epic: Alpha Release Readiness

**Status:** Open
**Created:** 2026-07-18
**Owner:** @anirudhlath
**Source:** Public-release readiness audit (104-agent workflow `ww7pnqlf1` / run `wf_c13d7aac-b4d`, 70 verified findings)

## Goal
Bring the Alfred workspace to a defensible **alpha** state: no live secrets or personal
data in published content, a first clone that builds and runs, green quality gates,
sane security defaults, and coherent licensing/legal/privacy docs. This epic tracks every
item surfaced by the release-readiness audit.

## Reality check: the repos are already public
This audit was requested as "readiness for public release" — but three repos are **already
public**: `anirudhlath/alfred` shipped release **v0.1.0 "first public release" on 2026-07-16**,
alongside `anirudhlath/alfred-ios` and `anirudhlath/alfred-home-service`. The other two
(`signal-bridge/`, `home-assistant/`) have **no GitHub remote at all** (local-only).
Consequence: for the three public repos, remediation is **post-exposure response**
(rotate/revoke + history + redact), not "clean up before going public."

## ⚠️ Handling note — coordinated disclosure
Several tickets below describe **live, unpatched vulnerabilities on an already-public repo**
(a still-downloadable private key, auth-bypass chains, the owner's apartment IP). Do **not**
file those as public GitHub issues until fixed — treat as coordinated disclosure. These
backlog files themselves contain sensitive specifics; keep them local (or scrub) and do not
bulk-commit them into the public repo. Tickets marked **🔒 Sensitive** are the affected ones.

**See also:** the GitHub-platform subset of this work (settings, workflows, branch
protection, branch/PR hygiene) is organized for a focused work session in the
[GitHub Chores epic](github-chores.md).

---

## 🔴 Blocker — do immediately (before anything else)
- [ ] 🔒 [Revoke Leaked APNs Signing Key Still Served by GitHub](../highest/revoke-leaked-apns-key.md) — the real Apple `.p8` is still downloadable at a pre-rewrite SHA; revoke the Apple key + GitHub Support GC.

## 🟠 High — alpha-blocking
- [ ] [Enable Secret Scanning, Push Protection & Dependabot on Public Repos](../high/enable-github-security-features.md) — all off on all 3 repos; free guardrail that prevents the next key leak.
- [ ] [Lock Down Unauthenticated Redis & Anonymous MQTT (Deployment)](../high/lock-down-compose-redis-mqtt.md) — Redis is the auth root of trust; drop published ports + add auth.
- [ ] [Lock WebAuthn Registration After First Passkey](../high/webauthn-registration-lock.md) — any trusted-network peer can enroll and become owner.
- [ ] [Fix Broken First-Run: Document Node.js + Web Build Step](../high/fix-first-run-web-build.md) — quickstart yields a UI-less server (`/` 404s).
- [ ] [Fix First-Run 401 from Gated EmbeddingGemma Default](../high/embedding-model-gated-first-run.md) — default embedding model can't be downloaded without accepting Gemma ToU.
- [ ] [Restore Green Quality Gates on master (ruff format + mypy)](../high/restore-green-master-gates.md) — documented gates fail on a fresh clone.
- [ ] [Resolve alfred-sdk AGPL/MIT License Contradiction](../high/sdk-license-clarification.md) — AGPL SDK bundled into MIT-labeled repos.

## 🟡 Medium
- [ ] [Relock Dependencies to Clear CRITICAL/HIGH CVEs](../medium/dependency-cve-relock.md) — litellm 3 CRITICAL + starlette/urllib3/cryptography HIGH.
- [ ] 🔒 [Redact Real Apartment HA IP from Docs & History](../medium/redact-apartment-ip.md) — `192.168.50.159` in tracked specs + recent history.
- [ ] [Move Runtime-Mutated Data Out of Git-Tracked Paths](../medium/runtime-data-out-of-tracked-paths.md) — routines YAML + research CSVs bleed real home behavior into commits.
- [ ] [Harden Trusted-Network Gate & Enforce Identity Clearance](../medium/trusted-network-and-clearance-enforcement.md) — proxy-header spoofable; `risk_clearance` never enforced.
- [ ] [Document Threat Model & Prompt-Injection Defense](../medium/threat-model-prompt-injection.md) — LLM wired to home control + calendar CRUD with no documented defense.
- [ ] [Add Privacy/Consent/Biometric Docs (GDPR/BIPA) + Voiceprint Deletion](../medium/privacy-consent-biometric-docs.md) — always-on audio + ECAPA voiceprints, no consent/retention docs, no deletion path.
- [ ] [Harden GitHub Actions & Add Branch Protection/CI Enforcement](../medium/harden-github-actions-and-ci.md) — `cla.yml` on `pull_request_target` w/ write perms; unenforced CONTRIBUTING gates.
- [ ] [Fix .env.example & README/CONTRIBUTING Config Drift](../medium/fix-env-example-and-readme-drift.md) — ~13 undocumented env vars, wrong defaults, stale "not yet public" text.
- [ ] [Add SECURITY.md, Issue/PR Templates & Code of Conduct](../medium/add-security-md-and-repo-health-files.md) — zero responsible-disclosure path today.
- [ ] 🔒 [Prep signal-bridge & home-assistant Repos (LICENSE, README, .gitignore, history)](../medium/prep-sibling-repos-license-readme.md) — unlicensed = legally unusable; HA history has runtime files.
- [ ] [Remove Stray Binary/Audio Artifacts from Public PR Branches](../medium/clean-public-pr-branch-artifacts.md) — 89KB PCM file named `>` on PR #29 branch. *(Update 2026-07-18: PR #29 merged; master verified clean of the blob — remainder is branch deletion, see [delete-stale-remote-branches](../medium/delete-stale-remote-branches.md).)*
- [ ] [Delete Stale Merged Remote Branches](../medium/delete-stale-remote-branches.md) — post-merge-train cleanup; removes the published `>` blob branch.

## ⚪ Low / polish
- [ ] [Scrub Local Absolute Paths & Private Session URLs from Published Docs/PRs](../low/scrub-local-paths-and-session-links.md)
- [ ] [iOS Hardening: ATS Arbitrary Loads, Team ID & Tailscale IP in History](../low/ios-security-hardening.md)
- [ ] [Release Polish Batch: Bind Address, Model License Docs, AGPL Notice, web README, pytest Warning](../low/release-polish-batch.md)
- [ ] [Decide "Alfred" Branding: Trademark & Package-Namespace Collisions](../low/alfred-name-branding-decision.md)

## Linked existing backlog tickets (already tracked — do not duplicate)
- [mypy --strict fails with redis 8 stubs](../medium/mypy-strict-redis8-stub-drift.md) — subsumed by *Restore Green Quality Gates*.
- [APNs Credential Setup and E2E Testing](../high/apns-credential-setup.md) — the reconfigure-after-revoke step for the blocker.
- [Admin API: respect owning-process boundaries](../medium/admin-api-owner-boundaries.md) — auth-surface hardening adjacent to *Trusted-Network Gate*.
- [Admin API: auth-surface consistency + perf follow-ups](../low/admin-auth-and-perf-followups.md) — cookie `Secure` flag + integration-cred gating.
- [Multi-User WebAuthn with Roles](../low/multi-user-webauthn-roles.md) — the eventual home for enforced clearance/roles.

---

## Definition of Done (alpha)
- Blocker + **all High** tickets closed.
- GitHub secret scanning + push protection enabled on all three public repos.
- `ruff check`, `ruff format --check`, `mypy --strict`, and `pytest` all pass on a **fresh clone** (not just the aged local venv).
- No live secret, credential, or real personal data reachable in any published tree, history, or PR branch.
- README quickstart, followed literally on a clean machine, produces a running server **with UI**.
- `SECURITY.md` + a documented responsible-disclosure path exist.
- SDK licensing is explicit and non-contradictory; every published repo has a LICENSE.
- Privacy/consent documentation exists for the audio + voiceprint pipeline.
