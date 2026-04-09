# D14: Nested OTel Spans + Trace Propagation

## Summary
`@traced` exists but no span nesting or Redis propagation.

## Context
Traces don't follow events across process boundaries (e.g., Reflex → HomeAgent → home-service).

## Acceptance Criteria
- Nested spans within processes
- Trace context propagated through Redis Streams
- End-to-end traces visible in SigNoz
