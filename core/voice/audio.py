"""Audio decode helpers — any browser/HTTP upload to canonical 16 kHz mono PCM."""

from __future__ import annotations

import io


def decode_to_pcm16k(data: bytes) -> bytes:
    """Decode any PyAV-readable audio (webm/opus, wav, m4a…) to 16 kHz s16 mono PCM.

    PyAV ships with faster-whisper (voice extra). Import is local so the module
    can be imported without the extra installed.

    Note: resampled frame planes are backed by an internally over-allocated
    buffer whose length does not track the frame's valid sample count (verified
    against PyAV 18.0.0) — reading ``bytes(frame.planes[0])`` directly pulls in
    trailing garbage. ``to_ndarray()`` trims to ``frame.samples``, so it's used
    instead to get exact byte counts.
    """
    import av

    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    out = bytearray()
    with av.open(io.BytesIO(data), mode="r") as container:
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                out.extend(resampled.to_ndarray().tobytes())
    for resampled in resampler.resample(None):  # flush
        out.extend(resampled.to_ndarray().tobytes())
    return bytes(out)
