# Home-Service `repository_dispatch` Chain, End to End

**Feature:** Continuous deployment (Phase 3 — home-service dispatches an alfred redeploy)
**Priority:** high
**Type:** functional

## Prerequisites
- `ALFRED_DISPATCH_TOKEN` is minted (a fine-grained PAT scoped to `contents: write` on
  `anirudhlath/alfred` alone) and set as a repository secret on `alfred-home-service`.
- `alfred-home-service#19` ("ci: dispatch an alfred redeploy on every merge to main") is
  merged.
- The `alfred-deploy` runner is Idle.

## Test Steps
1. Note the currently running alfred image: `docker inspect -f '{{.Image}}' alfred`.
2. Merge a trivial PR to `alfred-home-service`'s `main`.
3. Watch home-service's `dispatch` job: `gh run watch --repo anirudhlath/alfred-home-service`.
4. Confirm it posts successfully to `repos/anirudhlath/alfred/dispatches` with
   `event_type: home-service-merged` (the step logs `HTTP 204` on success).
5. Watch alfred's resulting run:
   `gh run list --repo anirudhlath/alfred --event repository_dispatch`.
6. After it finishes: `docker inspect -f '{{.Image}} {{.Created}}' alfred`, and confirm the
   home-service change is observably present (e.g. a log line or behavior change from the
   merged PR).

## Expected Result
- Step 3: home-service's `gate` (`ci-ok`) green, then `dispatch` green.
- Step 4: `HTTP 204`, no `::error::ALFRED_DISPATCH_TOKEN is not set` line.
- Step 5: a new alfred run appears with `event_name: repository_dispatch`, not `push` —
  this is the only trigger that fires without a corresponding alfred commit.
- Step 6: a fresh `Created` timestamp (alfred rebuilt with the new home-service sibling)
  and the home-service change is observably present.
- The whole chain — home-service merge → dispatch → alfred rebuild and redeploy —
  completes with no human running a command by hand.

## Notes
This is the one CD path that spans two repos and a bearer-token network call between them;
it has never run for real as of this writing — `ALFRED_DISPATCH_TOKEN` has not been minted,
so `alfred-home-service#19`'s dispatch step fails by design
(`::error::ALFRED_DISPATCH_TOKEN is not set — alfred will not be redeployed`) rather than
silently no-op'ing. This drill is what proves the last untested leg of the design.
