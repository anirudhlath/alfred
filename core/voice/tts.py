"""PiperTTS — text-to-speech using Piper (local, streaming-capable)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from shared.traced import traced


class PiperTTS:
    """Text-to-speech using Piper (local, upgradeable to cloud TTS).

    Piper runs as a subprocess — no Python bindings needed.
    Voice models are downloaded separately to a configurable directory.
    """

    DEFAULT_VOICE = "en_GB-alan-medium"

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        piper_bin: str = "piper",
        model_dir: str = "core/voice/models",
    ) -> None:
        self._voice = voice
        self._piper_bin = piper_bin
        self._model_dir = Path(model_dir)

    @traced(name="voice.tts.synthesize")
    def synthesize(self, text: str) -> bytes:
        """Synthesize text to WAV audio bytes.

        Args:
            text: Text to speak.

        Returns:
            Raw WAV audio bytes.
        """
        model_path = self._model_dir / f"{self._voice}.onnx"

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            cmd = [
                self._piper_bin,
                "--model",
                str(model_path),
                "--output_file",
                str(tmp_path),
            ]
            proc = subprocess.run(
                cmd,
                input=text.encode(),
                capture_output=True,
                timeout=30,
            )
            if proc.returncode != 0:
                logger.error("Piper TTS failed: {}", proc.stderr.decode())
                raise RuntimeError(f"Piper TTS failed: {proc.stderr.decode()}")

            return tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)

    def synthesize_streaming(self, text: str) -> subprocess.Popen[bytes]:
        """Start a streaming TTS process. Returns Popen with stdout as audio stream.

        The caller reads from proc.stdout in chunks for low-latency streaming.
        """
        model_path = self._model_dir / f"{self._voice}.onnx"
        proc: subprocess.Popen[bytes] = subprocess.Popen(
            [
                self._piper_bin,
                "--model",
                str(model_path),
                "--output-raw",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.stdin:
            proc.stdin.write(text.encode())
            proc.stdin.close()
        return proc
