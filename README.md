# Looper OSS

A touchscreen loop station in the spirit of the Boss RC-505, built as a thin
skin over **SuperCollider**. Layers per track, tempo-locked loops, per-track
gate / filter / delay / reverb send, MIDI footswitch, VST3 inserts, and session
recording with autosave. Runs fully offline on Linux and Windows.

Nothing in this repo implements audio DSP. Audio is stock SuperCollider UGens;
the UI is a browser page; the only Python translates OSC to JSON.

```text
ui/            browser UI (HTML/CSS/JS, no build step, scales to any screen)
bridge/        bridge.py — OSC <-> WebSocket JSON, + config writer
engine/        looper.scd — UGen wiring and the per-track state machine
               vst.scd   — VSTPlugin slots, loaded only if the extension exists
scripts/       smoke.py (e2e test), uicheck.mjs (UI test), sweep.py, release.py
docs/          PROTOCOL.md (message contract), AUDIO.md (graph + gain staging)
ui/config.json the only config file
goal.md        what was built, in phases, with acceptance criteria
FEATURES.md    feature inventory: shipped / planned / backlog
VERSION        the release number, read by scripts/release.py
```

## Requirements

| what | why | where it comes from |
| --- | --- | --- |
| **SuperCollider 3.13+** (`sclang`, `scsynth`; tested on 3.14.1) | the entire audio engine | your OS package manager |
| **sc3-plugins** | the per-track filter (`BLowPass` / `BHiPass`) | your OS package manager |
| **Python 3.9+** | the bridge, and the tests | your OS package manager |
| an audio server | PipeWire on Linux, WASAPI on Windows | already on your desktop |
| *optional* **VSTPlugin** | VST3 / VST2 insert slots | see [Install](#install) |
| *optional* **chromium / firefox** | `--browser` and `--kiosk` app window | already installed |

That is all of it. No build step, no JavaScript toolchain, no database, no
network access at runtime. The two Python packages the bridge needs are pinned
exactly in [`requirements.txt`](requirements.txt) and installed into a local
`.venv`; nothing is installed system-wide and nothing needs `sudo`.

## Get it

**Release archive** — no git required. Take `looper-oss-<version>.tar.gz` (or the
`.zip`, which keeps the Unix modes for Windows users) from the
[releases page](https://github.com/benjamin-nimmerrichter/looper-oss/releases),
check it against the published `SHA256SUMS`, unpack it, and continue at
[Install](#install).

**From source:**

```bash
git clone git@github.com:benjamin-nimmerrichter/looper-oss.git
cd looper-oss
```

**To build the archive yourself** — a USB stick, a pedal image, an offline
machine — run `./scripts/release.py`. It writes
`dist/looper-oss-<version>.tar.gz`, a `.zip` with executables still executable,
and `SHA256SUMS`; it refuses to ship `sessions/`, `logs/`, `.venv/` or the
generated `engine/_config.scd`, and it rebuilds byte-identically.

## Install

Linux (CachyOS, Arch):

```bash
sudo pacman -S supercollider sc3-plugins pipewire-jack
./run.sh --setup          # creates .venv, installs requirements.txt, renders config
```

Debian/Ubuntu: `sudo apt install supercollider sc3-plugins pipewire-jack`.
Fedora: `sudo dnf install supercollider sc3-plugins pipewire-jack`.

`pipewire-jack` puts scsynth on the PipeWire graph like any other client. If you
keep `jack` (jack2) instead, set `"audio": { "device": "default" }` in
`ui/config.json` so scsynth uses ALSA through `pipewire-alsa`.

Windows: install [SuperCollider](https://supercollider.github.io/) and Python 3,
then `.\run.ps1 -Setup`.

Optional, VST3 inserts: the `VSTPlugin` extension (AUR `sc3-vstplugin`, or the
prebuilt release zip from `github.com/Spacechild1/vstplugin` unpacked into
`~/.local/share/SuperCollider/Extensions/`). The engine detects it at startup and
shows `VST yes` in the log; without it the slots are simply absent.

## Run

```bash
./run.sh                 # then open http://127.0.0.1:8181/
./run.sh --browser       # opens a chromium/chrome app window
./run.sh --kiosk         # fullscreen, for the 8" panel
```

`Ctrl-C` stops everything (engine, bridge, UI server, scsynth). Restarting is
always safe: `run.sh` sweeps leftovers from a crashed run first.

Windows: `.\run.ps1`, `.\run.ps1 -Browser`, `.\run.ps1 -Kiosk`.

## Playing it

| gesture | effect |
| --- | --- |
| tap a track pad (empty) | record layer 1 |
| tap while recording | close the layer, it loops |
| tap while playing | record another layer on top |
| **undo / redo** | drop or restore the last layer, instantly |
| **hold clear** (0.8 s) | wipe the track — long press so it cannot happen by accident |
| **stop** | silence the track, keep its layers |
| **fx** | per-track drawer: input, gate, filter, loop, delay, reverb, plugin slots |
| **setup** | audio device, loop, clock, UI scale, MIDI learn, sessions |
| tap the BPM bar / **tap** button | set tempo, or tap tempo |
| **count-in** | next recording waits for the bar |

Keyboard on the laptop: `1`–`8` press a track, `u` undo, `s` stop, `space` stop
all, `esc` close a drawer, `d` toggle the 48 px target overlay.

## How loops stay in sync

One `Phasor` writes loop phase to an audio bus; every track derives its own
phase from it, so layers can never drift and a track can run at half or a
quarter of the master loop (`loopRatio`). Overdub is a `BufWr` into a free layer
buffer while the others keep playing, and undo is just that layer's gain going
to zero — which is why it is instant and needs no buffer arithmetic.

A short fade at the loop seam kills the wrap click. `contentLen` (frozen per
track when it first records) versus the live grid length is what makes a tempo
change a free varispeed instead of a re-record. Details and gain staging in
`docs/AUDIO.md`.

## Sessions

- **save slot** (footer or setup → session) writes `sessions/slotN/`: one WAV per
  layer, `session.json` with the whole UI state, `vst.txt` + `vst_*.bin` for
  plugin state.
- **load** restores layers, plugin chain and plugin state.
- **autosave** writes `sessions/autosave/` whenever the layer structure changes
  and nothing is recording; a cold start recovers it automatically. A crash
  costs at most one layer.
- **export** bounces 30 s of the master bus to `sessions/exports/`.

## Scaling

`ui/config.json` → `tracks` (1–16) and `maxLayers` (1–4). The track grid is
`grid-auto-columns: 1fr`, so the columns divide whatever width there is. Every
dimension is in `rem`, and

```css
html { font-size: calc(clamp(9px, min(1.62vh, 1.05vw), 26px) * var(--scale)); }
```

so the whole pedal rescales from 1024×600 to 4K, portrait or landscape, and the
**scale** slider (setup → UI, 0.75–1.75) sets `--scale` for the room you are in.
Every interactive target is at least 48 px in both axes (`--tap`); press `d` to
have the UI outline each target and count any that fall below that floor.

## Tests

```bash
./scripts/smoke.py            # starts its own engine: 35 end-to-end checks
./scripts/smoke.py --attach    # run against a stack you started with ./run.sh
PLAYWRIGHT_DIR=/tmp/uitest/node_modules ./scripts/uicheck.mjs
./scripts/release.py --check   # is the tree shippable? builds nothing
```

`smoke.py` needs the venv's packages only; it finds `.venv` itself, so you never
have to activate anything first. It also **wipes `sessions/autosave`,
`sessions/slot7` and `sessions/exports`** before it starts — copy them elsewhere
if one of them is yours.

`./scripts/wavcheck.py` inspects the WAVs a session slot wrote — offline, silent —
and reports whether each take ends cleanly at the loop point (`STEP into the final
frame`).

`smoke.py` drives the real protocol: state shape, record → auto-close →
overdub → undo → redo → stop → resume, varispeed, reverse, loop length, dub
decay, crossfade, record offset, stats, device enumeration, session save/load,
autosave recovery and export.

`uicheck.mjs` needs playwright (`mkdir -p /tmp/uitest && cd /tmp/uitest &&
npm i playwright && npx playwright install chromium`). It checks layout fit at
1280×800 / 1024×600 / 1920×1080 / 800×1280, the 48 px floor in the main view and
both drawers, that tapping a pad changes engine state, that the scale slider
works and persists, and writes screenshots to `/tmp/looper-oss-ui/`.

## Troubleshooting

| symptom | cause |
| --- | --- |
| `missing: sclang` | SuperCollider not installed (see above) |
| `the UI server did not start on port 8181` | something else holds the port; `python3 scripts/sweep.py 8181` |
| UI says *engine offline* | check `logs/bridge.log` and `logs/engine.log` |
| silence but meters move | output routed to the wrong sink — patch `SuperCollider:out_1/2` in `qpwgraph` |
| click at the loop point | raise `loop.crossfadeMs` (setup → loop) |
| loops land late | calibrate `loop.recordOffset` (setup → loop) |
| high latency | PipeWire quantum; `pw-metadata -n settings 0 clock.force-quantum 256` |

Logs are in `logs/`, rotated at 5 MB.

## Credits

Looper OSS is a thin skin over other people's work, and says so in
[`ACKNOWLEDGEMENTS.md`](ACKNOWLEDGEMENTS.md): SuperCollider, sc3-plugins,
VSTPlugin, python-osc, websockets, PipeWire, and the RC-505 and SooperLooper
designs this imitates on purpose.

## Licence

**GPL-3.0-or-later** — see [`LICENSE`](LICENSE). This repository ships no
third-party code; [`docs/LICENSING.md`](docs/LICENSING.md) has the full
attribution table, what is imported versus merely launched, and what
redistributing a pedal built on this actually obliges you to do.

## Docs

- `AGENTS.md` — where everything lives, the commands, and the invariants that bite
- `docs/CONTROLS.md` — **every button, slider and knob**, with range, effect and the message it sends
- `docs/PROTOCOL.md` — every OSC/JSON message, with field order
- `docs/AUDIO.md` — signal graph, gain staging, latency, CPU numbers
- `goal.md` — the plan this was built against, phase by phase
- `FEATURES.md` — feature inventory and backlog
