# Voice Round-Trip: Mic → Transcription → TTS Playback

**Feature:** Chat voice input / audio response  
**Priority:** critical  
**Type:** functional

## Prerequisites

- Alfred runner fully started (`uv run python -m runner`)
- Real browser with microphone access (Chrome or Safari on macOS)
- Ollama running with a model loaded (e.g. `gpt-oss:20b`)
- Whisper STT and Piper TTS configured (auto-downloaded on first use)
- Navigate to `http://localhost:8081` and be authenticated (passkey login)

## Test Steps

1. Open the Chat page (`/`).
2. Locate the microphone button (circular icon button to the right of the text input in the Composer).
3. Click the microphone button — observe the button border turns `reflex` blue and a pulsing glow appears (level meter active).
4. Speak a short sentence (e.g. "What's the weather like today?").
5. Click the microphone button again (now shows a Stop/Square icon) to stop recording.
6. Observe: a transcription message appears in the chat thread immediately after stop.
7. Observe: a "thinking" indicator (pulsing reflex dot + "thinking" label) appears below the transcription.
8. Wait for Alfred's response — a text message should appear from Alfred.
9. If TTS is active, the response should be accompanied by audio playback through the browser speaker.
10. Confirm the microphone button returns to its default state (Mic icon, no glow).

## Expected Result

- Step 3: Microphone button glows blue; audio level meter pulses with voice amplitude.
- Step 6: A `transcription` message from the WebSocket appears in the thread with the spoken text.
- Step 7: "thinking" indicator visible while the Conscious Engine processes.
- Step 8: Alfred response text appears.
- Step 9: TTS audio plays (WAV, ~1–3s latency). If Piper TTS is not configured, audio is absent but text response still succeeds.
- Step 10: Button resets cleanly; no error state.

## Notes

- VoiceButton records in `audio/webm;codecs=opus` and sends a base64 data URL over the chat WebSocket.
- Microphone permission must be granted on first use — browser will prompt.
- If mic is unavailable (e.g. no device or permission denied), the button border turns `bad` red and the button tooltip reads "Microphone unavailable". This is the error path, not the happy path.
- The re-entrancy guard (`starting.current`) prevents double-click during the permission prompt.
- AudioContext and MediaRecorder are cleaned up on stop and on component unmount.
