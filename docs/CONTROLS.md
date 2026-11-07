# Looper OSS — every control

Authoritative index of every button, slider, knob and toggle in the UI: what it
is, its range, what it does, the message it sends, and the engine parameter it
lands on. If a control is not in this file, it does not exist.

Wire messages are described in `docs/PROTOCOL.md`; `set` means
`{"op":"set","path":…,"v":…}` → `/ld/set`, `act` means `/ld/act`, `svc` means
`/ld/svc`, `cfg` means `{"op":"cfg"}` → writes `ui/config.json` (and
`engine/_config.scd`) then live-applies when the engine supports it.

---

## 1. Transport bar (top)

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| **STOP ALL** | button | — | Stops every track (commits anything recording, `playing` 0, layers kept) **and parks the clock**: the playhead and the metronome stop. Any take resumes the grid. | `act stopAll` | `~stopTrack` × N + `gridOn = 0` |
| **CLEAR ALL** | button (tap twice) | — | Empties **every** track: all layers, the undo stacks and playback, and it drops the autosave so the wipe cannot come back on the next start. Tap once to arm (`sure?`), tap again within 2.5 s to confirm. | `act clearAll` | `~clearTrack` × N + `gridOn = 0` |
| **SETUP** | button | — | Opens the setup drawer (seven tabs, see §5). | — | — |
| **tempo value** | button (numeric keypad) | 40–240, default 120 | **Tap the number to type a tempo**: keypad with digits, `clear` and `set`. Sets and persists the BPM. | `set g.bpm` + `cfg bpm` | `\ld_transport` rate, track `beatLen`/`loopSec` |
| tempo fader | fader | 40–240, default 120 | Sets the tempo. Existing layers **re-pitch** (varispeed), they are not re-recorded. | `set g.bpm` | transport `rate`, every track `beatLen`/`loopSec` |
| **TAP** | button | — | Tap tempo: tap 2–8 times at the tempo you feel; averages taps inside a 2.2 s window and sets the BPM. It measures your actual spacing, so tapping 3× at 0.40 s gives 150. | `svc tap` | `~tapTempo` → `~setGlobal("bpm")` |
| **BARS / LOOP** | segmented | 1 · 2 · 4 · 8, default 4 | Loop length in bars. Defines the grid every track records against. Changing it re-times existing material (varispeed). | `set g.bars` | transport `rate`/`bars`, track `loopSec` |
| **POSITION** | read-out | `bar·beat` | Where you are in the loop: `3·2` is the third bar, second beat. It eases along the same position as the blue playhead, so the number and the line cannot disagree, and it freezes with the clock when everything is stopped. Pure arithmetic in the browser from `phase`, `bars` and `beatsPerBar` — no extra message. | — | state `phase`, `bars`, `beatsPerBar` |
| **METRONOME** | toggle | off by default | The click. Independent of everything else; count-in clicks even when this is off. | `set g.metro` | transport `metro` |
| **COUNT-IN** | toggle | off by default | Adds `count-in bars` bars of click before a recording starts, and starts the take exactly on the bar. | `set g.countIn` | state `countIn`, `/ldbeat` handler |
| **RHYTHM** | toggle + two steppers | `on` · kit 1–16 · pattern 1–200 | The drum engine: four **synthesised** voices (kick, snare, hat, perc) stepped by a 16th-note clock at the current bpm. `kit` picks the voice parameters (pitch, decay, drive); `pattern` picks one of 200 rows built from 5 kick × 5 snare × 4 hat × 2 perc. No samples, so there is no sample licence to carry. | `cfg rhythm.on/kit/pattern` | `\ld_rhythm`, `~rhythmSet` |
| master fader | fader | 0–1.2, default 0.8 | Master volume (post-limiter). Silences everything, click included. | `set g.masterVol` | `\ld_master` `vol` |

