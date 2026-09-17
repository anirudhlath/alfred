# Publish Multi-Arch Images to a Registry

## Summary

`alfredctl build` always builds from source (staged from `git ls-files`) — there is no
prebuilt image anywhere. Publishing multi-arch (amd64 + arm64) images to GHCR would let
`alfredctl up`/`docker compose` pull instead of build, cutting first-run time from
"clone two repos and compile a fat multi-stage image" to "pull an image."

## Context / Motivation

- The containerization design spec explicitly scoped this out for v1: "Not publishing
  prebuilt images to a registry in this iteration (build-from-source; registry
  publishing is a later backlog item)"
  (`docs/superpowers/specs/2026-07-19-alfred-containerization-design.md` §1 Non-Goals).
- `container-build.yml` CI already builds the image on both `amd64` and `arm64` for
  every PR touching the Containerfile/`alfredctl`/`runner` — the multi-arch build matrix
  already exists, it just discards the result instead of pushing it.
- home-service is co-packaged into the image at build time (`alfredctl/staging.py`
  stages both repos together) — a published image would need a versioning/tagging
  scheme that accounts for both repos' commits, not just alfred's.

## Acceptance Criteria

- [ ] Decide the registry (GHCR under `ghcr.io/anirudhlath/alfred` is the natural
      choice given the repo's existing GitHub-centric tooling) and the tagging scheme
      (e.g. `latest`, release tags, and how a home-service commit pin is encoded/pinned
      alongside the alfred tag).
- [ ] `container-build.yml` (or a new release-triggered workflow) pushes the built
      amd64 + arm64 images as a multi-arch manifest on tag/release, not just on every
      PR.
- [ ] `alfredctl build` gains a mode (or a new `alfredctl pull`) that fetches the
      published image instead of building locally; `alfredctl up --build/--no-build`
      semantics extend naturally to "pull if missing."
- [ ] `docs/containerization.md` and the README quickstart document the pull-based path
      as the fast option, keeping build-from-source as the always-available fallback
      (self-hosters who don't trust a third-party registry, or who need a branch build).
- [ ] Image provenance/signing considered (e.g. `cosign`) given this becomes a
      supply-chain trust boundary once people start pulling instead of building.

## Also covers the CD path (added 2026-08-19)

CD currently builds on the runner (`alfredctl build` → `docker compose up -d`), which
works and needs no registry auth. Publishing would let the deploy job pull a digest built
on a GitHub runner instead of spending several minutes compiling a fat multi-stage image
on lath-server on every merge — and would make the deployed artifact byte-identical to the
one CI tested rather than a rebuild of the same source. It would also remove the deploy
job's one unauthenticated dependency: cold builds today pull `python:3.13-slim-bookworm`,
`redis:8-bookworm` and `node:22-slim` from Docker Hub anonymously
(`docs/backlog/low/deploy-anonymous-registry-pulls.md`).

Shape: `container-build.yml` pushes to GHCR on merge to `master`; the deploy job pulls by
digest and `docker compose up -d`; the rollback target becomes the previous digest rather
than the running container's local image id (today: tagged `alfred:rollback`, see
"Continuous deployment" in `docs/deployment.md`). See
`docs/superpowers/specs/2026-08-18-cd-local-runner-design.md` §11.
