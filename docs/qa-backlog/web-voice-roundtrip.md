# Voice Round-Trip: Hold → Transcription → Reply → TTS Playback

**Feature:** Hold-to-talk and spoken replies in the Room
(`web/src/room/HoldToTalk.tsx`, `web/src/lib/recorder.ts`, `web/src/lib/audio.ts`)
**Priority:** critical
**Type:** functional

## Prerequisites

- Alfred runner fully started (`uv run python -m runner`)
- Real browser with microphone access (Chrome or Safari on macOS; Safari is the one that
  matters — it has no WebM)
- Ollama running with a model loaded (e.g. `gpt-oss:20b`)
- Whisper STT and Piper TTS configured (auto-downloaded on first use)
- Navigate to `http://localhost:8081`, signed in, standing in the Room

## Test Steps

1. Tap once anywhere first (this unlocks the shared `AudioContext` — see
   [`audio-context-unlock-before-first-urgent-notification.md`](audio-context-unlock-before-first-urgent-notification.md)).
2. Locate the round microphone button to the right of the composer field (56 px, accent
   border, `aria-label="Hold to talk"`). With the field empty it is the microphone; type a
   draft and the same slot becomes **Send** instead.
3. **Press and hold** the button — it is a hold, not a click. Observe: the button fills with
   the accent colour and scales up, the mark becomes four animated bars, and a caption appears
   above the composer reading `recording 0:0X · release to send`.
4. While holding, observe the presence field responding to your voice — `HoldToTalk` attaches
   the recorder's analyser to `PresenceSignal`, and the headline changes to the holding line.
5. Speak a short sentence (e.g. "What's the weather like today?"), then **release**.
6. Observe: a dashed right-aligned bubble reading `Transcribing…` with
   `audio sent · N.N s · waiting on server` underneath.
7. Observe: a thinking row reading `conscious mind · working`.
8. Observe: the dashed bubble is replaced by your words as a normal `you` bubble when the
   `transcription` frame arrives, and Alfred's reply follows as plain text with a meta line
   `{mood} · {tools or "no tools"} · HH:MM`.
9. If TTS is active, the reply plays through the shared `AudioContext`.
10. Slide your finger off the button before releasing, mid-sentence — the take must still end
    and still send (the button captures the pointer).
11. Press and release inside one second — nothing is sent and the caption clears.

## Expected Result

- Step 3: hold state is visible within a frame; the caption counts seconds.
- Step 6: the dashed bubble is a `role="status"` so a screen reader hears the recording went.
- Step 7: `conscious mind · working` — the client cannot know which tools the turn will run
  until the reply arrives, so no tool name appears here.
- Step 8: transcription replaces the dashed bubble in place; Alfred's row is plain text
  (`pre-line`), never markdown.
- Step 9: TTS audio plays (WAV, ~1–3 s latency). If Piper is not configured, audio is absent
  but the text reply still succeeds.
- Step 10: releasing off-target still delivers — no take that runs for ever.
- Step 11: a sub-second hold is treated as a mis-tap (`MIN_HOLD_MS = 1000`); the recorder is
  still stopped and the microphone indicator clears.
- After every take, the presence field and headline return to their idle state.

## Notes

- `pickMimeType()` negotiates `audio/mp4` → `audio/aac` → the browser default. **Never WebM**:
  Safari has none and throws from the `MediaRecorder` constructor if asked. The blob is sent
  as a `data:audio/…;base64,…` URL over the chat WebSocket with `channel: "web_pwa"`.
- Microphone permission is requested on the first hold. If it is refused, or there is no
  device, `HoldToTalk` unwinds quietly — there is no error badge on the button; the composer
  is still there and the user can type. Confirm the microphone indicator goes out.
- The button is `disabled` while the chat socket is not online.
- Ending the hold while `getUserMedia` is still prompting must release the stream the prompt
  eventually opens — watch that the OS microphone indicator goes out rather than staying lit
  until the tab closes.
