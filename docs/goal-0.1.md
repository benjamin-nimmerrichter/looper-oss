# goal.md — Looper OSS: production-ready touchscreen loop station

## Context

Looper OSS is a thin skin over SuperCollider: `engine/looper.scd` wires stock UGens,
`bridge/bridge.py` translates OSC ⇄ WebSocket JSON, `ui/` is a browser page.
No DSP is hand-written; that constraint holds for everything below.

**Working today** (verified end-to-end on the target machine, 2026-01):

- 5 tracks × 4 layers, tempo-locked loop grid, one `Phasor` shared on an audio bus
- record → auto-close on loop boundary → overdub as new layers → undo/redo (gain toggle, instant)
- per-track: input select, gain, noise gate, HP/LP, tempo delay, reverb send, pan, volume, mute
- shared FreeVerb2 bus, master Compander, metres for track/input/master
- metronome + tap tempo, count-in (bar-aligned via `/ldwrap`)
- OSC in :9000, state out :9001, ws :8182, static UI :8181
- state machine verified: `record → auto-close → overdub → undo → redo → stop → resume`, tempo locked while layers exist

### Known audio defects (found in review, not yet fixed)

| # | defect | where |
| --- | --- | --- |
| A1 | No loop-seam crossfade → click at every wrap where content ≠ 0 at both ends | `\ld_track` read path |
| A2 | `vol`, `pan`, `g0..g3`, `playing`, `recOn` are set instantly → zipper noise, clicks on undo/stop | `\ld_track` |
| A3 | Recording writes at unity with no soft clip → one hot pass destroys a layer | `BufWr(sig * recOn, …)` |
| A4 | No input-driver latency compensation → recorded material sits a few ms late against the grid | write offset = `pos` |
| A5 | Metronome bypasses master volume/limiter (`Out.ar(0, click)`) and has no level control | `\ld_transport` |
| A6 | `\ld_master` reads `In.ar(revBus, 2)` twice (wasted bus reads) | `\ld_master` |
| A7 | Delay "feedback" knob drives `CombN` `decaytime` through an inverse mapping — mislabelled and non-monotonic at the ends | `delayFb` |
| A8 | Auto-close uses `Main.elapsedTime` (>0.8 × loop) instead of a sample counter → misfires if tempo changed mid-pass | `\ldwrap` |
| A9 | `s.options.numWireBufs` unset (SC default 64) — track synth is near that; add headroom | top of `looper.scd` |
| A10 | UI sends `synth.set` on every `pointermove` (~100 Hz) with no throttle | `ui/controls.js` |

## Constraints

- Library-first. Audio = stock SC/sc3-plugins UGens only. If a feature needs DSP,
  find the UGen (`RecordBuf`, `LoopBuf`, `CombN`, `FreeVerb2`, `Compander`, `PitchShift`) or drop it from scope.
- One config file (`ui/config.json`) stays the single source of truth; the launcher
  generates `engine/_config.scd` from it. Settings must write back to that file.
- No UI build step. No runtime network access. Must run on CachyOS **and** Windows.
- Touch-first: every interactive target ≥ 48 × 48 CSS px after scaling.
- Do not lower the PipeWire quantum yet (user is running YouTube through an HDMI→optical
  splitter to a soundbar). Latency work is *measurement and offset only*; quantum is a documented setting.

## Phase 0 — Audio hardening (do first, everything else rides on it)

**0.1 Loop-seam crossfade** — in `\ld_track`, shape the layer sum with a seam window:
read `pos` and `pos + loopLen` (one extra `BufRd` per layer, or one shared window UGen)
and crossfade over `xfade` samples (default 64 ≈ 1.3 ms, setting 0–256).
Write side: apply the inverse window to the overdub write near the seam so a layer
recorded across the seam does not double.
*Accept:* a sustained sine recorded on track 1 loops for 30 s with no audible tick;
loop a DC-offset-free but non-zero-ended buffer and confirm no discontinuity.

