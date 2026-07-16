# Voice Enrollment Card Polish

## Summary

Three small UX gaps in `web/src/pages/VoiceEnrollmentCard.tsx` from the v1 voice-satellite
review (2026-07): the mic button stays clickable while `status === "submitting"` (a 4th
recording can fire a second submit), the "Enrollment failed — retry" message lingers while
new samples are being recorded, and the status region has no `aria-live` so state changes
aren't announced to screen readers.

## Context / Motivation

Non-blocking polish deferred from the voice-satellite bridge branch. All three are
contained in one component. The double-submit is harmless server-side (backend accepts 3-5
samples and re-enroll overwrites), but the UX is confusing.

## Acceptance Criteria

- Mic control disabled (or hidden) during submit.
- Error state clears as soon as a new sample is recorded.
- Status region announces submitting/enrolled/error via `aria-live="polite"`.
- Existing vitest coverage extended for the three behaviors.
