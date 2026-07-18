# Add CODEOWNERS for Auto Review-Requests on Agent PRs

**Epic:** [GitHub Chores](../epics/github-chores.md)
**Priority:** low
**Source:** GitHub agent-enablement round-up 2026-07-18

## Summary
With agents opening PRs (Dependabot, `@claude`), a `.github/CODEOWNERS` file with
`* @anirudhlath` auto-requests the owner's review on every PR — a lightweight notification
channel that needs no workflow. Deliberately do **not** couple it to branch protection's
"require review from Code Owners," which would block the auto-merge flow the other tickets
build; CODEOWNERS here is a ping, not a gate.

## Context / Motivation
- One file per repo, one line for a solo owner; per-path entries can come later if
  collaborators join (e.g. `sdk/` needing stricter eyes).
- The trade-off to record: enabling required code-owner review would reinstate a human
  click on every agent PR — exactly what
  [enable-auto-merge-branch-cleanup](../high/enable-auto-merge-branch-cleanup.md) removes.
  If a stronger gate is ever wanted for specific paths (auth code, workflows, SDK), scope
  required-owner-review to those paths only via a ruleset rather than repo-wide.

## Acceptance Criteria
- [ ] `.github/CODEOWNERS` with `* @anirudhlath` in alfred, alfred-ios, and home-service
  (and the sibling repos once created).
- [ ] Branch protection does NOT require code-owner review (auto-merge flow verified still
  working after the file lands).
- [ ] Decision recorded (in the ticket or protection config) on whether any sensitive
  paths (`.github/workflows/`, `core/identity/`, `sdk/`) warrant scoped required review
  later.