## 2. Track column (one per track, 1–N)

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| track number + `2L · 1 undo · HOT` | read-out | — | Layers committed, how many are undoable, `HOT` when the layer sum is near clipping. | — | state `layers`, `undoDepth`, `layerPeak` |
| **the big pad** (RC-505 *track button*) | pad | — | Context-sensitive: **EMPTY** → record layer 1 · **RECORDING** → close the take · **PLAYING** → record another layer (overdub) · **STOPPED** → resume playback (does *not* start a new take). Colour: grey empty, red recording, green playing, amber overdub, dim stopped. | `act press` | `~press` |
| **STOP** | button | — | Commits a take in progress, then sets `playing` 0. Layer content is kept. | `act stop` | `~stopTrack` → state 3 |
| **FX** | button | — | Opens the per-track FX drawer (§3) for this track. | — | — |
| volume fader | fader | 0–1.4, default 0.8 | Track output level, smoothed 10 ms. | `set t{i}.vol` | `\ld_track` `vol` |
| **UNDO** | button | depth = layers (max 4) | Drops the newest layer. The buffer is kept, so redo is instant. | `act undo` | `~undo` |
| **REDO** | button | — | Restores the last undone layer. | `act redo` | `~redo` |
| **CLEAR** | tap twice | — | Wipes this track: all layers dropped, `contentLen` reset. Tap once to arm (`sure?`), tap again within 2.5 s to confirm, so a stray finger cannot erase a phrase. A long-press is deliberately *not* used: on a touchscreen the OS claims the hold and turns it into a context menu, so the hold never completes. | `act clear` | `~clearTrack` |
| track meter | meter | — | Post-fader output level of this track. | — | state `meter` |

## 3. FX drawer (per track, opened by **FX**)

### Input FX — before the write head

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| INPUT | segmented (select past 4 sources) | `stereo` · `L` · `R` · `ch 3…n`, default `stereo` | Which hardware input feeds this track. `stereo` sums both channels (×0.7071); `L`/`R` take one channel and record it to both sides, so a mono mic and a mono guitar can be two tracks from one 2-channel interface. The list is built from `audio.in`, so an 8-input box offers 8 sources. The track header shows the choice as `IN ST` / `IN L` / `IN R`. | `set t{i}.input` | `~inputChannels` → `\ld_track` `input` |
| GAIN | knob | 0–2 (100 %), default 1 | Input gain into the gate and filters. | `set t{i}.gain` | `gain` |
| REC LEVEL | knob | 0–1.5 (100 %), default 1 | Level written into the buffer, before the `tanh` soft clip. | `set t{i}.recGain` | `recGain` |

### Noise gate

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| on/off | toggle | on by default | Enables the gate. | `set t{i}.gateOn` | `gateOn` |
| THRESHOLD | knob | 0.002–0.4, curved, default 0.02 (−34 dB) | Gate threshold, 3 ms attack / 60 ms release. Shown as dB. | `set t{i}.gateThr` | `gateThr` |
| ARM AUTO-REC | toggle | off | Next time the input crosses the threshold, recording starts on the loop boundary. Disarms itself after firing. | `set t{i}.autoArm` | `autoArm` → `/ldauto` → `~pending` |

### Filter (after the gate, also before the write head)

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| HIGH PASS | knob | 20–2000 Hz, curved, default 40 | 12 dB/oct high-pass. | `set t{i}.hp` | `hp` |
| LOW PASS | knob | 400–18 000 Hz, curved, default 16 000 | 12 dB/oct low-pass. | `set t{i}.lp` | `lp` |

