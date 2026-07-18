# Voice Model Configurability (Whisper size, Piper voice, cache locations)

## Summary

The voice stack is entirely hardcoded: Whisper model/device/compute, Piper
voice and prosody, and model cache locations. These are among the most
personal and hardware-dependent choices in the system. From the 2026-07-18
configurability audit.

## Context / Motivation

- `WhisperSTT` defaults `large-v3-turbo` / device `auto` / compute `auto` /
  `beam_size=5` (`core/voice/stt.py:22,26-28,60`) and is instantiated with
  zero args (`core/channels/web_server.py:61`). Model size is the single
  biggest VRAM/latency lever a self-hoster has.
- `PiperTTS` is pinned to `en_GB-alan-medium` with fixed prosody
  (`length_scale=0.75`, etc.) (`core/voice/tts.py:20,62,69-71`) — voice and
  accent are highly personal.
- Piper downloads voice models INTO the package tree (`core/voice/models/`,
  `tts.py:63`) — surprising and breaks read-only installs.
- No handling of HF cache env (`HF_HOME` / `SENTENCE_TRANSFORMERS_HOME`):
  multi-GB downloads silently land in `~/.cache/huggingface`.

## Acceptance Criteria

- [ ] Env-configurable via `AlfredConfig`: `WHISPER_MODEL`, `WHISPER_DEVICE`,
      `WHISPER_COMPUTE_TYPE`, `PIPER_VOICE` (and a considered decision on
      exposing prosody knobs vs keeping curated defaults).
- [ ] Piper model cache moves out of the package tree to the unified data
      dir (see unified-data-dir ticket) or an explicit cache dir var.
- [ ] Model cache locations documented in `.env.example` + getting-started
      (how to redirect HF caches; disk-space expectations per model choice).
- [ ] Auto-download behavior preserved (user preference: never require
      manual model downloads).
- [ ] Voice adapters read config once at construction (lazy loaders in
      `core/channels/voice_models.py` pass config through).

## Notes

- Speaker-ID (ECAPA) threshold/model became real in the voice-satellite work
  (`SPEAKER_ID_THRESHOLD` default 0.45) — include it here when configuring,
  it has the same "personal hardware tuning" character.
