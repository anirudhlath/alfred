# Landing Page + Launch Posts

## Summary

Public-launch marketing kit: a minimal landing page and drafted launch posts
(Show HN, r/selfhosted, r/homeassistant).

## Context / Motivation

Launch-readiness assessment (2026-07-18): currently zero marketing artifacts.
PRD §1 already reads as positioning copy ("treats the cloud as a hired
specialist, not a landlord") — the landing page is largely assembly of PRD
prose + README media ticket assets. Gate on HA Plans 2–3 + manual QA passing,
so the marquee claim ("paste your Home Assistant token, Alfred knows your
home") is true and demoable on launch day.

## Acceptance Criteria

- [ ] Single-page site (static; GitHub Pages is fine) with: one-line pitch,
      demo GIF/video, three capability highlights, hardware requirements
      honesty (GPU), AGPL/self-hosted framing, repo + getting-started links.
- [ ] Drafted launch posts tailored per venue (Show HN plain-technical tone;
      r/selfhosted setup-focused; r/homeassistant integration-focused), each
      ending with what feedback is wanted.
- [ ] Launch checklist: getting-started verified on a clean machine, compose
      profile green, qa-backlog empty of criticals, issue templates on.

## Draft framing (2026-07-19)

Positioning lives in **PRD §6 "Why Alfred is different"** — the launch copy is
an assembly of that section, not a new argument. The core thesis: Alfred is the
only assistant that is **private, proactive, memory-rich, and physically
embodied at the same time.** Home control is where a butler *starts*, not what a
butler *is*; the value is one memory + one reasoning loop *crossing* domains.

Venue set follows the launch-milestone spec
(`2026-07-18-reddit-launch-milestone-design.md`): **r/selfhosted →
r/homeassistant → r/LocalLLaMA**, staggered (this supersedes the "Show HN"
placeholder in the Summary above; Show HN can trail as a later venue).

**Shared hook (the lede every post can open from):**

> Every voice assistant makes you pick two of four: a real brain, the ability to
> act on your house, a memory of your life, and staying on hardware you own.
> Home Assistant Voice acts but doesn't remember; Alexa and Gemini do both but
> live in the cloud and sell the context; ChatGPT reasons brilliantly but can't
> turn on a light. Alfred is a self-hosted butler that does all four at once — a
> fast local reflex for the house, a frontier mind for judgment, a three-layer
> memory that learns your routines, and nothing leaving hardware you control.

**Per-venue angle (which face of the thesis leads):**

- **r/selfhosted** — sovereignty + cost: runs on your box + one GPU, AGPL, cloud
  is a metered specialist not a landlord, killing Alfred leaves the home working.
  Lead with "own it, don't rent it."
- **r/homeassistant** — Alfred is the *judgment layer above HA*, not a
  replacement: paste a token, it discovers every room/device, streams live
  state, and reacts (sensor→action reflex) — no hand-written automations. Be
  explicit that HA stays the device layer.
- **r/LocalLLaMA** — the System 1 / System 2 split: local SLM reflex (<500 ms)
  vs. frontier conscious mind, three-layer memory, and the fluid→crystallized
  lifecycle (novel behavior composed once, promoted to a reflex). Lead with the
  architecture.

Each post ends by naming the feedback wanted (per existing acceptance criteria).
