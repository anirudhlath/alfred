# D15: Logging File + OTLP Sinks

## Summary
Console-only logging. No file rotation, no OTLP export.

## Context
Production needs persistent log files with rotation and OTLP export to SigNoz.

## Acceptance Criteria
- File sink with rotation (configurable size/count)
- OTLP log export to SigNoz
- Console sink remains for dev
