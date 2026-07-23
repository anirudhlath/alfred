"""TTS backend registry — the single source of truth for selectable adapters.

Adding a backend is one entry here (module + class as strings, so importing this
module pulls in no heavy optional deps) plus a ``TTSBackend`` subclass. Selection
is config-driven via ``AlfredConfig.tts_backend`` (see ``core.channels.voice_models``).
"""

from __future__ import annotations

from loguru import logger

# name -> (module, class_name, missing_dep_msg)
TTS_BACKENDS: dict[str, tuple[str, str, str]] = {
    "kokoro": ("core.voice.tts_kokoro", "KokoroTTS", "kokoro-onnx not installed"),
    "piper": ("core.voice.tts", "PiperTTS", "piper-tts not installed"),
}

DEFAULT_TTS_BACKEND = "kokoro"


def resolve_backend_order(selected: str) -> list[str]:
    """Return backend names to try: ``selected`` first, then the rest as fallbacks.

    An unknown name logs a warning and falls back to ``DEFAULT_TTS_BACKEND`` first.
    """
    if selected not in TTS_BACKENDS:
        logger.warning(
            "Unknown TTS backend {!r} — falling back to {!r}", selected, DEFAULT_TTS_BACKEND
        )
        selected = DEFAULT_TTS_BACKEND
    rest = [name for name in TTS_BACKENDS if name != selected]
    return [selected, *rest]