**0.2 Smoothed parameters** — `LagUD.kr` (~10 ms) on `vol`, `pan`, `muted`, `g0..g3`,
`playing`; `T2A` + `LagUD` (~5 ms) on `recOn` and `monitor`.
*Accept:* dragging the volume fader and pressing undo/stop produce no clicks;
verify by eye on the master meter (no step transients) and by ear.

**0.3 Record-side gain staging** — per-track `recGain` (0–1.5, default 1.0),
`tanh`-soft clip before `BufWr`, and a per-layer peak meter so the user sees a hot layer.
*Accept:* recording with input gain at max does not produce a destroyed, full-scale layer;
layer peak is reported in the state message.

**0.4 Latency measurement + record offset** — read `Server.latency` + device latency,
expose `recordOffset` (samples, signed, default 0, range ±4096) subtracted from the
write index; show the computed round-trip latency in the settings panel.
*Accept:* a click played exactly on the downbeat lands on the downbeat of the loop
within ±2 ms when the offset is calibrated (measure via the input meter vs. `/ldpos`).

**0.5 Master chain cleanup** — single `In.ar(revBus, 2)` read; metronome through a
dedicated `clickBus` summed into the master *before* the limiter, with a `clickVol`
setting (default 0.3) and `metroOnly` preview; Compander attack 0.005 → 0.001,
`clip2(0.98)` after it.
*Accept:* master volume at 0 silences everything including the click; metronome is not
distorted at high click level.

**0.6 Delay: honest controls** — keep `CombN` (feedback comb) but expose `delayDecay`
(0.05–8 s, log) and label the knob "decay"; `delayBeats` stays tempo-synced.
*Accept:* knob is monotonic across its whole range, 1/8-beat slapback to 8 s wash.

**0.7 Engine headroom + CPU readout** — `s.options.numWireBufs = 256`,
`s.options.memSize` sized for 16 tracks × 4 layers, report `s.avgCPU` and peak in state.
*Accept:* 8 tracks × 4 layers at 4 bars/120 bpm stays under 25 % CPU on the target box;
no "too many wires"/"alloc failed" errors in the log.

**0.8 Auto-close by sample counter** — replace the `Main.elapsedTime` heuristic with
a counter incremented per `/ldwrap`, invalidated when tempo changes.
*Accept:* first layer closes exactly one loop after record start even if the user
dragged the BPM fader (which is refused anyway) or pressed clear mid-pass.

**0.9 UI throttle** — coalesce `pointermove` parameter sends to one per animation frame.
*Accept:* OSC traffic during a 3 s fader drag ≤ ~180 `set` messages.

## Phase 1 — Features a looper is expected to have

Reference: RC-505 mkII (5 stereo tracks, independent faders, Input FX + Track FX,
manual loop length, SPEED CHANGE, Mark Back undo, 99 phrase memories),
SooperLooper (threshold record, multiply, reverse, undo/redo, rate), LoopMix
(5 ms loop crossfade), Deluge (pre/post-roll margins for crossfade).

**1.1 Reverse per track** — `LoopBuf`/negative-rate read (or read `pos` reflected);
toggle per track, click-free by crossfading the reflection.
**1.2 Overdub decay** — `dubDecay` per track: existing layers attenuate by x dB per pass
(layer gain multiplied once per `/ldwrap`). Gives the classic "dub" fade.
**1.3 Threshold auto-record** — arm a track; recording starts on the first sample above
`autoThresh` (via `Amplitude` + `SendTrig`), then quantizes the *loop start* to the grid.
**1.4 Proper count-in** — independent of the metronome toggle: count-in always clicks
`countInBars` bars, then starts recording exactly on the boundary.
**1.5 Mark Back undo (depth > 1)** — per track, keep a stack of layer gain snapshots
(`maxLayers` already bounds it); undo/redo walks the stack. UI shows depth.
**1.6 Fade in / fade out** per track (0–5 s) applied to the layer sum — for pads and swells.
**1.7 Per-track loop length** — `loopLen` per track as a multiple of the bar grid
(0.5, 1, 2 bars…) with LOOP SYNC on/off; transport keeps one master phase.
**1.8 Speed / varispeed** — allow BPM change with content: on change, re-lay each layer by
resampling into a scratch buffer over the loop duration (accepting pitch shift, like
RC-505 SPEED CHANGE), or retain "grid lock" as a setting.
**1.9 Input FX vs Track FX split in the UI** — mirror RC-505: input section (gate, HP/LP,
comp) applies before the write head; track section (delay, reverb send, fade) after the read head.
**1.10 Master FX** — one insert on the master bus (reverb already there; add filter/limit bypass).

