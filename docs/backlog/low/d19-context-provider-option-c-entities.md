# D19: Context Provider Option C Entities

## Summary
home-service only exposes lights + scenes, not automations/scripts/input_booleans.

## Context
Full Home Assistant context requires exposing additional entity types for richer reasoning.

## Acceptance Criteria
- home-service exposes automations, scripts, input_booleans
- Context provider includes these in system prompt
- Reflex/Conscious can reason about and control these entities
