# alfred-satellite Repo Implementation Plan (Pi-Side Devices)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new workspace repo `alfred-satellite` that turns a Raspberry Pi Zero 2 W + ReSpeaker 2-Mic HAT into a "Hey Alfred" Wyoming satellite: provisioning script, custom wake word model + training docs, systemd units, macOS dev-satellite script, and hardware docs.

**Architecture:** The device runs only stock third-party software — `wyoming-satellite` 1.4.1+ (mic/speaker/wake orchestration, listens on TCP 10700) and `wyoming-openwakeword` 2.1+ (local wake word, TCP 10400 on loopback) — configured by our scripts. Zero custom firmware. Alfred's bridge (see `2026-07-16-voice-satellite-bridge-plan.md` in the alfred monorepo) connects out to the device. The device also works with plain Home Assistant — it is a standard Wyoming satellite.

**Tech Stack:** Bash + systemd + Python venvs on Raspberry Pi OS Lite 64-bit (Bookworm); openWakeWord training (Colab/4090); sox for the macOS dev satellite.

## Global Constraints

- New repo at workspace root: `/Users/anirudhlath/code/private/alfred/alfred-satellite/` → `github.com/anirudhlath/alfred-satellite` (same pattern as `home-service`).
- Shell scripts must pass `bash -n` and `shellcheck` (if installed) and use `set -euo pipefail`.
- Backlog convention: `docs/backlog/<priority>/*.md`; QA convention: `docs/qa-backlog/*.md` (per global conventions).
- Raspberry Pi OS Lite **64-bit** is REQUIRED (wyoming-openwakeword aarch64 wheels; 32-bit will not work).
- Known risk (verified 2026-07): wyoming-openwakeword on a Pi Zero 2 W is marginal — run exactly ONE preloaded model; the docs must cover the two fallbacks (server-side wake service; `wyoming-microwakeword`).

## Verified facts for the implementer

