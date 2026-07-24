# Kokoro HF Hub First-Run Auto-Download and Offline Cache Reuse

**Feature:** `core/voice/hf_models.py::ensure_model` (shared HF Hub downloader) as used by
`KokoroTTS`
**Priority:** high
**Type:** functional

## Prerequisites
- Ability to clear/relocate the local HF cache (`~/.cache/huggingface/hub`) — **back it up
  first** if it holds other models you don't want to re-download
- Network access for the initial download; ability to disable network (Wi-Fi off / airplane
  mode) for the offline-reuse step
- `uv run python -m runner` (or just the web channel) ready to start

## Test Steps
1. Confirm a cold cache: `ls ~/.cache/huggingface/hub | grep kokoro` returns nothing (rename
   `~/.cache/huggingface/hub/models--fastrtc--kokoro-onnx` out of the way if present).
2. Start the runner and trigger the first Kokoro synthesis (open the web chat and send a
   message).
3. Watch server logs / network activity during that first synthesis. Expect a download of
   `kokoro-v1.0.onnx` (~325 MB) and `voices-v1.0.bin` (~28 MB) — ~353 MB total — from
   `fastrtc/kokoro-onnx` at the pinned revision `8d07950c9b6c87ce6809e9bba7bd494336217c2a`.
4. Confirm the first synthesis eventually succeeds (expect it to take noticeably longer —
   download time plus 10–40s model construction) and produces valid, playable audio.
5. Confirm the cache is now populated: `ls ~/.cache/huggingface/hub/models--fastrtc--kokoro-onnx/`.
6. Restart the runner (warm cache) and trigger a second synthesis — confirm it's fast, with no
   re-download.
7. With a warm cache, disable network entirely (Wi-Fi off / airplane mode), restart the runner,
   and trigger synthesis again — confirm `hf_hub_download` resolves fully from the local cache
   without erroring on the (absent) network call.

## Expected Result
- First-run download totals ~353 MB and completes without error.
- First synthesis succeeds once download and model construction finish.
- Subsequent runs are fast (cache-hit, no re-download).
- Fully offline runs against a warm cache still succeed.

## Notes
- `hf_hub_download` is pinned to an exact revision here — this also verifies the pin resolves
  correctly, not just "whatever is latest on the Hub."
- `PiperTTS` uses the same `ensure_model` downloader for its fallback voice — if time allows,
  spot-check its first-run download too, though it's lower priority since Piper isn't the
  default backend.
- Automated tests mock `hf_hub_download`/`ensure_model` entirely (see
  `tests/core/voice/test_hf_models.py`), so a real end-to-end download + cache-hit + offline
  round trip is unverified by CI.
