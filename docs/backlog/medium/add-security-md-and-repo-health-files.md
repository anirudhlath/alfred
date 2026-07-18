# Add SECURITY.md, Issue/PR Templates & Code of Conduct

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md) · [GitHub Chores](../epics/github-chores.md)
**Priority:** medium
**Severity (audit):** medium
**Source:** Public-release readiness audit 2026-07-18 (findings #29)

## Summary
None of the three public repos (`alfred/`, `alfred-ios/`, `home-service/`) has a
`SECURITY.md`, so there is no stated private vulnerability-disclosure channel and
reporters will default to filing security issues publicly. Because these repos are
**already public**, this is a live gap on a project that ships WebAuthn auth, a secrets
manager, home-automation control, and push-notification credentials — post-exposure
remediation, not pre-release polish. Issue templates, a PR template, `CODE_OF_CONDUCT.md`,
and dependabot config are also absent everywhere.

## Context / Motivation
- No `SECURITY.md` exists in any of `alfred/`, `alfred-ios/`, or `home-service/`. With no
  private disclosure channel documented, a reporter finding a vulnerability will default
  to a public GitHub issue, disclosing the flaw before it can be fixed.
- Severity rationale: the project ships security-sensitive surfaces — WebAuthn auth, a
  secrets manager, home-automation control, and push-notification credentials — which
  raises the value of having a private, well-signposted disclosure path.
- Also absent everywhere: issue templates, PR template, `CODE_OF_CONDUCT.md`, and
  dependabot config. `alfred`'s `.github/` contains only the two workflow files.
- Scope note (already in decent shape): repo descriptions are good on all three repos, and
  `alfred` already has `README`, `CONTRIBUTING`, a CLA, and a `LICENSE`. This ticket is
  the community-health/security-metadata gap on top of that baseline.

## Acceptance Criteria
- [ ] `alfred/` has a `SECURITY.md` (at minimum) pointing reporters to GitHub private
  vulnerability reporting or a dedicated security email.
- [ ] GitHub private vulnerability reporting is enabled on `alfred/`.
- [ ] Basic issue templates and a PR template are added to `alfred/.github/` as polish.
- [ ] `CODE_OF_CONDUCT.md` and dependabot config are added to `alfred/`.
