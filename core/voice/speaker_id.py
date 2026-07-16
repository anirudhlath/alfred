"""SpeakerID — voiceprint speaker identification (ECAPA-TDNN embeddings).

Enrollment: mean of normalized per-sample embeddings → Redis hash
VOICEPRINT_KEY (field = identity, value = float32 bytes).
Inference: cosine similarity of the utterance embedding vs all enrolled prints.

Input contract: 16 kHz, 16-bit, mono PCM bytes.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from loguru import logger

from shared.streams import VOICEPRINT_KEY, decode_stream_value

if TYPE_CHECKING:
    from collections.abc import Callable

    from shared.types import AioRedis

_DEFAULT_THRESHOLD = 0.45  # ECAPA cosine: same speaker ≈ 0.4-0.7+, different ≈ 0.0-0.25
_MODEL_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
_MODEL_DIR = Path("data") / "models" / "spkrec-ecapa-voxceleb"


@dataclass(frozen=True)
class SpeakerMatch:
    """Result of a speaker identification attempt."""

    identity: str
    confidence: float
    enrolled: bool


_UNKNOWN = SpeakerMatch(identity="unknown", confidence=0.0, enrolled=False)


class SpeakerID:
    """Voiceprint-based speaker identification backed by Redis storage."""

    def __init__(
        self,
        redis: AioRedis,
        *,
        threshold: float | None = None,
        device: str = "cpu",
        embed_fn: Callable[[bytes], np.ndarray] | None = None,
    ) -> None:
        self._redis = redis
        self._threshold = (
            threshold
            if threshold is not None
            else float(os.getenv("SPEAKER_ID_THRESHOLD", str(_DEFAULT_THRESHOLD)))
        )
        self._device = device
        self._embed_fn = embed_fn
        self._model: Any = None
        self._load_lock = asyncio.Lock()

    async def identify(self, audio_bytes: bytes) -> SpeakerMatch:
        """Identify the speaker of a 16 kHz s16 mono PCM utterance."""
        prints = await self._load_prints()
        if not prints:
            return _UNKNOWN
        embedding = await self._embed(audio_bytes)
        best_identity, best_score = "", -1.0
        for identity, print_vec in prints.items():
            score = float(np.dot(embedding, print_vec))
            if score > best_score:
                best_identity, best_score = identity, score
        if best_score < self._threshold:
            logger.debug("SpeakerID: best={} score={:.3f} < threshold", best_identity, best_score)
            return _UNKNOWN
        confidence = min(0.95, 0.7 + (best_score - self._threshold) * 0.5)
        return SpeakerMatch(identity=best_identity, confidence=confidence, enrolled=True)

    async def enroll(self, identity: str, audio_samples: list[bytes]) -> bool:
        """Enroll a voiceprint from one or more PCM samples."""
        if not audio_samples:
            return False
        embeddings = [await self._embed(s) for s in audio_samples]
        mean = np.mean(np.stack(embeddings), axis=0)
        mean = mean / (np.linalg.norm(mean) + 1e-10)
        await self._redis.hset(VOICEPRINT_KEY, identity, mean.astype(np.float32).tobytes())
        logger.info("Enrolled voiceprint for '{}' ({} samples)", identity, len(audio_samples))
        return True

    async def _load_prints(self) -> dict[str, np.ndarray]:
        raw = await self._redis.hgetall(VOICEPRINT_KEY)
        prints: dict[str, np.ndarray] = {}
        for key, value in raw.items():
            blob = value if isinstance(value, bytes) else value.encode()
            prints[decode_stream_value(key)] = np.frombuffer(blob, dtype=np.float32)
        return prints

    async def _embed(self, pcm: bytes) -> np.ndarray:
        if self._embed_fn is not None:
            return await asyncio.to_thread(self._embed_fn, pcm)
        await self._ensure_model()
        return await asyncio.to_thread(self._embed_ecapa, pcm)

    async def _ensure_model(self) -> None:
        if self._model is not None:
            return
        async with self._load_lock:
            if self._model is not None:
                return

            def _load() -> Any:
                from speechbrain.inference.speaker import EncoderClassifier

                return EncoderClassifier.from_hparams(
                    source=_MODEL_SOURCE,
                    savedir=str(_MODEL_DIR),
                    run_opts={"device": self._device},
                )

            self._model = await asyncio.to_thread(_load)
            logger.info("SpeakerID: loaded ECAPA model on {}", self._device)

    def _embed_ecapa(self, pcm: bytes) -> np.ndarray:
        import torch

        wav = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        tensor = torch.from_numpy(wav).unsqueeze(0)
        emb = self._model.encode_batch(tensor)  # [1, 1, 192]
        vec = emb.squeeze().detach().cpu().numpy().astype(np.float32)
        normalized: np.ndarray = vec / (np.linalg.norm(vec) + 1e-10)
        return normalized
