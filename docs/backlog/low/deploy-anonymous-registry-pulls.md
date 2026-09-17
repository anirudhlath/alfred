# Deploy Job Has No `docker login` — Cold Builds Pull Anonymously

## Summary

The fat image's base layers (`python:3.13-slim-bookworm`, `redis:8-bookworm`,
`node:22-slim`) are pulled from Docker Hub with no authentication anywhere in the deploy
job. A cold build — e.g. right after the `Prune old deploy images` step's
`docker builder prune`, or on a fresh runner — could fail on Docker Hub's per-IP anonymous
pull-rate limit, most likely on a day when several other things sharing this box's public
IP also cold-pull from Docker Hub.

## Context / Motivation

- `alfredctl build` shells out to `docker build` with no `docker login` step anywhere in
  `ci.yml`'s `deploy` job.
- Fails safe today: a rate-limited pull fails the `Build` step, which runs before `Start`,
  so rollback correctly does not fire (nothing was replaced) and the job is simply red with
  no outage. The risk is a noisy, confusing failure, not downtime.
- lath-server also runs several other Docker Hub–pulling projects (usher, ha-home-panel,
  comfyui) sharing the same public IP's rate-limit bucket.

## Proposed shape

- Add a `docker login` step using a Docker Hub PAT (read-only, e.g.
  `secrets.DOCKERHUB_TOKEN`) before `Build`, or mirror the three base images into GHCR and
  repoint the `Containerfile`.

## Acceptance Criteria

- [ ] The deploy job authenticates to Docker Hub (or no longer needs to) before the
      `Build` step.
- [ ] A cold build (`docker system prune -a` first, as a manual drill) succeeds without
      hitting a pull-rate error.
