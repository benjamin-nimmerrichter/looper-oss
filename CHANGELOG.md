# Changelog

All notable changes to Looper OSS. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The single
source of truth is the [`VERSION`](VERSION) file; `scripts/release.py` reads it.

## [Unreleased]

Nothing yet.

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

- `requirements.txt` with exact pins (`websockets==17.4`, `python-osc==1.10.2`);
  `run.sh --setup` / `run.ps1 -Setup` install from it and check Python ≥ 3.9.
- `run.sh` / `run.ps1`: config render → leftovers sweep → UI server → bridge →
  engine, with log rotation at 5 MB and a restart loop.
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

[Unreleased]: https://github.com/USER/looper-oss/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/USER/looper-oss/releases/tag/v0.1.0
