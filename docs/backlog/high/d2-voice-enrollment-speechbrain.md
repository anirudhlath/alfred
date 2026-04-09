# D2: Voice Enrollment (SpeechBrain)

## Summary
Speaker identification via voice enrollment using SpeechBrain.

## Context
Depends on D1 (WebAuthn). Requires audio sample collection, model training, and voiceprint storage. Currently `core/voice/speaker_id.py` returns hardcoded `confidence=0.0`.

## Acceptance Criteria
- Audio sample collection during enrollment
- SpeechBrain model training per user
- Voiceprint storage and retrieval
- Speaker ID returns real confidence scores