### Loop (playback side)

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| length | segmented | 1 · ½ · ¼, default 1 | That track loops 1×, 2× or 4× per master loop (`loopRatio`). Stays locked to the same grid. | `set t{i}.loopRatio` | `loopRatio` |
| length | segmented | 1 · ½ · ¼, default 1 | That track loops 1×, 2× or 4× per master loop (`loopRatio`). Stays locked to the same grid. | `set t{i}.loopRatio` | `loopRatio` |
| **speed** | segmented + knob | ½ · 1 · 2 · 4 and continuous 0.25–4, default 1 | Varispeed. At `1` the track reads from the master phase and stays grid-locked; anything else free-runs its own phasor, reset when the track starts, so the material plays slower or faster and **re-pitches** (the hardware's SPEED knob). Not time-stretch: pitch moves with speed, by design. | `set t{i}.speed` | `speed`, `Phasor` in `\ld_track` |
| **MARK / MARK BACK** | buttons | — | `mark` bookmarks the current layer count; `mark back` drops straight to it instead of stepping down one layer at a time. The layers above the mark are gated but kept, so REDO still walks them back one by one. | `act mark` / `act markBack` | `~setMark`, `~markBack` |
| REVERSE | toggle | off | Reads the layer backwards. | `set t{i}.reverse` | `reverse` |
| FADES 0 | button | — | Resets both fades to zero. | `set t{i}.fadeIn/out` | `fadeIn`, `fadeOut` |
| FADE IN | knob | 0–5 s, default 0 | Ramp up at the start of the loop. | `set t{i}.fadeIn` | `fadeIn` |
| FADE OUT | knob | 0–5 s, default 0 | Ramp down at the end of the loop. | `set t{i}.fadeOut` | `fadeOut` |

### Delay (tempo-synced, `CombN` behind the read head)

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| on/off | toggle | off | Enables the delay. | `set t{i}.delayOn` | `delayOn` |
| TIME | knob | 0.06–2 beats, default 0.75 | Delay time in beats, recomputed when the tempo changes. | `set t{i}.delayBeats` | `delayBeats` (`beatLen`) |
| DECAY | knob | 0.05–8 s, default 0.4 | How long one repeat takes to fall 60 dB. This is the real feedback control — it is a decay time, not a made-up "feedback %". | `set t{i}.delayDecay` | `delayDecay` |
| MIX | knob | 0–1, default 0.25 | Dry/wet. | `set t{i}.delayMix` | `delayMix` |

### Reverb (per-track send into the shared bus)

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| SEND | knob | 0–1, default 0 | How much of this track goes to the shared `FreeVerb2`. Size/mix live in setup → loop → master chain. | `set t{i}.revSend` | `revSend` |

### Plugin slots (VST3 / VST2, insert chain after the read head)

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| slot 1–3 → `load…` | select | plugin catalog | Loads a plugin into that insert slot. | `svc vstOpen` | `VSTPluginController.open` |
| **UI** | button | — | Opens the plugin's own editor, or VSTPlugin's generic parameter window when the plugin has none. | `svc vstEditor` | `editor` / `VSTPluginGui` |
| **BYP** | toggle | off | Bypasses the slot (the plugin stays loaded). | `svc vstBypass` | `vB0..vB2` |
| **×** | button | — | Unloads the plugin from the slot. | `svc vstClose` | `close` |

---

## 4. Master footer (bottom)

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| INPUT L/R meters | meter | — | Hardware input level, straight from `SoundIn`. | — | `/b_getn` metre buffer slots 62/63 |
| master fader | fader | 0–1.2, default 0.8 | Same value as the transport master fader. | `set g.masterVol` | `\ld_master` `vol` |
| master meter | meter | — | Post-limiter output level. | — | metre buffer slots 60/61 |
| **SAVE** | button | — | Writes slot 1: one WAV per layer + `session.json` + plugin state. | `svc save` | `~writeSession` |
| **EXPORT** | button | — | Bounces 30 s of the master bus to `sessions/exports/`. | `svc export` | `~exportMix` |

---

## 5. Setup drawer

### Tab: AUDIO

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| DRIVER | select | auto · jack · alsa · asio | `auto` = JACK if available (PipeWire), else ALSA. `jack` never passes a device. `alsa` opens the hardware directly and takes it exclusively. `asio` (Windows) prefixes the device with `ASIO : ` so PortAudio picks the ASIO backend — the low-latency path. **Restart required.** | `cfg audio.driver` | `s.options.device` logic |
| ALSA DEVICE | select | `hw:N,M` list | Only shown for driver = `alsa`. These are the only names scsynth accepts. **Restart required.** | `cfg audio.device` | `s.options.device` |
| DEVICE + **clear** | read-out + button | — | Shown when a stale device string is in the config (e.g. a PipeWire node name from an older build, which makes scsynth fail with "couldn't initialize audio"). **clear** removes it. | `cfg audio.device = null` | — |
| SAMPLE RATE | stepper | 0 = default, or 44.1k…192k | Server sample rate. **Restart required.** | `cfg audio.sampleRate` | `s.options.sampleRate` |
| BLOCK SIZE | stepper | 16–2048, step 16, default 64 | SC control block. **Restart required.** | `cfg audio.blockSize` | `s.options.blockSize` |
| HW BUFFER | stepper | 32–4096, step 32, default 256 | Hardware buffer / latency. **Restart required.** | `cfg audio.hardwareBufferSize` | `s.options.hardwareBufferSize` |
| INPUTS / OUTPUTS | stepper | 1–16, default 2 | Channel counts on that one duplex device. **Restart required.** | `cfg audio.in` / `audio.out` | `s.options.numInput/OutputBusChannels` |
| engine stats line | read-out | — | cpu, sample rate, block, hw buffer, computed latency, VST count. | — | `svc stats` |
| **restart** | button | — | Restarts the engine so audio settings apply (the launcher restarts `sclang`). | `{op:"restart"}` | `s.quit` + exit 42 |
| **measure** | button | — | Re-requests the stats above. | `svc stats` | `~sendStats` |
| CONNECTIONS in ← / out → | read-out | — | Where PipeWire has the engine's ports wired right now, read from `pw-link`. | — | bridge `list_audio_devices` |
| **refresh** | button | — | Re-reads devices and connections (use after patching in `qpwgraph`). | `{op:"devices"}` | — |

### Tab: LOOP

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| TRACKS | stepper | 1–16, default 5 | Number of track columns and track synths. **Restart required.** | `cfg tracks` | `~nTracks` |
| LAYERS / TRACK | stepper | 1–4, default 4 | Undo depth and how many overdubs fit. **Restart required.** | `cfg maxLayers` | `~maxLayers` |
| MAX LOOP | stepper | 4–120 s, default 16 | Buffer length reserved per layer. Memory = tracks × layers × this × 192 kB/s. **Restart required.** | `cfg loop.maxSeconds` | `~maxSeconds` |
| CROSSFADE | stepper | 0.1–50 ms, default 1.3 | Seam fade length, shaped as a raised cosine (a linear ramp splatters high frequencies — audible as a buzz). Raise to 5 ms for dense bass. | `cfg loop.crossfadeMs` | `xfade` |
| RECORD OFFSET | stepper | ±4096 samples, default 0 | Shifts the write head against the grid to compensate interface latency. Positive moves the write earlier. | `cfg loop.recordOffset` | `recordOffset` |
| INPUT MONITOR | stepper | 0–1.5, **default 0** | Dry input heard while recording. `0` = loop only, which is right when your interface has direct monitoring / a hardware loopback — two copies at different latencies is comb filtering. | `cfg loop.monitor` | `monitor` |
| AUTO-CLOSE 1ST LAYER | toggle | on | The first take closes on the loop boundary instead of running on. | `cfg loop.autoClose` | `autoClose` |
| CONTINUE INTO OVERDUB | toggle | on | After that close, recording carries straight on as an overdub (hardware behaviour) instead of dropping to playback. | `cfg loop.autoDub` | `autoDub` |
| OVERDUB REPLACES | toggle | off | A new take **mutes the layers it covered** instead of layering on top — the hardware's replace mode. Their content is never erased, so UNDO brings them straight back. | `cfg loop.overdubMode` | `overdubMode` in `~commit` |
| QUANTIZE START | toggle | off | Start recording on the next loop boundary instead of immediately. | `cfg loop.quantizeStart` | `quantizeStart` |
| DUB DECAY | stepper | 0–95 %, default 0 | Every pass, existing layers lose this fraction — the classic dub fade. | `cfg loop.dubDecay` | `dubDecay` |

**Master chain** (same tab):

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| HIGH PASS | stepper | 0–2000 Hz, default 0 | Master high-pass (0/20 Hz = transparent). Live. | `set g.masterHp` | `\ld_master` `masterHp` |
| LOW PASS | stepper | 1000–19 000 Hz, default 18 000 | Master low-pass. Live. | `set g.masterLp` | `masterLp` |
| REVERB MIX | stepper | 0–1, default 0.6 | Wet level of the shared reverb. | `cfg loop.revMix` | `revMix` |
| ROOM SIZE | stepper | 0–1, default 0.8 | Reverb room size (damping follows inverted). | `cfg loop.room` | `room`, `damp` |

### Tab: CLOCK

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| CLICK LEVEL | stepper | 0–1, default 0.3 | Metronome level (its own bus, before the limiter). | `cfg clock.clickVol` | `clickVol` |
| COUNT-IN BARS | stepper | 0–8, default 1 | Bars of count-in before a take. | `cfg clock.countInBars` | `countIn` |
| ACCENT | stepper | 200–4000 Hz, default 1580 | Bar-one click pitch. Restart required. | `cfg clock.accentHz` | transport `accentHz` |
| CLICK | stepper | 200–4000 Hz, default 1050 | Other-beat click pitch. Restart required. | `cfg clock.clickHz` | `clickHz` |

### Tab: UI

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| SCALE | fader | 0.75–1.75, default 1 | Multiplies the root font size — the whole pedal scales. Persisted. | `cfg ui.scale` | — (`--scale`) |
| HOLD TIME | stepper | 200–2000 ms, default 800 | How long **CLEAR** must be held. | `cfg ui.holdMs` | — |
| SHOW MASTER BAR | toggle | on | Hides the footer for more track height. | `cfg ui.showMaster` | — |
| **fullscreen** | button | — | Browser fullscreen (kiosk alternative). | — | — |
| **check 48px** | button | — | Outlines every interactive element and counts any below 48 px (same as pressing `d`). | — | — |

### Tab: MIDI

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| DEVICE | select | all sources, or one MIDI source | Restricts the mapping to one input. | `svc midiDevice` + `cfg midi.device` | `~midiDevice` |
| **default layout** | button | — | Loads CC 20–27 press · 30–37 undo · 40–47 stop · 50–57 clear · 60 tap · 61 stop-all · 62 metronome. | `svc midiDefault` | `~midiDefault` |
| one row per target (`t0.press` … `g.metro`) → **learn** | button | — | Arms learn; the next CC or note maps to that target. Shown as the learned CC afterwards. | `svc midiLearn` | `~learnTarget` |
| gesture (notes only) | learned, not chosen | press · hold · double | A **note** carries the gesture you performed while learning it: release inside 300 ms → `press`; still down after 300 ms → `hold`; two taps inside 300 ms → `double`. The same pedal can therefore mean three things on three targets, and no extra UI is needed to pick one. CC mappings are continuous, which is what an expression pedal wants. | `svc midiLearn` | `~noteAt`, `~noteHold`, `~fireGesture` |
| **clear all** | button | — | Empties the map. | `svc midiClear` | `~midiMap = []` |

Diagnostics, used by the suite and handy when a pedal misbehaves: `svc midiAdd type num target gesture` installs a mapping without hardware, `svc midiSim num gesture` fires one. They are the only way to test the gesture timing.

The learned map persists in `ui/config.json` under `midi.map`.

### Tab: SESSION

| control | type | range / default | what it does | wire | engine |
| --- | --- | --- | --- | --- | --- |
| SLOT | stepper | 1–99, default 1 | Which memory save/load uses. Persisted. `sessions/slotN/` grows with the number, so 99 memories cost nothing until they are used. | `cfg session.slot` | — |
| **save slot** | button | — | Writes `sessions/slotN/`: `t<track>_l<layer>.wav`, `session.json`, `vst.txt`, `vst_*.bin`. | `svc save` | `~writeSession` |
| **load slot** | button | — | Restores layers, plugin chain and plugin state from that slot. | `svc load` | `~loadSessionDir` |
| **BOUNCE** | button | — | Merges **every track that has layers** into track 1 as a single layer, in one loop pass (`\ld_bounce`, its own `Line`, self-freeing). The destination's synth is stopped for the pass — it writes its buffers back every sample and would clobber the sum — then rebuilt. Sources are cleared afterwards. | `act bounce 0` | `~bounce`, `\ld_bounce` |
| **bounce 30 s** | button | — | Renders 30 s of the master bus to `sessions/exports/mixdown_*.wav`. | `svc export` | `~exportMix` |
| autosave | toggle | on | Snapshot of committed layers after every layer change. On the next start the layers come back **stopped and silent** — press a pad to play them. Turn it off for a session you do not want back. | `cfg loop.autosave` | `autosave`, `~autosaveTick` |
| **clear autosave** | button | — | Deletes the autosave snapshot now. (Clearing all tracks does this automatically.) | `svc clearAutosave` | `~clearAutosave` |
| **backup** | button | — | Zips the whole `sessions/` tree (every memory, the autosave, exports) into `sessions/backups/looper-<stamp>.zip` — one file to carry to another machine. Written by the bridge, not the engine. | `op backup` | — |
| **restore** | button | — | Extracts the newest backup back over `sessions/`. Members that would land outside `sessions/` are refused, so a hostile zip cannot write anywhere else. | `op restore` | — |

### Tab: 505

Read-only. Maps Boss RC-505 controls to their Looper OSS equivalents, and lists the
deliberate differences (grid length from BARS+BPM rather than AUTO, 4 layers
instead of one loop + undo, no drum patterns, effects are stock UGens / your VSTs).

---

## 6. Keyboard (laptop / no touchscreen)

| key | action | wire |
| --- | --- | --- |
| `1` … `8` | press track N (same as tapping the pad) | `act press` |
| `s` | stop the selected track | `act stop` |
| `u` | undo on the selected track | `act undo` |
| `space` | STOP ALL | `act stopAll` |
| `esc` | close the open drawer | — |
| `d` | toggle the 48 px target overlay | — |

The "selected" track is the last pad you touched (it gets a highlighted border).

## 7. Gestures that are not buttons

| gesture | meaning |
| --- | --- |
| drag a fader anywhere | jumps to that value immediately (no need to grab the thumb) |
| drag a knob vertically | full range over ~150 px; the dot shows the value |
| hold **CLEAR** 0.8 s | wipe the track (release early = nothing happens) |
| tap **BARS** | set loop length; the change is immediate and re-times existing layers |
| tap the **BPM number** | opens the numeric keypad (digits / clear / set) |
| tap **TAP** 2+ times | sets the BPM from the interval you tap |

## 8. Status the UI shows (no control)

| indicator | where | meaning |
| --- | --- | --- |
| playhead sweep | top of the tracks | loop position, eased toward the engine's beat points; freezes when the engine stops sending them |
| pad colour + label | track column | `EMPTY` grey · `RECORDING` red · `PLAYING` green · `STOPPED` dim |
| `2L · 1 undo · HOT` | track column | layers, undoable layers, and a hot layer sum |
| input / master meters | footer | hardware input and post-limiter output |
| boot overlay | full screen | "engine starting…" until the first state frame; red with the reason if the audio device cannot be opened |
| toast | bottom centre | errors, saves, exports, MIDI learn, connection state |