## Phase 2 — Settings menu (audio devices and everything else)

New `#settings` drawer, same visual language, reachable from the transport bar.

**2.1 Audio panel** — driver (JACK / ALSA on Linux; device name on Windows), device list
fetched by the bridge (`pw-cli ls Node` / `aplay -l` / `snd_pcm` enumeration), sample rate,
`blockSize`, `hardwareBufferSize`, in/out channel counts, **Restart engine** action,
measured latency, xruns, CPU.
**2.2 Loop panel** — tracks (1–16), maxLayers (1–4), default bars, beatsPerBar,
crossfade ms, record offset, auto-close on/off, dub decay.
**2.3 Clock panel** — metronome level, count-in bars, accent pitch, tap-tempo window.
**2.4 UI panel** — **scale factor 0.75–1.75** (see Phase 3), track colour assignment,
fullscreen/kiosk toggle, show/hide the master footer.
**2.5 Persistence** — the bridge writes changes back to `ui/config.json`; engine restart
re-reads the generated `_config.scd`. Schema-validate before writing; keep a `.bak`.
*Accept:* change sample rate or track count in the settings drawer, confirm it survives a
full `./run.sh` restart, and that a malformed config cannot brick startup (falls back to defaults).

## Phase 3 — Touch sizing + global scale factor

