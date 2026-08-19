# `scripts/setup.sh` Is Not Idempotent

## Summary

Every satellite deploy re-runs the entire provisioning script — `apt-get update`/
`install`, a fresh `git clone` of `wyoming-satellite` from GitHub, and two virtualenv
rebuilds via network `pip install`s — even when nothing relevant changed since the last
successful deploy. That's four external-network dependencies per deploy (the apt mirror,
GitHub, and two `pip install`s against PyPI/GitHub), any one of which can turn a no-op
release into a failed rollout on the only voice satellite in the fleet today.

## Context / Motivation

- `scripts/setup.sh`'s `git clone` guard checks `${INSTALL_DIR}/wyoming-satellite`, but the
  deploy tool's step 4 always moves the previous `INSTALL_DIR` aside before `setup.sh`
  runs, so that directory is always freshly empty at the point the guard is checked — the
  clone runs unconditionally on every CD-triggered deploy, not just when
  `wyoming-satellite`'s pinned ref actually changed. Same for `apt-get` and both venvs:
  nothing is skipped based on whether the inputs changed.
- Found during Phase 5 documentation of the CD design. The current fleet is one device
  (`pi1`), so a transient network blip during any deploy is currently a single point of
  failure for the house's only voice satellite.

## Proposed shape

- Cache/reuse the `wyoming-satellite` clone and the two venvs across deploys when their
  pinned versions haven't changed (hash `config.env` + a version-pin file, skip the rebuild
  when it matches).
- Or, short of full idempotency: retry each network step a few times before failing the
  whole device, so a single transient blip doesn't force a rollback.

## Acceptance Criteria

- [ ] A deploy where nothing changed in the fleet's fixed inputs (script version,
      `wyoming-satellite` pin, `config.env`) does not re-clone or re-`pip install`.
- [ ] A transient failure of any one external network dependency retries before the device
      is reported as failed.
