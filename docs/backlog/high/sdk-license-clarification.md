# Resolve alfred-sdk AGPL/MIT License Contradiction

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** high
**Severity (audit):** high
**Source:** Public-release readiness audit 2026-07-18 (findings #8, #16)

## Summary
`alfred-sdk` has no license of its own, so it inherits the monorepo's `AGPL-3.0-or-later`,
yet it is bundled into repos published under MIT (`home-service`) and declared as a
dependency by others (`signal-bridge`). Distributing `home-service` container images
therefore ships AGPL code under an MIT label, and any "sovereign" third-party app that
consumes the SDK silently inherits AGPL obligations — directly undercutting the
decoupled/permissive-integration pitch. Because `home-service` is **already public**
(v0.1.0, 2026-07-16), this is a post-exposure legal-contradiction cleanup, not a
pre-publication check — though for the local-only `signal-bridge` it can still be fixed
before that repo is ever pushed.

## Context / Motivation
- `alfred/pyproject.toml` line 5 sets `license = "AGPL-3.0-or-later"`, and `sdk/` lives
  inside that repo and is listed in its setuptools packages.
- `alfred/sdk/pyproject.toml` has **no `license` field**, and there is **no `sdk/LICENSE`
  file** — so `alfred-sdk` is AGPL by default (inherits the monorepo license).
- `home-service` is published under MIT (`home-service/LICENSE`:
  `MIT License, Copyright (c) 2025-2026 Anirudh Lath`) yet depends on `alfred-sdk>=0.1.0`.
  Since the SDK is not on PyPI, `home-service/Containerfile` copies `alfred/sdk/` into the
  image ("Install alfred-sdk from monorepo source (not on PyPI)") and
  `home-service/alfred_ext/register.py` imports `alfred_sdk` directly. The published image
  thus bundles AGPL code under an MIT label.
- `signal-bridge/pyproject.toml` also declares an `alfred-sdk` dependency, extending the
  same contradiction (this repo is still local-only per the epic, so it is pre-exposure).
- Scope note: the CLA already preserves the maintainer's right to relicense `sdk/`, so the
  permissive-exception path is available without contributor re-consent.
- Locations: `alfred/sdk/pyproject.toml`, `alfred/pyproject.toml`, `alfred/LICENSE`,
  `home-service/LICENSE`, `home-service/Containerfile`, `home-service/alfred_ext/register.py`,
  `home-service/pyproject.toml`, `signal-bridge/pyproject.toml`.

## Acceptance Criteria
- [ ] An explicit licensing decision for `sdk/` is made and recorded: either (a) keep the
      SDK permissive so integrations stay permissive, or (b) keep it AGPL and make every
      consumer's AGPL inheritance explicit.
- [ ] If path (a): `alfred/sdk/pyproject.toml` declares an explicit permissive `license`
      field (`MIT` or `Apache-2.0`) and a matching `sdk/LICENSE` file is added.
- [ ] If path (a): `alfred/README` and `CONTRIBUTING` document that `sdk/` is the
      permissive exception to the AGPL monorepo.
- [ ] If path (b): `home-service` (and `signal-bridge`) are relabeled/relicensed away from
      MIT, and it is documented that SDK consumers inherit AGPL obligations.
- [ ] No published or buildable repo bundles AGPL SDK code under an MIT label — the
      `home-service` MIT `LICENSE` no longer contradicts the SDK it copies in its
      `Containerfile`.
