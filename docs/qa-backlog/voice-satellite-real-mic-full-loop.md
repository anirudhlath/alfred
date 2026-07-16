# Voice Satellite: Real-Mic Full Voice Loop (Wake → Transcript → Reply)

**Feature:** Satellite Bridge full voice loop (`core/channels/satellite/bridge.py`, `pipeline.py`)
**Priority:** critical
**Type:** e2e

## Prerequisites
- Redis Stack + Mosquitto running: `bash scripts/dev-up.sh` (from `alfred/` repo root)
- `home-service` running against a real/dev Home Assistant instance:
  `cd ../home-service && uv run uvicorn app.server:app --port 8000` (needs `HA_TOKEN`)
- A real LLM API key configured for the Conscious Engine (LiteLLM/OpenRouter) — this must be
  the actual cloud LLM, not the evals' mocked backend
- `config/satellites.yaml` created from `config/satellites.yaml.example` with one entry
  pointed at `127.0.0.1` (e.g. `name: dev-mac`, `area: Office` to match the dev satellite
  script's `--area`)
- A running macOS dev satellite using the MacBook's real mic/speakers:
  `brew install sox`, then either run `alfred-satellite/scripts/dev-satellite-macos.sh` if
  that planned sibling repo has been scaffolded (`docs/superpowers/plans/2026-07-16-alfred-satellite-repo-plan.md`
  Task 4), or run the two commands it wraps directly:
  ```bash
  wyoming_openwakeword --uri tcp://127.0.0.1:10400 \
    --custom-model-dir models --preload-model hey_alfred   # or hey_jarvis if untrained

  wyoming_satellite --name dev-mac --area Office \
    --uri tcp://0.0.0.0:10700 \
    --mic-command 'sox -q -d -r 16000 -c 1 -b 16 -e signed-integer -t raw -' \
    --snd-command 'play -q -r 22050 -c 1 -b 16 -e signed-integer -t raw -' \
    --wake-uri tcp://127.0.0.1:10400 --wake-word-name hey_alfred --debug
  ```
- Full Alfred stack running: `uv run python -m runner` (from `alfred/` repo root)
- A reasonably quiet room; macOS mic permission granted to the terminal on first run

## Test Steps
1. Confirm the channels process log shows `Satellite 'dev-mac' connected` shortly after the
   dev satellite starts (bridge connected out to it over TCP 10700)
2. Say the wake word ("Hey Alfred" or "Hey Jarvis", matching the loaded model) followed by
   "What's on my calendar today?"
3. Speak the full sentence naturally at normal pace, then stop
4. Watch the channels log for `Satellite 'dev-mac' heard: <transcript>` and the conscious
   process log for the request being processed
5. Listen for the spoken reply through the MacBook speakers
6. Repeat with 2 more different requests (e.g. "Turn off the kitchen lights", "Remind me to
   take the bread out of the oven") to confirm repeatability across multiple turns
7. Say the wake word and then stay completely silent for 8+ seconds — confirm the satellite
   re-arms without error

## Expected Result
- Step 1: connection log line appears within a few seconds of the satellite process starting
- Steps 3-5: logged transcript matches what was actually said (occasional minor Whisper
  errors are acceptable; gross mis-transcription is not); the spoken reply is relevant to the
  request, audible, and understandable
- Step 6: all 3 requests round-trip successfully with no crash or hang in the bridge, the
  satellite process, or the runner
- Step 7: after the no-speech timeout the satellite silently re-arms (a `no speech —
  re-arming` debug log line appears with `--debug`), no error tone, no crash

## Notes
- This is the one test that exercises the REAL `wyoming-satellite`/`wyoming-openwakeword`
  software, a REAL human voice, and the REAL Conscious Engine cloud LLM together end to end.
  The automated fake-satellite tests (`tests/core/channels/satellite/test_bridge.py`,
  `test_wiring.py`) use an in-process socket-level fake client with no real audio or wake
  word, and the existing live E2E smoke uses a fake/silent mic in an isolated stack — neither
  substitutes for a human voice through real hardware software.
- `alfred-satellite` is a planned sibling repo, not yet scaffolded as of this branch — if
  `scripts/dev-satellite-macos.sh` doesn't exist, run the two subprocess commands directly.
- If `models/hey_alfred.tflite` hasn't been trained yet, fall back to the stock `hey_jarvis`
  model per the script's built-in fallback.
- This test is the happy-path baseline only — latency, endpointing feel, and audio quality
  are separately tracked (see the other `voice-satellite-*` QA items) since they need
  independent judgment even when this test "passes."
