# Enable Auto-Merge & Delete-Branch-on-Merge on All Repos

**Epic:** [GitHub Chores](../epics/github-chores.md)
**Priority:** high
**Source:** GitHub agent-enablement round-up 2026-07-18 (verified via `gh api` on alfred)

## Summary
`anirudhlath/alfred` currently has `allow_auto_merge: false` and
`delete_branch_on_merge: false` (verified 2026-07-18). With auto-merge off, an agent that
opens a green PR still needs a human click to land it; with branch cleanup off, every merge
leaves a stale branch on the public remote — the exact mess
[delete-stale-remote-branches](../medium/delete-stale-remote-branches.md) is cleaning up.
Both are one-line settings changes and are prerequisites for a hands-off agent merge flow.

## Context / Motivation
- Auto-merge is only safe behind required status checks — sequence this with (or after)
  branch protection from [harden-github-actions-and-ci](../medium/harden-github-actions-and-ci.md).
  Until protection exists, auto-merge on a green-but-ungated repo merges instantly.
- `delete_branch_on_merge` is retroactively harmless and can be enabled immediately on all
  repos.
- Command per repo:
  `gh api -X PATCH repos/anirudhlath/<repo> -F allow_auto_merge=true -F delete_branch_on_merge=true`
- Apply to `alfred`, `alfred-ios`, `alfred-home-service`, and to `alfred-signal-bridge` /
  `alfred-home-assistant` when [those repos are created](../medium/prep-sibling-repos-license-readme.md).
- Merge-queue is deliberately out of scope: solo-owner + agents doesn't have the PR
  contention that justifies it; revisit only if concurrent agent PRs start racing.

## Acceptance Criteria
- [ ] `delete_branch_on_merge=true` on all repos with a GitHub remote.
- [ ] `allow_auto_merge=true` on all repos with a GitHub remote, enabled together with (or
  after) required status checks on the default branch.
- [ ] Squash-only enforced (merge commits + rebase-merge disabled) — DECIDED per the
  branching strategy spec §3: squash titles are the conventional-commit stream
  release-please reads, and squashing keeps stray blobs like the PR #29 `>` file out of
  master history.
- [ ] Verified end-to-end: an agent-opened PR set to auto-merge lands by itself once CI
  passes, and its branch is auto-deleted.