- `wyoming-satellite` deps are pure Python; mic/snd are arbitrary subprocesses (`--mic-command` / `--snd-command`). Pi uses `arecord`/`aplay`; macOS uses sox (`sox -q -d …` / `play -q …`).
- `wyoming-openwakeword` 2.1+ installs from PyPI on aarch64 + macOS (it uses `pyopen-wakeword`'s prebuilt wheels). Custom models: `--custom-model-dir <dir>` + `--preload-model hey_alfred` (filename stem); **only `.tflite` files are read**.
- Wake word training: the official openWakeWord Colab notebook has bit-rotted on 2026 runtimes; use the community-patched fork `alfiedennen/openwakeword-colab-2026` (Colab, ~75–90 min) or run it locally on the CachyOS 4090 via jupyter. Training emits `.onnx` + `.tflite`; ship the `.tflite`.
- ReSpeaker 2-Mic HAT drivers: the maintained fork is `HinTak/seeed-voicecard` (kernel-version sensitive — pin the branch matching the Pi kernel). ALSA device name after install: `seeed2micvoicec`.
- wyoming-satellite ships feedback sounds in its repo (`sounds/awake.wav`, `sounds/done.wav`) — reference them via `--awake-wav`/`--done-wav`.

## File Structure

```
alfred-satellite/
├── README.md                      # what it is, quickstart, workspace context
├── LICENSE                        # AGPL-3.0 (match alfred monorepo)
├── .gitignore
├── config.env.example             # per-device settings (name, area, audio devices)
├── models/
│   └── README.md                  # training instructions (+ hey_alfred.tflite when trained)
├── scripts/
│   ├── setup.sh                   # one-shot provisioning, run ON the Pi
│   ├── dev-satellite-macos.sh     # dev satellite on the MacBook (sox)
│   └── check-audio.sh             # mic/speaker sanity test on the Pi
├── systemd/
│   ├── wyoming-openwakeword.service
│   └── wyoming-satellite.service
└── docs/
    ├── parts-list.md
    ├── assembly.md
    ├── flashing.md
    └── troubleshooting.md
```

---

### Task 1: Repo scaffold

**Files:** Create `README.md`, `LICENSE`, `.gitignore`, `config.env.example`; init git; create GitHub repo.

- [ ] **Step 1: Scaffold**

```bash
mkdir -p ~/code/private/alfred/alfred-satellite/{scripts,systemd,models,docs}
cd ~/code/private/alfred/alfred-satellite
git init -b master
```

`.gitignore`:

```
config.env
*.wav
!sounds/*.wav
__pycache__/
.venv/
```

`config.env.example`:

```bash
# Copy to config.env on the device and edit.
SATELLITE_NAME="kitchen"          # unique; must match alfred's config/satellites.yaml
SATELLITE_AREA="Kitchen"          # Home Assistant area name
WAKE_WORD="hey_alfred"            # filename stem of models/*.tflite
MIC_DEVICE="plughw:CARD=seeed2micvoicec,DEV=0"
SND_DEVICE="plughw:CARD=seeed2micvoicec,DEV=0"
INSTALL_DIR="/opt/alfred-satellite"
```

`README.md`: purpose (physical "Hey Alfred" devices for Project Alfred), hardware summary (Pi Zero 2 W + ReSpeaker 2-Mic HAT + speaker, ~$45), quickstart (flash → assemble → `scripts/setup.sh` → add to alfred's `config/satellites.yaml`), pointer to the design spec in the alfred monorepo, and an explicit note that the device is a standard Wyoming satellite (works with plain Home Assistant too). Copy the LICENSE from the alfred monorepo (AGPL-3.0).

- [ ] **Step 2: Commit and publish**

```bash
git add -A && git commit -m "chore: scaffold alfred-satellite repo"
gh repo create anirudhlath/alfred-satellite --private --source=. --push
```

---

### Task 2: Wake word model + training docs

**Files:** Create `models/README.md`; produce `models/hey_alfred.tflite` (guided/manual step).

- [ ] **Step 1: Write `models/README.md`** with both training routes, fully spelled out:

````markdown
# Wake Word Models

`wyoming-openwakeword` loads any `.tflite` in this directory (`--custom-model-dir`).
The wake word name is the filename stem: `hey_alfred.tflite` → `hey_alfred`.

## Training "Hey Alfred" (openWakeWord)

The official openWakeWord training notebook has bit-rotted on 2026 runtimes.
Use the community-patched fork:

### Route A — Colab (recommended, ~75-90 min)
1. Open https://github.com/alfiedennen/openwakeword-colab-2026 and launch the
   notebook in Colab (GPU runtime).
2. Set the target phrase to `hey alfred`. Keep the default synthetic-data
   settings (Piper-generated speech + augmentation).
3. Run all cells. Download `hey_alfred.tflite` (ignore the `.onnx` — wyoming-
   openwakeword only reads `.tflite`).

### Route B — Local on the CachyOS 4090
1. `git clone https://github.com/alfiedennen/openwakeword-colab-2026`
2. `uv venv --python 3.11 && uv pip install jupyter` (training deps are
   installed by the notebook's first cells; they pin their own versions)
3. `jupyter notebook`, open the training notebook, set phrase `hey alfred`,
   run all cells.

### Verify before deploying
```bash
uv venv --python 3.13 && uv pip install pyopen-wakeword
# Feed it a 16kHz mono recording of you saying "Hey Alfred":
python -c "
from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures
import wave
oww = OpenWakeWord('models/hey_alfred.tflite')
feats = OpenWakeWordFeatures()
w = wave.open('test_hey_alfred.wav', 'rb'); pcm = w.readframes(w.getnframes())
step = 320  # 10ms of 16kHz s16 mono
hits = 0
for i in range(0, len(pcm) - step, step):
    for f in feats.process_streaming(pcm[i:i+step]):
        for prob in oww.process_streaming(f):
            hits += prob > 0.5
print('detections:', hits)
"
```
(Adjust the constructor call if the installed pyopen-wakeword version differs —
check its README.)

Commit the final `.tflite` to this repo — devices copy it during provisioning.

## Interim option

Until `hey_alfred.tflite` is trained, use a stock model name (e.g. `hey_jarvis`,
bundled with wyoming-openwakeword) as `WAKE_WORD` in `config.env`.
````

- [ ] **Step 2: Train the model** (manual/GPU step — Route A or B). Place `models/hey_alfred.tflite`, run the verification snippet against a real recording, and record the detection count in the commit message. If training is deferred, set `WAKE_WORD="hey_jarvis"` in `config.env.example` with a `TODO-until-trained` comment and add `docs/backlog/high/train-hey-alfred-model.md` (summary/context/acceptance per the backlog convention) instead — do NOT silently skip.

- [ ] **Step 3: Commit**

```bash
git add models && git commit -m "feat: hey_alfred wake word model + training docs"
```

---

### Task 3: Provisioning (`setup.sh` + systemd units + `check-audio.sh`)

**Files:** Create `scripts/setup.sh`, `scripts/check-audio.sh`, `systemd/wyoming-openwakeword.service`, `systemd/wyoming-satellite.service`.

- [ ] **Step 1: Write `systemd/wyoming-openwakeword.service`**

```ini
[Unit]
Description=Wyoming openWakeWord (local wake word)
After=network-online.target

[Service]
Type=exec
EnvironmentFile=/opt/alfred-satellite/config.env
ExecStart=/opt/alfred-satellite/oww-venv/bin/python -m wyoming_openwakeword \
  --uri tcp://127.0.0.1:10400 \
  --custom-model-dir /opt/alfred-satellite/models \
  --preload-model ${WAKE_WORD}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write `systemd/wyoming-satellite.service`**

```ini
[Unit]
Description=Wyoming Satellite (Alfred voice device)
After=network-online.target wyoming-openwakeword.service
Requires=wyoming-openwakeword.service

[Service]
Type=exec
EnvironmentFile=/opt/alfred-satellite/config.env
ExecStart=/opt/alfred-satellite/sat-venv/bin/python -m wyoming_satellite \
  --name "${SATELLITE_NAME}" \
  --area "${SATELLITE_AREA}" \
  --uri tcp://0.0.0.0:10700 \
  --mic-command "arecord -D ${MIC_DEVICE} -r 16000 -c 1 -f S16_LE -t raw" \
  --snd-command "aplay -D ${SND_DEVICE} -r 22050 -c 1 -f S16_LE -t raw" \
  --wake-uri tcp://127.0.0.1:10400 \
  --wake-word-name "${WAKE_WORD}" \
  --awake-wav /opt/alfred-satellite/wyoming-satellite/sounds/awake.wav \
  --done-wav /opt/alfred-satellite/wyoming-satellite/sounds/done.wav
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Write `scripts/setup.sh`** (run ON the Pi as the default user, via sudo where needed):

```bash
#!/usr/bin/env bash
# One-shot provisioning for an Alfred voice satellite on Raspberry Pi OS Lite 64-bit.
# Usage: copy this repo to the Pi (or git clone), edit config.env, then: sudo scripts/setup.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${REPO_DIR}/config.env"
INSTALL_DIR="${INSTALL_DIR:-/opt/alfred-satellite}"

if [[ $(uname -m) != "aarch64" ]]; then
  echo "ERROR: 64-bit Raspberry Pi OS required (wyoming-openwakeword wheels are aarch64)."
  exit 1
fi

echo "==> System packages"
apt-get update
apt-get install -y --no-install-recommends git python3-venv python3-pip alsa-utils

echo "==> Install dir ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
cp "${REPO_DIR}/config.env" "${INSTALL_DIR}/config.env"
mkdir -p "${INSTALL_DIR}/models"
cp "${REPO_DIR}"/models/*.tflite "${INSTALL_DIR}/models/" 2>/dev/null || \
  echo "WARN: no .tflite models found — stock model names still work"

echo "==> wyoming-satellite"
if [[ ! -d "${INSTALL_DIR}/wyoming-satellite" ]]; then
  git clone https://github.com/rhasspy/wyoming-satellite.git "${INSTALL_DIR}/wyoming-satellite"
fi
python3 -m venv "${INSTALL_DIR}/sat-venv"
"${INSTALL_DIR}/sat-venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/sat-venv/bin/pip" install "${INSTALL_DIR}/wyoming-satellite"

echo "==> wyoming-openwakeword"
python3 -m venv "${INSTALL_DIR}/oww-venv"
"${INSTALL_DIR}/oww-venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/oww-venv/bin/pip" install wyoming-openwakeword

echo "==> systemd units"
cp "${REPO_DIR}"/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wyoming-openwakeword.service wyoming-satellite.service

echo "==> Done. Check: systemctl status wyoming-satellite"
echo "    Then add this device to alfred's config/satellites.yaml:"
echo "      - name: ${SATELLITE_NAME}"
echo "        host: $(hostname -I | awk '{print $1}')"
echo "        area: ${SATELLITE_AREA}"
```

NOTE for the implementer: the ReSpeaker 2-Mic HAT kernel driver (HinTak/seeed-voicecard) must be installed BEFORE running setup.sh; it is deliberately not automated here because the branch must match the running kernel. `docs/assembly.md` (Task 5) carries those exact steps. `check-audio.sh` verifies the result.

- [ ] **Step 4: Write `scripts/check-audio.sh`**

```bash
#!/usr/bin/env bash
# Mic/speaker sanity check for the ReSpeaker 2-Mic HAT.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config.env"

echo "==> Cards:"
arecord -l
echo "==> Recording 3s from ${MIC_DEVICE}..."
arecord -D "${MIC_DEVICE}" -r 16000 -c 1 -f S16_LE -d 3 /tmp/mic-test.wav
echo "==> Playing back on ${SND_DEVICE}..."
aplay -D "${SND_DEVICE}" /tmp/mic-test.wav
echo "OK — you should have heard your recording."
```

- [ ] **Step 5: Verify + commit**

```bash
bash -n scripts/setup.sh scripts/check-audio.sh
command -v shellcheck >/dev/null && shellcheck scripts/*.sh || true
git add scripts systemd && git commit -m "feat: Pi provisioning script + systemd units"
```

Real-hardware validation goes to QA backlog (Task 6).

---

### Task 4: macOS dev satellite script

**Files:** Create `scripts/dev-satellite-macos.sh`.

This is the no-hardware dev loop used by the bridge plan's Task 17 live smoke test.

- [ ] **Step 1: Write `scripts/dev-satellite-macos.sh`**

```bash
#!/usr/bin/env bash
# Run a dev Wyoming satellite on macOS using the MacBook mic/speakers.
# Prereqs: brew install sox ; uv (astral). First run creates venvs in .venv-dev/.
# Usage: scripts/dev-satellite-macos.sh [wake_word]   (default: hey_alfred,
#        falls back to hey_jarvis if models/hey_alfred.tflite is missing)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WAKE_WORD="${1:-hey_alfred}"
if [[ "${WAKE_WORD}" == "hey_alfred" && ! -f "${REPO_DIR}/models/hey_alfred.tflite" ]]; then
  echo "WARN: models/hey_alfred.tflite not found — using stock 'hey_jarvis'"
  WAKE_WORD="hey_jarvis"
fi

DEV_DIR="${REPO_DIR}/.venv-dev"
mkdir -p "${DEV_DIR}"

if [[ ! -d "${DEV_DIR}/oww" ]]; then
  uv venv --python 3.13 "${DEV_DIR}/oww"
  uv pip install --python "${DEV_DIR}/oww/bin/python" wyoming-openwakeword
fi
if [[ ! -d "${DEV_DIR}/sat" ]]; then
  uv venv --python 3.13 "${DEV_DIR}/sat"
  if [[ ! -d "${DEV_DIR}/wyoming-satellite" ]]; then
    git clone https://github.com/rhasspy/wyoming-satellite.git "${DEV_DIR}/wyoming-satellite"
  fi
  uv pip install --python "${DEV_DIR}/sat/bin/python" "${DEV_DIR}/wyoming-satellite"
fi

"${DEV_DIR}/oww/bin/python" -m wyoming_openwakeword \
  --uri tcp://127.0.0.1:10400 \
  --custom-model-dir "${REPO_DIR}/models" \
  --preload-model "${WAKE_WORD}" &
OWW_PID=$!
trap 'kill ${OWW_PID}' EXIT

exec "${DEV_DIR}/sat/bin/python" -m wyoming_satellite \
  --name "dev-mac" \
  --area "Office" \
  --uri tcp://0.0.0.0:10700 \
  --mic-command 'sox -q -d -r 16000 -c 1 -b 16 -e signed-integer -t raw -' \
  --snd-command 'play -q -r 22050 -c 1 -b 16 -e signed-integer -t raw -' \
  --wake-uri tcp://127.0.0.1:10400 \
  --wake-word-name "${WAKE_WORD}" \
  --awake-wav "${DEV_DIR}/wyoming-satellite/sounds/awake.wav" \
  --done-wav "${DEV_DIR}/wyoming-satellite/sounds/done.wav" \
  --debug
```

- [ ] **Step 2: Verify it end-to-end on the MacBook**

Run: `brew list sox || brew install sox`, then `scripts/dev-satellite-macos.sh`.
Expected: both services start; saying the wake word logs a detection and (with the alfred runner up and `config/satellites.yaml` pointing at `127.0.0.1`) produces a spoken reply. macOS will prompt for mic permission on first run.

- [ ] **Step 3: Commit**

```bash
git add scripts/dev-satellite-macos.sh && git commit -m "feat: macOS dev satellite (sox mic/speaker)"
```

---

### Task 5: Hardware docs

**Files:** Create `docs/parts-list.md`, `docs/flashing.md`, `docs/assembly.md`, `docs/troubleshooting.md`.

- [ ] **Step 1: Write the four docs** with this content outline (complete each — no stubs):
  - `parts-list.md`: Pi Zero 2 W (~$15); ReSpeaker 2-Mic Pi HAT (~$12); speaker — either a 2-3 W 4 Ω speaker on the HAT's JST connector or a small powered USB/3.5 mm speaker (~$10-15); 32 GB microSD; 5 V/2.5 A PSU; optional enclosure/3D print. Per-device total ~$45-55.
  - `flashing.md`: Raspberry Pi Imager → **Raspberry Pi OS Lite 64-bit** (Bookworm) — 64-bit is mandatory; pre-configure hostname (`<name>-sat`), SSH key, Wi-Fi in the imager; first boot + `ssh <user>@<name>-sat.local`.
  - `assembly.md`: seat the HAT on the GPIO header; speaker to JST or aux; ReSpeaker driver install — `git clone https://github.com/HinTak/seeed-voicecard && cd seeed-voicecard && git checkout <branch matching kernel: uname -r>` → `sudo ./install.sh && sudo reboot`; verify with `arecord -l` showing `seeed2micvoicec`; then run `scripts/check-audio.sh`; then `scripts/setup.sh`.
  - `troubleshooting.md`: wake latency/CPU pegging (known wyoming-openwakeword-on-Zero-2W issue — mitigations in order: ensure exactly one `--preload-model`, try `wyoming-microwakeword` instead, or move wake detection server-side by running wyoming-openwakeword on the CachyOS box and pointing `--wake-uri` at it over LAN — note the privacy tradeoff: mic audio then streams continuously); no audio device (driver/kernel mismatch — re-checkout matching seeed-voicecard branch); satellite not connecting (check `systemctl status`, port 10700 reachable from the server, name matches alfred's `satellites.yaml`); mic too quiet (`--mic-volume-multiplier 2` in the systemd unit, or `alsamixer` capture gain).
- [ ] **Step 2: Commit + push**

```bash
git add docs && git commit -m "docs: parts list, flashing, assembly, troubleshooting"
git push -u origin master
```

---

### Task 6: QA backlog for hardware validation

- [ ] In the **alfred-satellite** repo, create `docs/qa-backlog/` entries per the global QA template (one file each): `satellite-first-boot-provisioning.md` (flash→setup.sh→services green), `satellite-wake-word-accuracy.md` (detection across the room, false-positive hour), `satellite-e2e-voice-loop.md` (wake→command→spoken reply latency < ~4 s against the live runner), `satellite-announcement-playback.md` (URGENT notification audible), `satellite-reboot-resilience.md` (power-cycle → services recover, bridge reconnects). Commit and push.

---

## Self-Review Notes (already applied)

- Training route documents the community-patched notebook because the official one fails on 2026 Colab runtimes (verified 2026-07); Route B keeps it off-cloud per local-first preference.
- `setup.sh` refuses 32-bit OS early — the single most common provisioning trap for wyoming-openwakeword.
- ReSpeaker driver install stays manual-but-documented (kernel-branch matching cannot be safely automated).
- Everything hardware-dependent lands in QA backlog files rather than claiming verification in-plan.