**3.1 Enforce a 48 px floor** — `--tap: max(48px, 2.9rem)` applied to pads, buttons,
segmented controls, knob hit areas, fader tracks; verify with a dev overlay that outlines
every interactive rect and flags anything under the floor.
**3.2 Scale factor** — `--scale` (0.75–1.75) multiplying the `html` font-size clamp;
persisted in config, adjustable from the settings drawer and by pinch/Ctrl+wheel.
**3.3 Layout audit at the real sizes** — 1024×600, 1280×800 (the 8" panel), 1920×1080,
2560×1440, and portrait 800×1280; no scrolling, no clipped labels, ≥ 44 px between
adjacent destructive targets (clear vs. stop).
**3.4 Long-press affordances** — visual progress on every hold action, and a settings
toggle for hold duration (0.4 / 0.8 / 1.2 s).
*Accept:* screenshot check at 1280×800 with scale 1.0 and 1.4; the overlay reports zero
sub-48 px targets; all five track columns fully visible without scrolling.

## Phase 4 — Session memory

**4.1 Save/load a phrase** — per-track layers written as WAV via `Buffer.write`
(server-side, no sclang round-trip) + a JSON of the parameter tree; slot browser in the UI.
**4.2 Autosave / crash recovery** — write the session on every structural change, restore on boot.
**4.3 Export** — bounce the whole mix (with tail) to WAV via a recording synth on the master bus.

## Phase 5 — MIDI / footswitch

**5.1 Device selection** in settings; `MIDIIn` in `looper.scd`.
**5.2 Learn mode** — press a control in the UI, then a CC/note; store `{type, num, target}` in config.
**5.3 Default map** — 5 track buttons, undo, clear, tap, stop-all; sustain-pedal-safe.

## Phase 6 — VST slots (the last missing piece from the original ask)

**6.1** Install `sc3-vstplugin` (AUR) / Windows prebuilt; `VSTPlugin.search`.
**6.2** Per-track insert chain: `VSTPlugin.ar(sig, 2, id: slotId)` between filter and delay;
slot list (`pluginName`, `bypass`, ordered) travels in the state message.
**6.3** UI: the existing "plugin slots" card becomes real — browse, load, bypass, remove,
open the plugin editor as a client-side window sized to the track column.
**6.4** Persist plugin state (`VSTPluginController.save` blobs) into the phrase file.

## Phase 7 — Production readiness

**7.1** Structured logging: engine + bridge write to `logs/`, rotated, with a `--verbose` flag.
**7.2** Graceful failure: no scsynth, no websocket, no OSC → UI shows a specific reason
and a retry; `run.sh` distinguishes "server not installed" from "server died".
**7.3** Windows launcher (`run.ps1`) + README section; verify the engine boots on WASAPI.
**7.4** `scripts/smoke.py` — the e2e checks already used during development
(record → auto-close → overdub → undo → redo → stop → resume, tempo lock, clear),
committed and runnable in CI-less form: `./scripts/smoke.py`.
**7.5** Docs: `README.md` (install/run/controls), `docs/PROTOCOL.md` (message schemas
with the exact field order the bridge assumes), `docs/AUDIO.md` (signal flow diagram,
gain staging, latency budget).
**7.6** Dependency notes: `sc3-plugins` optional-but-recommended; single `--setup` path
that checks every dependency and prints exactly what is missing.

## Definition of done

1. `./run.sh --browser` cold-starts on CachyOS with no console errors, engine ready in < 5 s.
2. `./scripts/smoke.py` passes all state-machine assertions.
3. Loop of any material on any track runs 5+ minutes with no click at the seam (0.1).
4. Fader drag, undo, stop, resume: no transients (0.2).
5. 8 tracks × 4 layers at 4 bars/120 bpm < 25 % CPU, no server warnings (0.7).
6. Settings survive a restart; track count change and audio device change both work (2.5).
7. Zero interactive targets under 48 px at scale 1.0 on 1280×800; scale 0.75–1.75 works (3).
8. A phrase saves, reloads, and exports to WAV that opens in an audio editor (4).
9. One MIDI footswitch can trigger record/undo/clear on a track (5).
10. One VST3 plugin loads on a track insert, its editor opens, and its state survives reload (6).

## Risks / unknowns

- **`LoopBuf` vs `BufRd`** for varispeed + reverse: `LoopBuf` fixes `numChannels` at
  compile time and its `startLoop/endLoop` are per-instance, so it may be the cleaner
  path for 1.1/1.8 — spike it before committing.
- **In-place resampling** during a tempo change corrupts the buffer if read and write
  overlap: needs one scratch buffer per track and a sequential per-layer pass.
- **PipeWire quantum 1024** (~21 ms) is the dominant latency term; the user is deliberately
  not changing it yet. Record-offset compensation (0.4) is what makes it tolerable meanwhile.
- **Browser audio permissions are irrelevant** (audio lives in SC), but a browser restart
  drops the websocket — the UI must reconnect silently (already retries, keep it).
- **VSTPlugin on Windows** requires the prebuilt release zip placed in the Extensions
  folder; document the exact path or Phase 6 stalls on setup.
- **`numWireBufs`/`memSize`** increases can fail silently at boot on low-RAM machines;
  report allocation failures in the log rather than dying.

## Beyond this plan

The full feature inventory — shipped, planned here, and deferred — lives in
`FEATURES.md`. This plan covers the **basics that make the pedal usable**; nothing
in the backlog is started before Definition-of-done items 1–7 pass.

## Non-goals

- Not a DAW: no timeline, no MIDI sequencing, no plugin delay compensation.
- No time-stretch (varispeed only, like the hardware).
- No touchscreen drawing/waveform editing in this pass.
- No cloud/sync/account features. Fully offline.
