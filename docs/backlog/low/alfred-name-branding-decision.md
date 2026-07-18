# Decide "Alfred" Branding: Trademark & Package-Namespace Collisions

**Epic:** [Alpha Release Readiness](../epics/alpha-release.md)
**Priority:** low
**Severity (audit):** low
**Source:** Public-release readiness audit 2026-07-18 (findings #35, #63, #64)

## Summary
The project ships as plain "Alfred", which collides on three fronts: registered
trademarks in adjacent categories (finding #63), taken package-registry namespaces
(finding #35, rated medium), and a heavily diluted OSS "Alfred AI butler" name space
(finding #64). None of the findings assert active infringement — #64 is explicitly a
discoverability/mutual-confusion problem, not a legal one — and there is no code-level
rename required; the recommendation is to keep the name and de-risk. Because the three
public repos are already exposed (`anirudhlath/alfred` shipped v0.1.0 on 2026-07-16),
this is post-exposure cleanup on live repos, not a pre-publication gate.

## Context / Motivation

**Trademark crowd (#63).** The project presents as bare "Alfred" everywhere with no
non-affiliation disclaimer in any of the four repo READMEs (a grep for
`trademark`/`affiliated`/`disclaimer` across them returned nothing):
- `alfred/README.md` — H1 `# Alfred`
- `alfred/pyproject.toml` — `name = "alfred"`
- `alfred/docs/PRD.md` line 77 — wake word "Hey Alfred"
- `alfred-ios/README.md`
- `alfred-ios/App/Alfred/Resources/Info.plist` — `CFBundleDisplayName`/`CFBundleName` = "Alfred"

The finding cites 3+ active registered marks in directly adjacent categories, including
Alfred (Running with Crayons Ltd — macOS launcher/productivity software, Mac App Store id
405843582…), AlfredCamera (Alfred Systems Inc.), and an Alfred smart-lock mark. (The audit
detail is truncated at the source, so the full mark list is not reproduced here.)

**Registry collisions (#35, medium).** Verified live against registries on 2026-07-18:
- PyPI `alfred` is TAKEN — "Utilities for Alfred script filters" by Mike Spindel, v0.3
  (2020-12-30), keywords `alfred alfredapp script filter` (a helper for the trademarked
  macOS app). The monorepo's `pyproject` `name = "alfred"` can therefore never be published
  to PyPI under its current name.
- npm `alfred` is taken.
- The Homebrew cask `alfred` IS the trademark holder's product.
- Only `alfred-sdk` is currently free on PyPI.
- No immediate breakage: docs instruct `uv pip install -e .`, not `pip install alfred`, so
  no user currently pulls the unrelated package — but a reader who runs `pip install alfred`
  would get Spindel's package. LOC: `alfred/pyproject.toml`, `alfred/sdk/pyproject.toml`.

**OSS namespace dilution (#64, low, non-legal).** A crowded field of existing open-source
"Alfred" AI-butler/voice-assistant projects — `github.com/ssdavidai/alfred` (self-hosted
agentic infra, pip-installable), `masrad/ALFRED` (LangChain voice assistant),
`eriklindernoren/HomeAssistant` ("Alfred - Domestic butler"), `jllopes/Alfred`, `alfred-ai`
on SourceForge, plus multiple 2026 blog builds of offline "Alfred" butlers on the exact same
stack (Ollama + Whisper + Piper). Consequence is a search/discoverability cost and
mutual confusion, not a legal exposure.

## Acceptance Criteria
- [ ] A short branding decision is recorded (keep "Alfred" + de-risk, or rename) covering all three fronts.
- [ ] README H1s reframed to "Project Alfred" in `alfred/README.md` and `alfred-ios/README.md`.
- [ ] A non-affiliation note is added to the public READMEs, naming the conflicting marks — e.g. "Project Alfred is a personal, self-hosted open-source project. It is not affiliated with, endorsed by, or related to Alfred (Running with Crayons Ltd), AlfredCamera (Alfred Systems Inc.), or the Alfred smart-lock products."
- [ ] Public GitHub repo "About" descriptions carry the tagline ("ambient, voice-first multi-agent system"), not just "Alfred", so search results and citations disambiguate.
- [ ] An SDK distribution-name decision is recorded: either (a) defensively register `alfred-sdk` on PyPI now with a stub release to prevent dependency-confusion squatting, or (b) pick a scoped, non-colliding name (e.g. `alfred-home-sdk`, `projectalfred-sdk`, or a distinctive coinage).
- [ ] Confirm no docs instruct `pip install alfred` (current install path is `uv pip install -e .`); keep it that way while `name = "alfred"` remains unpublishable to PyPI.
