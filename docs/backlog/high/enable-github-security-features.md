# Enable Secret Scanning, Push Protection & Dependabot on Public Repos

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md) · [GitHub Chores](../epics/github-chores.md)
**Priority:** high
**Severity (audit):** medium (elevated to high priority: this is the guardrail that prevents another key leak)
**Source:** Public-release readiness audit 2026-07-18 (findings #36, #37, #38)

## Summary
Every GitHub secret-protection and dependency-alert feature is disabled on all three
already-public repos (`anirudhlath/alfred`, `anirudhlath/alfred-ios`,
`anirudhlath/alfred-home-service`): secret scanning, push protection, validity checks,
non-provider patterns, private vulnerability reporting, and Dependabot alerts/security
updates are all off. Because the repos are already public and this project has a proven
history of committing a real APNs `.p8` private key, this is post-exposure hardening, not
pre-release cleanup — the guardrails that would have caught (or blocked) that leak still do
not exist. All of these features are free for public repos and each is a single API call to
enable.

## Context / Motivation
Confirmed against all three public repos (`security_and_analysis` settings + REST probes):

- **Secret scanning + push protection (finding #36):** every secret-protection feature is
  disabled on all three repos. With **push protection off**, nothing prevents a repeat of
  the `.p8` incident on the next `git push`. With **scanning off**, GitHub will not alert on
  secrets already present or newly introduced — including partner-pattern tokens such as
  OpenRouter/Apple keys. Validity checks and non-provider patterns are likewise off.
- **Private vulnerability reporting (finding #37):** all three repos return
  `{"enabled":false}` from `/private-vulnerability-reporting`, and no `SECURITY.md` exists in
  any repo. A researcher who finds an issue (e.g. in the auth middleware, WebSocket auth gate,
  or trusted-network logic — all now-public attack-surface code) has no private disclosure
  path; the only options are a public GitHub issue (instant 0-day disclosure) or guessing the
  owner's email.
- **Dependabot (finding #38):** `/vulnerability-alerts` returns 404 (would be 204 if enabled)
  on all three repos, `/dependabot/alerts` returns 403 `Dependabot alerts are disabled for
  this repository.`, and `security_and_analysis` shows `dependabot_security_updates=disabled`
  everywhere. The alfred monorepo has a large Python + npm (Vite/React web SPA) dependency
  surface and home-service pulls FastAPI/httpx; with alerts off the owner gets no notification
  when a published CVE lands in a pinned dependency of a public repo.

Scope note: only the three repos with a GitHub remote are affected. `signal-bridge/` and
`home-assistant/` are local-only (no remote) and are out of scope here.

Remediation commands (per repo, `<repo>` ∈ {`alfred`, `alfred-ios`, `alfred-home-service`}):

```bash
# Secret scanning + push protection (add non_provider_patterns + validity_checks too)
gh api -X PATCH repos/anirudhlath/<repo> \
  -f 'security_and_analysis[secret_scanning][status]=enabled' \
  -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled'

# Private vulnerability reporting (204 on success)
gh api -X PUT repos/anirudhlath/<repo>/private-vulnerability-reporting

# Dependabot alerts, then automated security-update PRs
gh api -X PUT repos/anirudhlath/<repo>/vulnerability-alerts
gh api -X PUT repos/anirudhlath/<repo>/automated-security-fixes
```

Equivalent web UI path: Settings → Advanced Security (secret scanning, push protection,
private vulnerability reporting, Dependabot). Low effort, purely additive.

## Acceptance Criteria
- [ ] Secret scanning enabled on all three repos (`anirudhlath/alfred`, `anirudhlath/alfred-ios`, `anirudhlath/alfred-home-service`).
- [ ] Push protection enabled on all three repos.
- [ ] Non-provider patterns and validity checks enabled on all three repos.
- [ ] Private vulnerability reporting enabled on all three repos (`/private-vulnerability-reporting` returns enabled).
- [ ] A `SECURITY.md` added to each repo pointing at the PVR (private vulnerability reporting) flow.
- [ ] Dependabot alerts enabled on all three repos (`/vulnerability-alerts` returns 204).
- [ ] Dependabot automated security updates (security-update PRs) enabled on all three repos.
