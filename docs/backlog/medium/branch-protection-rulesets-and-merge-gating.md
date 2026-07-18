# Branch Protection Mechanics: Rulesets, Aggregate CI Gate & Merge-Blocking Details

**Epic:** [GitHub Chores](../epics/github-chores.md)
**Priority:** medium
**Source:** GitHub agent-enablement round-up 2026-07-18 (follow-up pass)

## Summary
[harden-github-actions-and-ci](harden-github-actions-and-ci.md) says "enable branch
protection with required checks" — this ticket is the *how*, so the gate stays reliable
once agents depend on it. Required status checks are matched **by job name**: a renamed or
path-filtered job leaves PRs stuck on "Expected — waiting for status" forever, which in an
auto-merge world means work silently never lands. Use modern **rulesets** (not classic
protection), gate merges on a single aggregate `ci-ok` job, and never path-filter a
required check. Land this together with the harden ticket's protection work.

## Context / Motivation
- **Rulesets over classic branch protection.** Rulesets are the current mechanism (free on
  public repos), layerable, with explicit bypass lists and better auditability. Configure:
  block force pushes + branch deletion on the default branch, require PRs before merging,
  required status checks, and a bypass list of just the owner (for emergencies — bypasses
  are logged).
- **The aggregate-gate pattern.** Make exactly ONE required check: a final `ci-ok` job with
  `needs: [python, web]` and `if: always()` that fails unless every needed job succeeded
  (explicitly fail on `failure`/`cancelled`/`skipped` results). Benefits: adding/renaming/
  splitting CI jobs never desyncs the ruleset config (agents refactor workflows too), and
  the merge gate is a single stable name.
- **Never path-filter required checks.** A required check whose workflow is skipped by
  `paths:` filters never reports, and the PR blocks forever. If path-based skipping is
  wanted for speed, do it *inside* jobs (change-detection step that exits successfully) so
  the check still reports success — never at the workflow `on:` level for anything
  required.
- **Up-to-date requirement — decide explicitly.** Two green PRs can conflict semantically;
  merging a stale-but-green PR can break master. Merge queue solves this but is
  **unavailable on user-owned repos** (org-owned only — verify current state). Options:
  (a) require branches up-to-date before merge (strict checks) — safest, costs a re-run
  per master advance, acceptable at this PR volume and agents can run update-branch
  themselves; (b) leave it off and rely on master CI to catch breakage. Recommend (a) on
  alfred, (b) on the low-traffic sister repos.
- **Tag ruleset for `v*`.** Block tag deletion/overwrite on release tags — pairs with
  [release-please-setup](release-please-setup.md) (ensure the ruleset's bypass list
  admits the bot App that release-please tags through).
- **Push rules / artifact blocking.** Rulesets can restrict file paths/extensions/sizes on
  push — which would have blocked the 89KB `>` PCM blob at push time. Availability on
  user-owned repos is plan-gated (historically org/Enterprise) — **verify**; if
  unavailable, add a cheap CI guard step instead (fail on files >1MB outside allowlisted
  paths, or on unexpected binary/shell-redirect names) so the
  [clean-public-pr-branch-artifacts](clean-public-pr-branch-artifacts.md) class of mistake
  is caught by the merge gate.

## Acceptance Criteria
- [ ] Default-branch ruleset on alfred (then sister repos): PRs required, force-push +
  deletion blocked, required status check = `ci-ok` only, owner-only bypass list.
- [ ] `ci-ok` aggregate job added to `ci.yml` (`needs` all jobs, `if: always()`, fails on
  any non-success result) and is the ONLY required check.
- [ ] No workflow containing a required check uses `on:`-level `paths:` filters; any
  change-detection skipping happens in-job and still reports success.
- [ ] Up-to-date requirement decision recorded and configured per repo (recommend strict
  on alfred).
- [ ] Tag ruleset protects `v*` tags from deletion/overwrite.
- [ ] Push rules configured if available on user-owned repos; otherwise a CI artifact-guard
  step (size/path/binary check) is added and included in `ci-ok`.
- [ ] Verified: a PR with a failing sub-job cannot merge; a PR whose jobs all pass
  auto-merges; renaming a CI sub-job does not strand any PR.
