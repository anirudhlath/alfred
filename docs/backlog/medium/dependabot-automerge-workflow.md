# Dependabot Auto-Merge Workflow for Patch/Dev-Dependency Updates

**Epic:** [GitHub Chores](../epics/github-chores.md)
**Priority:** medium
**Source:** GitHub agent-enablement round-up 2026-07-18

## Summary
[enable-github-security-features](../high/enable-github-security-features.md) turns
Dependabot on but not the follow-through: without auto-merge, every update becomes a
manual PR chore — on a dependency surface this size (large Python tree + npm SPA +
github-actions ecosystem), that's a steady drip of busywork. A small workflow that
auto-approves and enables auto-merge for low-risk updates (patch-level, and minor
dev-only deps) when CI is green keeps the update stream self-tending; minor/major runtime
updates still wait for a human (or an `@claude` review).

## Context / Motivation
- Shape: workflow on `pull_request` gated to `github.actor == 'dependabot[bot]'`, using
  `dependabot/fetch-metadata` to read `update-type` and dependency scope, then
  `gh pr review --approve` + `gh pr merge --auto --squash` for qualifying PRs.
  `permissions: contents: write, pull-requests: write`.
- Hard prerequisites: `allow_auto_merge=true`
  ([enable-auto-merge-branch-cleanup](../high/enable-auto-merge-branch-cleanup.md)) and
  required status checks on the default branch
  ([harden-github-actions-and-ci](harden-github-actions-and-ci.md)) — auto-merge without
  required checks merges untested updates instantly. Do not land this ticket first.
- CI must actually exercise the dependency surface for green to mean anything —
  [ci-frontend-gates-and-concurrency](../high/ci-frontend-gates-and-concurrency.md) closes
  the npm blind spot.
- Policy suggestion: auto-merge `version-update:semver-patch` for all deps and
  `semver-minor` for dev-dependency groups only; never auto-merge major bumps or anything
  in the auth/crypto path (cryptography, pyjwt, webauthn) — those get a comment tagging
  `@claude` for a reviewed upgrade instead.
- SHA-pin the metadata action per the hardening standard.
- **Token gotcha:** enabling auto-merge with the default `GITHUB_TOKEN` means the eventual
  merge is performed by `github-actions[bot]` and **master `push` workflows will not run**
  on it (GitHub's recursion guard). Use the bot App token from
  [workflow-chaining-and-bot-commit-resilience](../high/workflow-chaining-and-bot-commit-resilience.md),
  or consciously accept and document the gap.

## Acceptance Criteria
- [ ] Workflow added to `alfred` (and sister repos once they have CI): Dependabot
  patch-level PRs auto-approve + auto-merge when all required checks pass.
- [ ] Policy boundary implemented and documented in the workflow: what auto-merges, what
  waits (majors, auth/crypto-path deps), and what gets escalated to `@claude`.
- [ ] Landed strictly after branch protection + auto-merge settings; verified a
  non-qualifying PR (e.g. a major bump) does NOT auto-merge.
- [ ] Verified end-to-end on a real Dependabot PR.
