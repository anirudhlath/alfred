# Voice Satellite: Endpointing Feel (No Premature Cutoffs, No Long Hangs)

**Feature:** `UtteranceCollector` streaming VAD endpointing (`core/channels/satellite/endpointing.py`)
**Priority:** high
**Type:** functional

## Prerequisites
- Same live stack as `voice-satellite-real-mic-full-loop.md`: dev-mac satellite connected
  (real sox mic) + full runner + real LLM key
- A room with some ambient background noise available for step 5 (not dead silent — e.g. a
  fan, TV at low volume, or normal household noise), in addition to a quiet baseline

## Test Steps
1. Say the wake word, then speak a sentence with a natural mid-sentence pause shorter than a
   second (e.g. "Remind me... to take the bread... out of the oven in fifteen minutes")
2. Say the wake word, speak a short command, then stop talking abruptly — note how long it
   takes for the satellite to visibly/audibly re-arm (default `silence_ms=800`)
3. Say the wake word and speak a long, rambling sentence for 12-15+ seconds to probe the
   `max_utterance_ms` (15000ms) hard cutoff
4. Say the wake word, start speaking, pause for 3-5s mid-utterance (as if thinking), then
   continue speaking — check whether the utterance is cut off before you resume
5. Speak a full command at normal volume from a few meters away (not close to the mic), once
   in a quiet room and once with background noise present, to probe whether the VAD
   `threshold`/`end_threshold` defaults (0.5/0.35) hold up outside close-mic conditions

## Expected Result
- Step 1: the mid-sentence pause does NOT trigger early cutoff — full sentence is transcribed
- Step 2: re-arm happens promptly (roughly ~1-1.5s after you stop), not a "long hang"
- Step 3: utterance is forcibly ended around 15s — verify it isn't jarring or cut off
  mid-word in a confusing way, and that no crash/error occurs
- Step 4: the utterance DOES end during the 3-5s thinking pause (by design, `silence_ms=800`)
  — confirm this matches expectations and that resuming speech afterward correctly requires
  a fresh wake word rather than silently being ignored
- Step 5 (quiet): normal room-distance speech is reliably detected as speech (not missed) and
  silence reliably detected as silence (mic doesn't hang open)
- Step 5 (background noise): background noise doesn't falsely trigger speech_start or keep
  the collector "in speech" indefinitely after you actually stop talking

## Notes
- This is a subjective "does it feel natural" test. The automated
  `tests/core/channels/satellite/test_endpointing.py` only verifies the state machine against
  synthetic VAD probability sequences — it never exercises a real Silero VAD model against
  real room acoustics
- Pay special attention to two failure modes: cutting off the last word of a sentence (false
  endpointing) and hanging open due to background noise (false continuation)
- If either problem is observed, file a `docs/backlog/` ticket naming the specific
  `threshold`/`end_threshold`/`silence_ms` value that needs tuning rather than changing
  defaults ad hoc during QA
