# D20: Reflex Engine via DomainRouter

## Summary
Reflex bypasses DomainRouter and uses HomeAgent directly.

## Context
Should route through DomainRouter like Conscious Engine does, for consistency and to support multi-domain routing.

## Acceptance Criteria
- Reflex Engine routes actions through DomainRouter
- HomeAgent no longer directly coupled to Reflex
