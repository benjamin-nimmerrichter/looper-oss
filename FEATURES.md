# FEATURES.md — Looper OSS feature inventory

What exists, what `goal.md` builds next, what waits until the basics are done.
Nothing in **Backlog** starts before Definition-of-Done items 1–7 in `goal.md` pass.

Legend: ✅ shipped · ⬜ backlog · ❌ out of scope (the `goal.md` phases are done)

Reference points: **RC-505 mkII** (5 stereo tracks, Input FX + Track FX, 99 phrase
memories, SPEED CHANGE, Mark Back, rhythm guide), **SooperLooper** (threshold record,
multiply, reverse, rate, undo/redo), **LoopMix** (5 ms seam crossfade),
**looper-pedal.com** (per-layer volume/mute/delete).

## 1. Looping core

| feature | RC-505 | SooperLooper | Looper OSS |
| --- | --- | --- | --- |
| N tracks, independent volume/pan | 5 | ∞ | ✅ 1–16 (config) |
| layers / overdub | ✅ | ✅ | ✅ up to 4 per track |
| undo / redo | Mark Back | ✅ | ✅ stack, depth = maxLayers |
| quantized record to bar grid | ✅ | ✅ | ✅ |
| free-length loop (AUTO) | ✅ | ✅ | ⬜ |
| per-track loop length | ✅ (sync off) | ✅ | ✅ half / quarter |
| reverse playback | ✅ | ✅ | ✅ |
| multiply (loop grows ×2) | ✅ | ✅ | ⬜ (P-later) |
| overdub decay ("dub mode") | ✅ | ✅ (feedback) | ✅ |
| fade in / fade out per track | ✅ | — | ✅ |
| seam fade (click-free wrap) | ✅ | ✅ | ✅ configurable 0.1–50 ms |
| varispeed on tempo change | ✅ SPEED CHANGE | ✅ rate | ✅ free (contentLen) |
| per-layer volume / mute / delete | — | — | ⬜ (P-later, UI only) |
| track copy / swap / duplicate | ✅ | — | ⬜ |
| "once" / one-shot playback | ✅ | ✅ | ⬜ |
| loop length display + playhead | ✅ | ✅ | ✅ bar playhead |

## 2. Input

| feature | status |
| --- | --- |
| stereo / CH1 / CH2 / dual-mono input | ✅ per track |
| input gain | ✅ |
| noise gate (threshold, smoothed) | ✅ |
| high pass / low pass | ✅ |
| input compressor | ⬜ |
| input FX block separate from track FX (RC-505 split) | ✅ |
| threshold auto-record (start on first note) | ✅ (arm per track) |
| record level per track + soft clip | ✅ + HOT indicator |
| latency / record offset compensation | ✅ report + ±8192 smp |
| input metering (peak + layer peak) | ✅ |

## 3. Effects

| feature | status |
| --- | --- |
| tempo-synced delay (CombN) | ✅ with honest decay 0.05–8 s |
| shared reverb bus with per-track send | ✅ FreeVerb2 |
| reverb size/damp control | ✅ (size knob) |
| master limiter | ✅ Compander + clip2(0.98) |
| master FX insert (filter) | ✅ HP/LP |
| VST3/VST2 insert chain per track | ✅ 3 slots, bypass, editor |
| LV2 plugins (Linux, via Carla) | ⬜ |
| per-track EQ / comp as stock UGens | ⬜ |
| effects presets / bypass all | ⬜ |

## 4. Clock & sync

| feature | status |
| --- | --- |
| master BPM, tap tempo | ✅ |
| bars per loop, beats per bar | ✅ |
| metronome with accent + level | ✅ own bus, own level |
| count-in (independent of metronome) | ✅ beat-accurate |
| grid follows tempo | ✅ varispeed |
| rhythm guide / drum patterns | ✅ 16 synthesised kits, 200 patterns (generated, no samples) |
| external MIDI clock in/out | ⬜ |
| Ableton Link (SC Link quark) | ⬜ |
| JACK transport sync | ⬜ |

## 5. Memory & files

| feature | status |
| --- | --- |
| save/load a phrase (WAV + JSON + VST state) | ✅ |
| phrase slots / browser | ⬜ (start with one slot) |
| autosave + crash recovery | ✅ |
| export mixdown to WAV | ✅ |
| session capture (record everything you play) | ⬜ |
| import a WAV as a layer | ⬜ |

## 6. Control

| feature | status |
| --- | --- |
| touch UI + mouse | ✅ |
| keyboard shortcuts | ✅ |
| OSC control from another machine | ✅ documented |
| MIDI footswitch / CC learn | ✅ + default layout |
| MIDI device selection in settings | ✅ |
| OSC phone remote (same UI, narrow layout) | ⬜ |
| gamepad / hid pedal | ⬜ |

## 7. UI, platform, ops

| feature | status |
| --- | --- |
| touch targets ≥ 48 px | ✅ audit overlay (`d`) |
| global scale factor 0.75–1.75 | ✅ persisted |
| settings drawer (audio, loop, clock, UI, MIDI, session) | ✅ |
| portraits / 1024×600 verification | ✅ screenshot harness |
| long-press guard on destructive actions | ✅ |
| websocket reconnect without reload | ✅ |
| logging + graceful failure messages | ✅ rotated logs, sweep |
| Windows build | ⛔ **recipe only** (PyInstaller + Inno Setup) — never built, installed or run on Windows; full support is 0.3.0 |
| kiosk autostart (systemd user unit / .desktop) | ⬜ |
| theming / colour-blind safe palette | ⬜ |
| localisation (CZ/EN) | ⬜ |
| help overlay / cheatsheet | ⬜ |

## Backlog (after the basics)

Ordered by value-for-effort, all still library-first:

1. **VST slot polish** — drag to reorder, plugin params as knobs in the UI, plugin
   preset save/restore folded into the phrase file.
2. **Per-layer mixer** — the looper-pedal.com idea on top of the layer model we already
   have: each layer its own volume/mute/delete. Pure UI + 3 args per layer, no new audio.
3. **Loop multiply** — repeat the first pass underneath while recording (SooperLooper
   semantics); needs a second write head and a length multiplier per track.
4. **Free-length / AUTO loop** — first pass defines the length; needs the phasor to be
   per-track instead of global. Big change to the grid, do it consciously.
5. **Ableton Link / MIDI clock** — tempo in from outside; `Phasor` rate follows.
6. **Session capture + export** — record the master bus for the whole session and slice
   by marker.
7. **Phrase slots** — 8–99 slots with a browser, WAV per layer.
8. **Waveform view** — `Buffer.getToFloatArray` → peak-decimated SVG in the track column.
9. **Kiosk deployment** — systemd user unit, `--kiosk` autostart, boot-to-pedal.

## Out of scope (deliberately)

- Timeline / arrangement view, MIDI sequencing, plugin delay compensation — this is a looper, not a DAW.
- Time-stretching. Varispeed only, like the hardware.
- Any cloud, account, or telemetry feature. Fully offline.
- Reimplementing effects that a VST can provide.
