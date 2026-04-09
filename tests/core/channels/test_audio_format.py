import base64

from core.channels.web_server import _decode_audio


def test_decode_audio_webm_returns_format() -> None:
    raw = b"fake-webm-data"
    data_url = f"data:audio/webm;base64,{base64.b64encode(raw).decode()}"
    audio_bytes, fmt = _decode_audio(data_url)
    assert audio_bytes == raw
    assert fmt == "webm"


def test_decode_audio_aac_returns_format() -> None:
    raw = b"fake-aac-data"
    data_url = f"data:audio/aac;base64,{base64.b64encode(raw).decode()}"
    audio_bytes, fmt = _decode_audio(data_url)
    assert audio_bytes == raw
    assert fmt == "aac"


def test_decode_audio_wav_returns_format() -> None:
    raw = b"fake-wav-data"
    data_url = f"data:audio/wav;base64,{base64.b64encode(raw).decode()}"
    audio_bytes, fmt = _decode_audio(data_url)
    assert audio_bytes == raw
    assert fmt == "wav"


def test_decode_audio_m4a_returns_format() -> None:
    raw = b"fake-m4a-data"
    data_url = f"data:audio/m4a;base64,{base64.b64encode(raw).decode()}"
    audio_bytes, fmt = _decode_audio(data_url)
    assert audio_bytes == raw
    assert fmt == "m4a"


def test_decode_audio_no_mime_defaults_to_wav() -> None:
    raw = b"bare-data"
    bare_b64 = base64.b64encode(raw).decode()
    audio_bytes, fmt = _decode_audio(bare_b64)
    assert audio_bytes == raw
    assert fmt == "wav"
