# D24: Client-Side Geolocation for Weather

## Summary
Weather integration uses hardcoded lat/long from .env.

## Context
Should use browser Geolocation API, pass coordinates with request context, and fall back to configured default.

## Acceptance Criteria
- Frontend requests geolocation permission
- Coordinates passed with weather-related requests
- Fallback to .env configured coordinates
