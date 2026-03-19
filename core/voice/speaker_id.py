"""SpeakerID — voiceprint-based speaker identification (stub).

Full implementation requires:
- Enrollment: capture voiceprint embeddings during onboarding
- Inference: compare incoming audio against stored voiceprints
- Storage: Redis hash `alfred:identity:voiceprint`

This stub defines the interface for integration with IdentityGate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeakerMatch:
    """Result of a speaker identification attempt."""

    identity: str
    confidence: float
    enrolled: bool


class SpeakerID:
    """Voiceprint-based speaker identification.

    Stub — returns unknown identity with zero confidence.
    Full implementation planned for Phase 3.5.
    """

    async def identify(self, audio_bytes: bytes) -> SpeakerMatch:
        """Identify the speaker from audio.

        Args:
            audio_bytes: Raw audio data containing speech.

        Returns:
            SpeakerMatch with identity and confidence.
        """
        _ = audio_bytes
        return SpeakerMatch(identity="unknown", confidence=0.0, enrolled=False)

    async def enroll(self, identity: str, audio_samples: list[bytes]) -> bool:
        """Enroll a speaker's voiceprint.

        Args:
            identity: Identity label (e.g., "sir").
            audio_samples: List of audio samples for enrollment.

        Returns:
            True if enrollment succeeded.
        """
        _ = identity, audio_samples
        return False
