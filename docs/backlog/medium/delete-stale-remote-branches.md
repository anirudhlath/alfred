# Delete Stale Merged Remote Branches (incl. the `>` PCM blob branch)

**Epic:** [GitHub Chores](../epics/github-chores.md) · [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** medium
**Source:** GitHub-chores round-up 2026-07-18; completes the remaining GitHub-side scope of
[clean-public-pr-branch-artifacts](clean-public-pr-branch-artifacts.md)

## Summary
The 2026-07-18 merge train landed PRs #28 → #27 → #29, but the merged feature branches were
never deleted from the public `anirudhlath/alfred` remote. One of them,
`origin/feature/voice-satellite-bridge`, still publishes the stray `>` file (89,600 bytes of
synthetic Piper TTS PCM, blob `a454cdf88ff0bd56e1cc08eaac1155fc076a0479`) at its tip.
Verified 2026-07-18 on post-merge-train master (`01f3386`): the blob is **NOT** reachable
from master — `git ls-tree master` has no `>` entry and
`git log master --find-object=a454cdf8…` returns nothing — so the mandatory outcome of the
artifacts ticket (blob never enters master) is already satisfied. What remains is deleting
the published branches so the blob (and three other stale branch tips) stop being served.

## Context / Motivation
Remote branches on `origin` as of 2026-07-18 (`git branch -r` after `git fetch --prune`):

| Branch | Status | Action |
|---|---|---|
| `feature/voice-satellite-bridge` | merged (PR #29) — tip tree still contains `>` blob | delete |
| `feature/ha-plan1-sovereign-credentials` | merged (PR #28) | delete |
| `feat/instant-triggers-client-tz` | merged (PR #27) | delete |
| `feature/web-app-rebuild` | merged (PR #21, 2026-07-15) | delete |
| `cla-signatures` | CLA bot data branch | **keep** |
| `master` | default | keep |

Also check `anirudhlath/alfred-ios` for the merged `client-timezone` branch (PR #1) and
delete it if it still exists on that remote.

Note: deleting the branch makes the blob unreachable but GitHub may still serve it by SHA
until garbage collection. Since the content is verified synthetic TTS ("The kitchen lights
are now off, sir.") with no personal data, no GitHub Support GC request is warranted for it
— but if a Support request is filed anyway for the APNs `.p8` purge
([revoke-leaked-apns-key](../highest/revoke-leaked-apns-key.md)), mention this blob too.

## Acceptance Criteria
- [ ] `git push origin --delete feature/voice-satellite-bridge feature/ha-plan1-sovereign-credentials feat/instant-triggers-client-tz feature/web-app-rebuild` succeeds on `anirudhlath/alfred`.
- [ ] `cla-signatures` and `master` are untouched.
- [ ] Merged `client-timezone` branch deleted from `anirudhlath/alfred-ios` if present.
- [ ] Local worktrees owning those branches are cleaned up first (or branches deleted with their sessions' knowledge) — per memory, several worktrees were pending deletion after the merge train.
- [ ] `git branch -r` after `git fetch --prune` shows only `master`, `cla-signatures` (and any genuinely active feature branches).
- [ ] Re-verify master stays clean: `git log master --find-object=a454cdf88ff0bd56e1cc08eaac1155fc076a0479` returns nothing.
