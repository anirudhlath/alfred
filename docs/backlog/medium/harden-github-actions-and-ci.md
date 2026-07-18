# Harden GitHub Actions & Add Branch Protection / CI Enforcement

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md) · [GitHub Chores](../epics/github-chores.md)
**Priority:** medium
**Severity (audit):** medium
**Source:** Public-release readiness audit 2026-07-18 (findings #27, #28, #39, #55, #65)

## Summary
The three public repos (alfred, alfred-ios, alfred-home-service) have no branch
protection and inconsistent CI, so the contribution gates CONTRIBUTING promises are not
actually enforced and two sister repos accept PRs with no checks at all. The alfred CLA
workflow runs on `pull_request_target` with write-scoped `GITHUB_TOKEN` and a
tag-pinned (not SHA-pinned) third-party action, and fork-PR workflow approval is left at
GitHub's permissive default. Because these repos are **already public**, this is
post-exposure hardening of live Actions surface and a live contribution path, not
pre-release cleanup — anyone can open a PR against them today.

## Context / Motivation
- **Branch protection is off on all three default branches** — `gh api .protected =
  false` for alfred (`master`), alfred-ios (`master`), and alfred-home-service (`main`).
  `alfred/CONTRIBUTING.md` states "CI runs the same checks; PRs need a green run" and
  "Pull requests with unsigned commits will be asked to rebase", but there are **no
  required status checks and no DCO check app/workflow**. The project's own history has
  zero `Signed-off-by` trailers (recent commits carry only `Co-Authored-By: Claude Fable
  5`), so the stated DCO policy is not followed even internally (finding #27; LOC
  `alfred/CONTRIBUTING.md`, `alfred/.github/workflows/ci.yml`,
  `alfred/.github/workflows/cla.yml`).
- **Sister repos have zero CI and no contribution policy** — neither alfred-ios nor
  home-service has anything under `.github/` (`git ls-files` returns nothing). alfred-ios
  has a real snapshot suite (`Tests/AlfredTests`) and home-service has `tests/`, but
  nothing runs on PRs, so broken contributions land silently. Neither repo has a
  `CONTRIBUTING.md` or the CLA bot, so the relicensing protection the alfred CLA was built
  for does **not** cover contributions there — and alfred-ios PR #1 is already open
  (finding #28; LOC `alfred-ios/`, `home-service/`).
- **`cla.yml` runs on `pull_request_target` with write perms and a tag-pinned action** —
  it triggers on `opened`/`closed`/`synchronize`, which runs automatically in the
  base-repo context for any fork PR, so the fork-PR contributor-approval setting does not
  gate it. It grants `GITHUB_TOKEN` `actions:write`, `contents:write`,
  `pull-requests:write`, `statuses:write` and hands it to
  `contributor-assistant/github-action@v2.6.1`, pinned by mutable tag rather than commit
  SHA (repo-level `sha_pinning_required=false`, `allowed_actions=all`). Current risk is
  contained because the workflow never checks out PR code (findings #39, #55; LOC
  `.github/workflows/cla.yml`, `alfred/.github/workflows/ci.yml`).
- **`ci.yml` workflow-hardening gaps** — it triggers on `pull_request` (fork PRs get a
  read-only `GITHUB_TOKEN`, which is fine) but declares **no top-level `permissions`
  block**, so push-event runs get the repo-default token scope. Actions are pinned to
  mutable tags (`actions/checkout@v4`, `astral-sh/setup-uv@v5`, `actions/cache@v4`,
  `contributor-assistant/github-action@v2.6.1`) rather than commit SHAs, and there is no
  `dependabot.yml` for the github-actions ecosystem (finding #55).
- **Fork-PR approval left at the permissive default** — all three repos have
  `approval_policy=first_time_contributors`: a first-time fork contributor needs approval
  to run `pull_request` workflows, but anyone with a single previously-merged PR runs them
  unapproved forever after. alfred's `ci.yml` runs on `pull_request` with uv-installed
  dependencies, so arbitrary code from the PR executes in the runner; `allowed_actions=all`
  on every repo. Partially mitigated: `default_workflow_permissions=read` (finding #65;
  LOC Actions settings for all three repos).

## Acceptance Criteria
- [ ] Branch protection is enabled on all three default branches (alfred `master`,
  alfred-ios `master`, alfred-home-service `main`) with CI (and CLA on alfred) as required
  status checks.
- [ ] The DCO discrepancy is resolved one of two ways: either a DCO check is installed and
  commits start carrying `Signed-off-by`, or the DCO paragraph is removed from
  `alfred/CONTRIBUTING.md` so the public policy matches practice.
- [ ] alfred-ios and home-service each have a minimal CI workflow that runs on PRs (`swift
  test` for alfred-ios; `uv run pytest` + `ruff` + `mypy` for home-service).
- [ ] If contributions to alfred-ios / home-service should be covered by the same
  relicensing terms, the CLA workflow and a `CONTRIBUTING.md` are replicated into both.
- [ ] `contributor-assistant/github-action` is pinned to its full commit SHA in
  `cla.yml` (and the other actions in `ci.yml` are pinned to SHAs).
- [ ] `cla.yml` drops `actions:write` unless the lock/comment feature is used
  (contributor-assistant needs only `contents:write` + `pull-requests:write` +
  `statuses:write`; `actions:write` is required only for locking).
- [ ] `ci.yml` declares an explicit top-level `permissions: contents: read` block.
- [ ] A `dependabot.yml` is added for the github-actions ecosystem.
- [ ] Fork-PR workflow approval is tightened to require approval for all outside
  collaborators on all three repos (`approval_policy=all_external_contributors`), and
  optionally `sha_pinning_required=true` / restricted `allowed_actions` are set.

## Related
- [branch-protection-rulesets-and-merge-gating](branch-protection-rulesets-and-merge-gating.md) — the implementation mechanics for this ticket's branch-protection AC (rulesets, single `ci-ok` required check, up-to-date strictness, tag/push rules). Land together.
- [workflow-chaining-and-bot-commit-resilience](../high/workflow-chaining-and-bot-commit-resilience.md) — token strategy so bot commits/merges/tags actually trigger the workflows this ticket hardens; reusable `workflow_call` CI for the sister-repo workflows this ticket adds.
