# VoiceButton: Safari MediaRecorder Codec Support

**Feature:** VoiceButton (web/src/chat/VoiceButton.tsx)
**Priority:** medium

## Summary

`VoiceButton` hard-codes `mimeType: "audio/webm;codecs=opus"` in the `MediaRecorder` constructor.
Safari's `MediaRecorder` does not support WebM — the constructor throws, and the button permanently
shows "Microphone unavailable" on Safari desktop and iOS WKWebView. This was carried over from the
old `app.js` implementation and is not a regression from the React rebuild.

## Context / Motivation

The backend (`core/channels/web_server.py`, `_ALLOWED_AUDIO_FORMATS`) already accepts
`aac`, `m4a`, and `wav` in addition to `webm`, so there is no server-side work required.
The fix is purely frontend: select the best supported MIME type at runtime instead of
hard-coding `audio/webm;codecs=opus`.

Preference order: `audio/webm;codecs=opus` (Chrome/Firefox) → `audio/mp4` (Safari macOS 14.4+) →
`audio/aac` (Safari fallback) → no `mimeType` key (browser default, last resort).

## Acceptance Criteria

- [ ] `VoiceButton` calls `MediaRecorder.isTypeSupported()` over the ordered candidate list and
  passes the first supported type (or omits `mimeType` if none match) to the `MediaRecorder`
  constructor.
- [ ] The `Blob` type passed to `new Blob(chunks, { type: ... })` in `rec.onstop` matches the
  selected MIME type.
- [ ] Voice recording works in Safari desktop (macOS 14+): mic permission granted, recording
  starts, audio is sent and transcribed correctly.
- [ ] Voice recording still works correctly in Chrome and Firefox (webm/opus preferred).
- [ ] No permanent "Microphone unavailable" error shown in Safari when the codec is merely
  unsupported (only show the error on genuine `getUserMedia` denial).
