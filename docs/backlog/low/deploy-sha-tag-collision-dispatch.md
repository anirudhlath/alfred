# `repository_dispatch` Rebuilds Can Silently Move an Existing `alfred:<sha>` Tag

## Summary

The deploy job tags each build `alfred:${{ github.sha }}`. On a `push` to `master` that
sha is unique to the commit that triggered the run. On a `repository_dispatch` fired by
`alfred-home-service` (`home-service-merged`), `github.sha` resolves to whatever alfred's
default branch currently points at — the last real alfred commit, not a new one. A
home-service-triggered rebuild therefore builds a genuinely different image (home-service
moved, alfred didn't) but tags it with an alfred sha that may already be in use, silently
retagging — and orphaning — the image an earlier push-triggered deploy built for that same
sha.

## Context / Motivation

- `ci.yml`'s `Build` step: `docker tag alfred:latest "alfred:${{ github.sha }}"`.
  `github.sha` for a `repository_dispatch` event is the checked-out ref's head, unrelated
  to the home-service commit that actually triggered the dispatch.
- Found during Phase 5 documentation. Not yet reproduced end to end — home-service's
  dispatch job (`alfred-home-service#19`) has not merged/run yet — but the tag-collision
  logic is inspectable directly in `ci.yml` today.

## Proposed shape

- Tag `alfred:${{ github.sha }}-${{ github.run_id }}` instead, so every build gets an
  addressable, non-colliding tag regardless of what triggered it. (`run_id` is unique per
  workflow run; `sha` stays useful for "what alfred commit was this.")

## Acceptance Criteria

- [ ] Two consecutive deploys of the same alfred commit (one via `push`, one via
      `repository_dispatch`) produce two distinct, individually addressable image tags.
- [ ] The `alfred:rollback` tagging and the image-prune step (which pattern-matches 40-hex
      shas) are updated for the new tag shape and still work correctly.
