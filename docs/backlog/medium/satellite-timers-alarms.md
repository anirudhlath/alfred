# Satellite Timers & Alarms

## Summary

Local timer/alarm support on voice satellites — "Hey Alfred, set a 10 minute timer" rings
on the satellite it was set from, even if the server hiccups mid-countdown.

## Context / Motivation

Kitchen timers are the #1 real-world use of Alexa-class devices. Deferred from the v1
voice satellite design (`docs/superpowers/specs/2026-07-15-voice-satellite-design.md` §9)
because it needs local state on the Pi (timer must survive a server restart) plus chime
assets and a cancel/query flow. Note: `wyoming-satellite` has protocol-level timer event
support worth evaluating before building anything custom.

## Acceptance Criteria

- Setting a timer by voice registers it and confirms verbally with the duration.
- The timer rings on the originating satellite at expiry, including if the Alfred server
  restarted after the timer was set.
- Timers can be cancelled and queried by voice ("how long left on my timer?").
- Alarm (absolute time) variant works the same way.
- Timer creation composes with the existing Trigger Engine where possible (fluid
  intelligence pillar) rather than a parallel bespoke scheduler.
