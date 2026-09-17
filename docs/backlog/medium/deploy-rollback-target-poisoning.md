# A Failed Rollback Poisons the Next Deploy's Rollback Target

## Summary

If a rollback itself fails and alfred is left running whatever broken image `Start` had
put in place, the **next** deploy's `Record the running image for rollback` step reads
that broken image straight off the running container
(`docker inspect -f '{{.Image}}' alfred`) and adopts it as *its* rollback target. A second
consecutive deploy failure would then roll back to an image that was already known-bad,
not to the last genuinely healthy one.

## Context / Motivation

- `ci.yml`'s `Record the running image for rollback` step trusts whatever `alfred` is
  currently running unconditionally — it has no way to know that a previous run's rollback
  failed to verify (the `Roll back` step's
  `::error::deploy failed AND the rollback to $TARGET did not verify — alfred may be down`
  path).
- This is a low-probability compound failure — it requires two consecutive bad deploys
  with a failed rollback verification in between — but it is the one scenario where the
  design's stated safety property ("a rollback that itself fails is louder, not quieter")
  stops holding for the deploy *after* the loud one, because the recorded rollback target
  is silently wrong rather than loudly wrong.

## Proposed shape

- Have the `Roll back` step, when its own re-verify fails, write a marker (e.g. a file in
  `$DEPLOY_DIR`, or an `alfred:known-bad` tag) that the next deploy's `Record` step checks
  before trusting the running container's image as a rollback candidate — falling back to
  the last tag known to have passed `Verify` instead.

## Acceptance Criteria

- [ ] After a rollback that itself fails to verify, the next deploy's recorded rollback
      target is NOT the broken image the box was left running.
- [ ] Covered by a double-failure drill, either as an extension of
      `docs/qa-backlog/deploy-rollback-drill.md` or a dedicated one.
