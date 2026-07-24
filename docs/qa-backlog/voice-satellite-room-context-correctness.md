# Voice Satellite: Room-Aware Context Correctness ("the lights" → Correct Area)

**Feature:** Room-aware context injection (`config/satellites.yaml` `area` → `UserRequest.area` → `ContextAssembler.assemble(area=...)`, per `docs/voice-satellites.md` § Room-Aware Context)
**Priority:** high
**Type:** functional

## Prerequisites
- Full stack running: Redis Stack + Mosquitto (`brew install redis-stack mosquitto && brew services start redis-stack mosquitto`), real LLM key, `uv run python -m runner`
- `home-service` running against the dev Home Assistant instance
  (`../home-assistant/config`), which currently defines areas "Living Room", "Kitchen", and
  "Bedroom" (`config/.storage/core.area_registry`) but does NOT pre-assign the template light
  entities to an area — as a one-time setup, assign `light.living_room` to the "Living Room"
  area and `light.bedroom` to the "Bedroom" area via Home Assistant's UI (Settings → Areas &
  Zones), or create/assign equivalent light entities per area if those aren't suitable
- Two satellite entries configured in `config/satellites.yaml`, each pointed at a distinct
  dev-mac satellite instance (run two `wyoming_satellite`/`wyoming_openwakeword` pairs on
  different ports, e.g. 10700/10400 and 10701/10401) — or, if only one physical/dev satellite
  is available, test sequentially by editing `config/satellites.yaml`'s `area:` field and
  restarting the runner between the two area configurations:
  ```yaml
  satellites:
    - name: living-room-sat
      host: 127.0.0.1
      port: 10700
      area: Living Room
    - name: bedroom-sat
      host: 127.0.0.1
      port: 10701
      area: Bedroom
  ```

## Test Steps
1. From the satellite configured with `area: Living Room`, say the wake word followed by
   "Turn off the lights"
2. Check the conscious process log/prompt for the injected `## Location` section: "This
   request was spoken at the Living Room satellite. When a device is referenced without
   naming a room ('the lights'), assume the Living Room area."
3. Confirm the actual Home Assistant action taken (via `home-service` logs, or by observing
   the entity's state) targets `light.living_room` (or whichever entity is assigned to the
   Living Room area) and not the bedroom light
4. Repeat from the `area: Bedroom` satellite: say "Turn off the lights" and confirm the
   bedroom entity is targeted instead, leaving the living room light state unaffected by this
   request
5. From either satellite, say a request that explicitly names a different room ("Turn on the
   bedroom light" while standing at the Living Room satellite) and confirm the explicit room
   name in speech overrides the satellite's ambient area assumption
6. Say a request that has nothing to do with home control (e.g. "What's the weather like?")
   from an area-configured satellite and confirm the `## Location` context doesn't cause
   unrelated behavior (no home-domain tool call at all)

## Expected Result
- Step 2: the exact `## Location` prompt text appears with the correct area name for the
  speaking satellite
- Step 3-4: "the lights" (no room named) resolves to the entity in the SPEAKING satellite's
  assigned area, not a different room's light and not all lights fleet-wide
- Step 5: an explicitly named room in speech takes precedence over the ambient satellite area
  — the LLM should not blindly force everything into the satellite's own area when the user
  clearly said otherwise
- Step 6: non-home requests are unaffected by the room context (no spurious tool calls)

## Notes
- This is the one test that verifies the room-context feature does what it's for — nothing
  in `config/satellites.yaml` or `bus/schemas/events.py` hardcodes any area-to-entity
  mapping (per the "no hardcoding" design principle in `docs/voice-satellites.md`); the
  entire behavior depends on the REAL Conscious Engine LLM correctly using the injected
  `## Location` text together with the real HomeAgent/`home-service`/Home Assistant area
  registry. The automated test (`tests/core/conscious/test_context_area.py`) only asserts the
  prompt STRING contains "## Location" and the area name — it never verifies the LLM actually
  acts on it or that `home-service` correctly filters by area.
- If the dev Home Assistant instance's entities aren't cleanly split across areas, this test
  is only as good as the area assignments made in the Prerequisites step — double check them
  in the HA UI before concluding a failure is a bug in Alfred rather than a test-setup gap
- Two dev-mac satellites can share the same MacBook mic/speaker in a pinch (they'll compete
  for the input device) — sequential single-satellite testing (swap `area:` + restart) is
  more reliable if only one dev machine is available
