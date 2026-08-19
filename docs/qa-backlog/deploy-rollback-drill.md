# Deploy Rollback Drill

**Feature:** Continuous deployment (Phase 2, `ci.yml` `deploy` job)
**Priority:** high
**Type:** functional / failure-mode
**Status:** pending — this is about alfred's container rollback specifically. The
satellite fleet's independent rollback mechanism has already been exercised for real and
passed; see `docs/qa-backlog/satellite-rollback-exercised.md`. That is not evidence for
this drill — the two are separate implementations on separate runners.

## Prerequisites
- `first-live-deploy-lath-server.md` has passed, so a known-good `alfred` is running.
- Note the image it's currently running: `docker inspect -f '{{.Image}}' alfred` — this is
  the id the *next* deploy's `Record` step will tag `alfred:rollback` and use as its
  restore target.

## Test Steps
1. On a branch, break the runtime *without* breaking CI — e.g. make the web channel raise
   on startup in a path no unit test covers. Confirm `ci-ok` still goes green on the PR.
2. Merge it.
3. Watch the deploy job (`gh run watch`).
4. After the job finishes: `docker inspect -f '{{.Image}}' alfred`
5. `docker inspect alfred --format '{{.State.Health.Status}}'`
6. Read the job log for the `Roll back` step's messages.
7. Revert the breaking commit and let the next deploy restore the trunk.

## Expected Result
- Step 3: the `Verify` step FAILS (smoke's `health` check times out after up to 300s), the
  `Roll back` step runs, and the job ends **red**.
- Step 4: the image id matches the one noted in Prerequisites — the previous image is back.
- Step 5: `healthy`.
- Step 6: `::warning::deploy failed — restoring <target>` followed by
  `::error::deploy failed; rolled back to <target> and it verified`.
- The job is red even though the rollback succeeded. A recovered deploy is still a failed
  deploy.

## Also worth doing once
Stop and remove the `alfred` container entirely (simulating a from-scratch box with no
running container to record a rollback target from), then trigger a deploy with a broken
image. Confirm the `Record` step logs
`::notice::no running alfred container — first deploy, nothing to roll back to`, the
`Roll back` step logs `::error::deploy failed and there is no previous image to restore`
with `TARGET=none`, and the job still ends red rather than pretending a rollback happened.
