# Voice Satellite: Offline/Reconnect Behavior When Bridge Host Sleeps/Wakes

**Feature:** `SatelliteConnection` reconnect-forever loop (`core/channels/satellite/bridge.py`)
**Priority:** medium
**Type:** integration

## Prerequisites
- Full stack running on the MacBook (the bridge host): Redis Stack + Mosquitto
  (`brew install redis-stack mosquitto && brew services start redis-stack mosquitto`),
  `home-service`, real LLM key, `uv run python -m runner`
- `config/satellites.yaml` with the dev-mac satellite entry pointed at `127.0.0.1`, dev
  satellite process running
- Ability to observe channels process logs live (e.g. run the runner in a visible terminal or
  tail its output) across a macOS sleep/wake cycle

## Test Steps
1. Confirm the satellite is connected (`Satellite 'dev-mac' connected` in the logs)
2. Put the MacBook to sleep (close the lid or Apple menu → Sleep) for at least 60-90 seconds
   — long enough to exceed the bridge's 30s read timeout (`_READ_TIMEOUT_S`) and its 10s ping
   interval (`_PING_INTERVAL_S`) several times over
3. Wake the MacBook and reopen the terminal with the logs
4. Observe the reconnect behavior in the logs: does the bridge detect the dead connection,
   log a "connection lost" warning, and retry?
5. Confirm the satellite eventually reconnects (`Satellite 'dev-mac' connected` reappears)
   without needing to manually restart the runner or the satellite process
6. Once reconnected, say the wake word and confirm a full voice round trip still works
   (transcript + spoken reply)
7. Separately, kill the dev satellite process (Ctrl-C) while the bridge is running, wait ~5s,
   restart it, and confirm the bridge reconnects the same way
8. While the satellite is intentionally disconnected (step 7, before restart), fire an URGENT
   notification (see the Python snippet in
   `voice-satellite-urgent-announcement-audio-quality.md`) and confirm the channels log shows
   it was silently skipped (not delivered, no crash) — `SatelliteBridge.play_wav_all()` only
   targets satellites currently in `connections()`

## Expected Result
- Step 4: within roughly one read-timeout window (up to ~30s) after resuming from sleep, the
  bridge notices the stale connection and starts its reconnect-forever backoff loop (starts
  at 1s, doubles, caps at `reconnect_max_s`=60s) — check whether the backoff reset logic
  (fast retry after a previously-established session) behaves reasonably rather than backing
  off for a full minute unnecessarily on a brief sleep
- Step 5: reconnection succeeds automatically with no manual intervention
- Step 6: the voice loop works normally after reconnect — no leftover broken state from the
  sleep/wake cycle
- Step 7: same reconnect-forever behavior on a plain process kill/restart, independent of
  sleep/wake specifically
- Step 8: the missed announcement is logged/skipped cleanly, no exception surfaces to crash
  the notification dispatcher or the channels process

## Notes
- This exercises real OS-level network suspension (macOS sleep closes/stalls the TCP socket
  in ways that differ from a clean process kill) which cannot be simulated by the automated
  fake-satellite tests (`test_bridge.py` uses `asyncio` cancellation and mocked delays, not a
  real suspended NIC)
- The backoff-reset-on-short-session behavior has a known refinement backlogged in
  `docs/backlog/low/satellite-reconnect-min-uptime.md` (a misbehaving satellite that
  handshakes then immediately drops can retry at a constant ~1s forever) — if the sleep/wake
  cycle happens to reproduce a rapid reconnect/drop loop, note it, but this is already a
  known, accepted v1 limitation, not a new bug to report
- Record actual wall-clock time from wake to reconnect for reference
