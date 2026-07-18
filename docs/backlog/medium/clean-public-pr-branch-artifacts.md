# Remove Stray Binary/Audio Artifacts from Public PR Branches

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md) · [GitHub Chores](../epics/github-chores.md)
**Priority:** medium

> **Status update (2026-07-18, post-merge-train):** PR #29 merged before this ticket was
> actioned, but the mandatory outcome held — verified on master `01f3386` that the `>` file
> is absent from the tree and `git log master --find-object=a454cdf8…` returns nothing (the
> squash-merge dropped the blob). Remaining work is deleting the published branch
> `origin/feature/voice-satellite-bridge` (which still serves the blob at its tip), tracked
> in [delete-stale-remote-branches](delete-stale-remote-branches.md). The branch-tree scan
> for other stray artifacts also remains open.
**Severity (audit):** medium
**Source:** Public-release readiness audit 2026-07-18 (findings #10, #67)

## Summary
A stray binary file literally named `>` (89,600 bytes, ~2.8 s of 16 kHz 16-bit mono PCM audio) was accidentally committed to the PR #29 voice-satellite-bridge branch via a classic shell-redirect typo, and it sits at the tip of both the local branch and `origin/feature/voice-satellite-bridge`. It must be removed before the branch merges so it never enters master's permanent history. Decoding confirmed the audio is synthetic — Alfred's own Piper 'Alan' TTS reply ("The kitchen lights are now off, sir.") captured during the live E2E smoke test, not a recording of the owner or any human voice — so there is no personal-data exposure and the original medium finding does not escalate; this is repo hygiene, not a privacy incident. Because the blob is already pushed to origin, if that branch is publicly visible the file is already exposed there (post-exposure, not pre-release); removing it pre-merge is cheap and keeps it out of master permanently.

## Context / Motivation
Commit `e008c04` (full hash `e008c04a2ade44be74f42e59fbe1da9be457c146`, "refactor: simplify pass...", 2026-07-16, voice-satellite work) accidentally committed a file named `>` — a shell-redirect accident. The content is 89,600 bytes of raw 16-bit little-endian PCM-like data (~2.8 s at 16 kHz mono), stored as blob `a454cdf88ff0bd56e1cc08eaac1155fc076a0479`. It is present in the tip tree of `origin/feature/voice-satellite-bridge` (PR #29 head `a79e913`) as well as the local branch, so it will be published to master on merge.

The finding was originally rated medium out of concern it could be a microphone/satellite capture of the owner's voice/home. Follow-up decode (finding #67) removes that concern: the file is 16 kHz 16-bit mono PCM (2.8 s, RMS -17.2 dB, peaks 0 dB — full-scale, characteristic of TTS output), and faster-whisper large-v3-turbo transcribes it with language probability 1.0 as "The kitchen lights are now off, sir." This is Alfred's own Piper 'Alan' TTS reply from the PR #29 live E2E smoke test, NOT a recording of any human voice — no identifiable personal data. The severity stays medium as a cleanup obligation, not a privacy escalation.

Timing matters: while the branch is unmerged, dropping the blob is cheap (a small `git rm` commit, or an interactive rebase of `e008c04`). Once merged to master it is impossible to fully undo without a master history rewrite. Because the content is synthetic TTS with no personal data, a full branch-history rewrite is OPTIONAL — the mandatory outcome is that the blob never lands in master's tree (e.g. squash-merge with the file deleted first).

LOC: `git-history:e008c04a2ade44be74f42e59fbe1da9be457c146:>` (alfred repo, branch `feature/voice-satellite-bridge`, blob `a454cdf88ff0bd56e1cc08eaac1155fc076a0479`, 89,600 bytes, present in tip tree of `origin/feature/voice-satellite-bridge`).

## Acceptance Criteria
- [ ] The `>` file is removed from the `feature/voice-satellite-bridge` branch before PR #29 merges (via `git rm ">"` committed, or by dropping the blob from `e008c04` in an interactive rebase), so it is absent from the branch tip tree.
- [ ] Blob `a454cdf88ff0bd56e1cc08eaac1155fc076a0479` never enters master's tree — merge via squash (or rebase) with the file already deleted; verify `git log --all --find-object=a454cdf88ff0bd56e1cc08eaac1155fc076a0479` shows nothing reachable from master after merge.
- [ ] If the branch history is rewritten pre-merge, `origin/feature/voice-satellite-bridge` is force-pushed so the `>` file no longer appears in the published branch tree (full branch-history rewrite is optional given the content is synthetic TTS with no personal data; keeping the blob out of master is the required outcome).
- [ ] The branch tree is scanned for other stray shell-redirect / binary audio artifacts (e.g. files named `>` or `>>`, or raw PCM/audio captures left in tracked paths) and none remain.
