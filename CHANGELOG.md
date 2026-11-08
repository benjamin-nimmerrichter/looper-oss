# Changelog

All notable changes to Looper OSS. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The single
source of truth is the [`VERSION`](VERSION) file; `scripts/release.py` reads it.

## [0.2.0] — 2026-11-08

The RC-505mkII round (issue #3) plus the tooling that keeps the docs honest.

- **D1** per-track varispeed 0.25–4× (`speed`, grid-locked at 1)
- **D2** Mark Back, replace-overdub, Bounce In
- **D3** four VST insert slots, four recallable banks, an eight-step sequencer
  on the low pass, toggle or momentary per slot
- **D4** per-track input compressor, a mixer strip per input channel (gain +
  HP/LP on a shared bus), three stereo output buses with per-track routing that
  fold down to the device, cue send, a level per bus
- **D5** a synthesised drum engine: 16 kits, 200 patterns, no samples
- **D6** MIDI note gestures (press / hold / double), learned by performing them
- **D7** 99 memories, one-file backup/restore, WAV import
- **D8** audited rather than rebuilt — per-track playback settings, stems and
  the master mixdown already existed
- `scripts/shots.py` regenerates the documentation screenshots through WebKit's
  own snapshot: no browser, no interaction, starts and stops the stack itself

### Added

- **The application.** `app/main.py` + `app/window.py`: one process that owns the
  UI server, the OSC bridge, the SuperCollider engine and the window. Second
  launch raises the running window; closing it stops everything and releases the
  audio device; SIGTERM follows the same cleanup path, so no orphaned `scsynth`.
- **Linux AppImage** and **Windows portable + installer** (`packaging/`), with
  `looper_oss.py` as the single entry point for both.
- `install.sh` for source check-outs: prompts for the right package command when
  sclang is missing instead of failing with an import error.
- `docs/img/` — real screenshots of the main screen, the fx drawer and all seven
  setup tabs.
- **CLEAR ALL** in the transport bar: tap once to arm (`sure?`), tap again to
  wipe every track — layers, undo stacks and playback — and drop the autosave
  with it, so a reset cannot reappear on the next start. `act clearAll`.
- `--deps` and `--install-deps`. The AppImage carries all of Python, but
  SuperCollider, `sc3-plugins`, GTK 3/WebKit2GTK and PyGObject remain the
  operating system's. Instead of making anyone collect package names, the app
  reports exactly what is missing and installs the lot for your distribution
  (pacman / apt / dnf / zypper) behind a single polkit prompt. `install.sh` uses
  the same code.
- **Position in bars and beats.** The transport shows `bar·beat` (`3·2`), eased
  along the same position as the blue playhead so the two cannot disagree, and
  only touching the DOM when the beat changes. Derived from `phase`, `bars` and
  `beatsPerBar` — no protocol change.
- **Every input channel is a record source.** `~inputChannels` builds the list
  from `audio.in` at SynthDef compile time: `0` is the stereo sum, `1..n` are the
  mono channels, `n+1` the internal test bus. A 2-in interface offers
  `stereo / L / R`, so a mic and a guitar are two tracks from one box; an 8-in
  interface offers eight. The track header shows `IN L` / `IN R` / `IN ST`, so the
  choice is visible without opening the FX drawer.
- `scripts/shots.py` — regenerates every documentation screenshot without
  Playwright, by driving the cached Chromium over the DevTools protocol with the
  `websockets` client the bridge already has.

### Next

- **Windows support is the 0.3.0 milestone.** 0.2.0 ships the PyInstaller/Inno
  Setup recipe and the `driver = asio` path, but no Windows host has run them, so
  a Windows build is an experiment rather than a supported release. 0.3.0 is
  WASAPI/ASIO device handling, the engine's Windows paths, a verified installer,
  and the same test evidence the Linux build has.

### Changed — docs

- README gained an **About** section, first person and short: why it exists (a
  hardware looper costs too much just to find out whether you like looping), how
  it was made (vibecoded, with the audio side measured rather than guessed), what
  it is not, and the Beardyman/Beardytron reference.

- README now states plainly what was only implicit before: the track count is
  **parametric (1–16)** rather than a fixed layout, audio in/out are likewise
  1–16 channels, and the UI is built for touch (48 px floor, pointer events,
  long-press guards, `--kiosk` for a bare panel), on any screen from 1024×600
  to 4K.
- README documents **every setting** in plain language — what it does, when to
  touch it, and a sane starting value — once per setup tab.

### Fixed

- **Settings numbers no longer scatter across four columns.** The readout sat at
  `labelWidth + gap` because `.ctl` grew from wherever the label ended and its
  content was left-aligned; rows carrying a hint were shifted again by the
  hint's width. The control now hugs the right edge of a growing cell and the
  hint lives under the label, so every readout in a card shares one x position
  (verified across all seven tabs at 1280×800, 1024×600, 800×1280, 1920×1080).
- The `input monitor` readout said `off (loop only)`, which is wider than the
  readout column and pushed that one row out of line; the explanation moved to
  the hint and the value is `off`.
- The RC-505 tab reused the numeric readout class for whole sentences; it is now
  a two-column reference table.

### Fixed — first take now carries into overdub

- **A take no longer stops recording at the bar.** The RC-505 behaviour was
  already in the state machine (`/ldwrap`: commit the first layer, then
  `~startRec` the next one), but the shipped `ui/config.json` said
  `"autoDub": 0`, which sent the track to playback at the loop boundary instead
  of continuing to record. The engine's own fallback was `(? 1)` -- on when
  unspecified -- so the config was contradicting both the engine and
  `docs/CONTROLS.md`, which documents the default as on. Default is now 1: tap to
  start a take, it closes at the bar and keeps recording as an overdub until you
  tap again. The end-to-end suite follows the same behaviour: `press` starts a
  take, the bar closes it into an overdub (state stays *recording*), and `press`
  again commits it to playback; the old `state == 2` assertion was checking the
  bug. 36/36.

### Fixed — an update no longer resets the app

- **Installing a new build wiped every setting.** `stage()` copies the payload
  into `~/.local/share/looper-oss/app/<version>/` and preserved `sessions/` but
  not `ui/config.json` — so bpm, track count, audio device, MIDI map and every
  loop setting went back to the shipped defaults on each update. The config is
  now carried across, together with `sessions/`. Because keeping a *wrong*
  shipped default forever is the other half of that promise, old configs are
  migrated by version: a config older than 3 gets `loop.autoDub = 1` (0.1.0
  shipped it off, which stopped a take at the bar) and is bumped to 3. Your own
  values are untouched. Covered by `update keeps settings and music`.

### Fixed — audio after sleep (issue #1)

- **Coming back from suspend left the app silent.** `scsynth` survives a sleep
  but the audio device does not: PipeWire tears the node down and rebuilds it,
  and the running server keeps its dead connection, so every meter moves and no
  sound comes out. The process never exits, so the exit-code watchdog never
  fired. The launcher now watches the one signal a resume gives for free —
  `CLOCK_MONOTONIC` stops during suspend while `CLOCK_REALTIME` keeps running,
  so the difference between the two clocks *is* the sleep duration. On more than
  5 s it restarts the engine (after giving PipeWire 4 s to rebuild the graph).
  `logs/app.log` records `resume detected (…s asleep)`. No D-Bus client, no new
  dependency; Windows' QPC behaves the same way. The session is restored from
  autosave **stopped**, so nothing blasts when the lid opens.

### Fixed — AppImage window

- **The packaged app opened no window at all.** `AppRun` pointed `PYTHONPATH` at
the bundled libraries only, so the bundled CPython could not import the host's
PyGObject: `import gi` failed, `_native()` returned False, and the launcher fell
through to a browser without saying so. The AppImage now prefers the host
`python3` (which has gi) and keeps the bundled interpreter as a fallback, adding
any host site-packages that actually contain gi. `open_window()` also logs the
path it chose, so a silent fallback can never happen again.

### Changed — settings readouts

- **The numbers in setup were small, and sat in the top corner of the −/+ group.**
  The readout is a plain `div` between two 48 px buttons: with no rule of its
  own it stretched to the full row height and its text stayed on the top edge,
  right-aligned. It is now its own centred cell on the same plate as the
  buttons, at 1.35 rem and 600 weight instead of 0.85 rem — the value reads as
  the thing you are changing, not a stray label.

### Fixed — text that turned to garbage without a GPU

- Two full-screen layers were enough to make **every label render as the wrong
  glyphs** when Chromium rasterises in software (remote sessions, VMs,
  `--disable-gpu`): the invisible `.boot` overlay (`opacity: 0`, `inset: 0`) and
  the drawers (`position: fixed` + `backdrop-filter: blur(6px)` + a slide
  transform). The overlay is now `visibility: hidden` when done, and the drawer
  is opaque with no blur — 0.97 alpha bought a barely-visible effect and cost a
  composited layer over the whole app. Legible beats blurry.

### Fixed — a source check-out never opened its window

- A virtualenv is created with `include-system-site-packages = false`, so
  `python3 looper_oss.py` could not import the distro's PyGObject and silently
  fell back to a browser tab even with `python3-gi` installed. `_native()` now
  puts the distribution's site-packages back on `sys.path`, `install.sh` creates
  the venv with `--system-site-packages`, and `--deps` agrees with the window
  instead of reporting a false miss.

### Removed

- `run.sh` and `run.ps1`. There is no shell launcher to remember: the app is the
  entry point, and `install.sh` replaces `--setup`.

## [0.1.0] — 2025-11-06

First release. A working touchscreen loop station: record, overdub, undo, save.

### Added — audio engine (`engine/looper.scd`)

- Per-track looper: record → auto-close at the bar → overdub on top → undo/redo
  from a layer stack (up to `maxLayers`, default 4).
- One global `Phasor` clock; every track derives its own phase, so layers cannot
  drift and a track can run at half or a quarter of the master loop length.
- `contentLen` frozen per track at first record, which makes a tempo change a
  free varispeed re-pitch instead of a re-record.
- Configurable seam crossfade (0.1–50 ms, default 2 ms) and `~lerpRead`, which
  reads modulo `contentLen` so a fractional position near the loop end cannot
  interpolate against a zero past the buffer.
- Per-track input trim, smoothed noise gate, HP/LP filter (`BLowPass`/`BHiPass`),
  tempo-synced delay (`CombN`, 0.05–8 s), shared `FreeVerb2` reverb bus with
  per-track send, reverse playback, fade in/out, overdub decay ("dub mode").
- Master bus: `Compander` limiter + `clip2(0.98)`.
- Metering through 64-frame buffers written with `BufWr.kr` and polled by sclang
  at 10 Hz — deliberately *not* `SendReply` inside the big SynthDefs.
- Boot watchdog: retries the server twice, then reports
  `could not open the audio device` to the UI instead of throwing on every
  command; handlers are no-ops until `~tracks` exists.
- Clean shutdown: `s.quit` → wait → exit 42, so `run.sh` can restart the engine
  after an audio-device change without the new `scsynth` failing to open audio.

### Added — sessions

- Save/load a slot to `sessions/slotN/`: one WAV per layer, `session.json` with
  the whole UI state, `vst.txt` + `vst_*.bin` for plugin state.
- Autosave whenever the layer structure changes and nothing is recording; a cold
  start recovers it, restored **stopped** so opening the app never plays.
- Export: 30 s of the master bus to `sessions/exports/`.

### Added — bridge (`bridge/bridge.py`)

- OSC ⇄ WebSocket JSON, the only place that knows the positional `/ldstate`
  layout (`GLOBAL_FIELDS` 23 + `TRACK_FIELDS` 24).
- Settings write-back to `ui/config.json` plus regeneration of
  `engine/_config.scd`; audio-device enumeration; `/b_getn` fan-in for meters.
- Generic per-track setter: `/ld/set "t2.hp" 300` → `synth.set(\hp, 300)`, so a
  new knob needs no new OSC path.

### Added — UI (`ui/`)

- Touch UI with no build step and no framework: track pads, FX drawer, setup
  drawer, tap tempo, count-in, keypad, boot screen, bar playhead.
- Every dimension in `rem` off `calc(clamp(9px, min(1.62vh, 1.05vw), 26px) * --scale)`,
  so 1024×600 → 4K works in portrait or landscape; scale slider 0.75–1.75.
- 48 px (`--tap`) floor on interactive targets with an audit overlay (`d`) that
  outlines and counts offenders.
- Long-press (0.8 s) guard on clear; websocket reconnect without a reload.
- Keyboard: `1`–`8`, `u`, `s`, `space`, `esc`, `d`.

### Added — packaging

- **A desktop application, not a script.** `app/main.py` owns the static UI
  server, the bridge thread, the engine process and the window; `app/window.py`
  opens it with GTK+WebKit2, falls back to an Edge/Chromium `--app` window, then
  to the default browser. Closing the window stops the engine and releases the
  audio device; a second launch raises the running window instead of fighting
  over ports.
- **Linux: AppImage** (`packaging/linux/build-appimage.sh`) carrying its own
  CPython, stdlib and the two pinned packages — 12 MB, no install step.
- **Windows: portable build + installer** (`packaging/windows/`), PyInstaller for
  the executable and Inno Setup for a per-user install with no admin prompt.
- `requirements.txt` with exact pins (`websockets==17.4`, `python-osc==1.10.2`);
  `install.sh` installs from it, checks Python ≥ 3.9 and sclang, and refuses to
  fake an engine.
- `scripts/release.py` builds `dist/looper-oss-<version>.tar.gz` and `.zip` plus
  `SHA256SUMS`, excluding `sessions/`, `logs/`, `.venv/` and caches.
- `docs/LICENSING.md` (third-party audit) and `ACKNOWLEDGEMENTS.md`.
- Tests: `scripts/smoke.py` (drives the real protocol), `scripts/uicheck.mjs`
  (Playwright: layout fit, 48 px floor, engine round-trip), `scripts/wavcheck.py`
  (offline seam/step analysis of exported takes), `scripts/sweep.py`.

### Known limitations

- Loop length comes from **bars + BPM**, never from the first take (no AUTO mode).
- 4 layers per track instead of the RC-505's single loop + Mark Back.
- No time-stretch: a tempo change re-pitches.
- Windows launcher ships but WASAPI boot is **unverified**.
- VST inserts need the `VSTPlugin` extension; without it the slots are absent.

[0.2.0]: https://github.com/benjamin-nimmerrichter/looper-oss/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/benjamin-nimmerrichter/looper-oss/releases/tag/v0.1.0
