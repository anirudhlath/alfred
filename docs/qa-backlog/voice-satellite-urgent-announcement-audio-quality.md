# Voice Satellite: URGENT Announcement Audibility and TTS Quality

**Feature:** `SatelliteChannelAdapter` — spoken URGENT notifications (`core/notifications/adapters/satellite.py`)
**Priority:** high
**Type:** functional

## Prerequisites
- Same live stack as `voice-satellite-real-mic-full-loop.md`: dev-mac satellite connected
  (real sox speaker output) + full runner (`uv run python -m runner`)
- Ability to run a one-off Python snippet against the live Redis instance to publish a real
  `Notification` directly onto `alfred:notifications:dispatch`
  (`shared.streams.NOTIFICATION_DISPATCH_STREAM`), the same stream both `conscious-delivery`
  and `channels-delivery` consumer groups read from:
  ```bash
  uv run python -c "
  import asyncio
  import redis.asyncio as redis
  from core.notifications.schema import Notification, Urgency

  async def main():
      r = redis.from_url('redis://localhost:6379')
      n = Notification(
          title='Smoke Alarm',
          body='Smoke detected in the Kitchen',
          urgency=Urgency.URGENT,
          source='qa-manual-test',
      )
      await r.xadd('alfred:notifications:dispatch', {'notification': n.model_dump_json()})
      await r.close()

  asyncio.run(main())
  "
  ```

## Test Steps
1. With the dev-mac satellite connected and idle (not mid-utterance), run the snippet above
2. Listen to the satellite speak `"{title}: {body}"` via Piper TTS
3. Judge the audio by ear: volume level, clarity/intelligibility, naturalness of the Piper
   voice, and whether it's audible from a normal listening distance (not right next to the
   speaker)
4. Repeat with a longer, multi-sentence `body` to check whether long announcements stay
   intelligible start to finish (no clipping, no audio glitches/pops between chunks)
5. Say the wake word and START speaking a request, then — while that utterance/reply is still
   in flight — fire another URGENT notification from a second terminal; observe whether the
   two audio streams interleave/garble on the wire (this exercises `_audio_lock` in
   `SatelliteConnection.play_wav`)
6. If a second satellite is available (or configure two entries in `config/satellites.yaml`
   with a second dev-mac instance on a different port), fire one URGENT notification and
   confirm it plays audibly on both connected satellites (`play_wav_all` broadcast)

## Expected Result
- Step 2-3: the announcement is clearly audible, intelligible, and sounds like a natural
  (if synthetic) voice — no garbling, no dropped audio, no unexpectedly quiet/loud levels
- Step 4: longer announcements play through cleanly with no audible seams between the
  `AudioChunk`×N stream segments
- Step 5: the two `play_wav` calls are serialized (per the `_audio_lock` design) — you should
  hear them play sequentially, not interleaved/garbled, even though there's no cancellation
  of the first reply (a known v1 limitation noted in `bridge.py`)
- Step 6: both satellites audibly announce

## Notes
- This is a pure audio-quality-by-ear test — nothing automated judges whether Piper TTS
  sounds acceptable through a real speaker, or whether the announcement is loud/clear enough
  to actually notice from another room
- `SPEAKER_ID_THRESHOLD`/voice enrollment is unrelated here — announcements aren't
  speaker-gated
- The interleaving scenario in step 5 is a documented "known v1 limitation" (see the comment
  block above `send_transcript` in `core/channels/satellite/bridge.py`) — the goal here is to
  confirm the *documented* mitigation (serialization, not cancellation) actually holds in
  practice, not to find a new bug
