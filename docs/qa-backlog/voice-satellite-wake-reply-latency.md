# Voice Satellite: Wake-to-Reply Latency (~4s Target)

**Feature:** Satellite voice loop latency (design target in `docs/superpowers/specs/2026-07-15-voice-satellite-design.md:153` — "wake → first reply audio under ~4s typical, cloud LLM is the long pole")
**Priority:** high
**Type:** functional

## Prerequisites
- Same live stack as `voice-satellite-real-mic-full-loop.md`: `bash scripts/dev-up.sh`,
  `home-service` on port 8000, real LLM API key configured, `config/satellites.yaml`
  pointed at `127.0.0.1`, macOS dev satellite running (sox mic/speaker), full stack via
  `uv run python -m runner`
- A stopwatch/phone timer, or use the channels/conscious log timestamps as the reference

## Test Steps
1. With the dev satellite connected, say the wake word immediately followed by a short
   command (e.g. "What time is it?")
2. Start timing the instant you finish speaking (utterance end) and stop timing the instant
   reply audio starts playing from the MacBook speakers
3. Repeat for 5 different short requests, recording each latency
4. Cross-check perceived latency against log timestamps: `Satellite 'dev-mac' heard:
   <transcript>` (STT complete) → conscious engine response → reply audio start in the
   channels log
5. Repeat once with a request that requires a tool call (e.g. "Turn off the kitchen lights")
   to see the added latency from the HomeAgent/home-service round trip

## Expected Result
- Median wake→first-reply-audio latency across the 5 short requests is under ~4s, matching
  the design spec's stated typical target
- No single request feels broken or unresponsive — a human's subjective "does this feel like
  a real assistant" judgment, since this can't be substituted by a unit-test threshold
- The tool-call request in step 5 is noticeably but not dramatically slower than the plain
  Q&A requests

## Notes
- The published target is explicitly "typical," not a hard SLA — record actual per-request
  numbers rather than a strict pass/fail, and note whether the LLM round-trip (cloud) or
  local STT/TTS (CPU) dominates elapsed time
- Record which LLM/model was configured; network conditions and MacBook CPU load will vary
  results run-to-run
- If latency regularly exceeds ~6-8s, file a `docs/backlog/` ticket rather than trying to fix
  it as part of this QA pass
