# Looper OSS audio

Everything here is stock SuperCollider / sc3-plugins UGens. This document
explains the graph, the gain staging, and the two decisions that make the
looper behave (seam fade and `contentLen`).

## Signal flow

```text
                       ┌──────────── input FX (before the write head) ───────────┐
SoundIn 0..n ─▶ select ──▶ gain ──▶ gate ──▶ highpass ──▶ lowpass ──▶ tanh ──┬──▶
                       └───────────────────────────────────────────────────────┘
                                                                              │
   ┌──────────────── layer stack (BufRd × maxLayers) ────────────────┐        │
   │  b0×g0 + b1×g1 + b2×g2 + b3×g3   ── × seam window ── × reverse   │        │
   └──────────────────────────────────────────────────────────────────┘        │
                     │                                                         ▼
                     │                                            BufWr (recOn × sig)
                     ▼
             + monitor(sig × recOn) ──▶ [VST slot 0..2] ──▶ delay(CombN)
                     │                                         │
                     └──────────────▶ fade in/out ──▶ pan/vol ──▶ mix bus (2 ch / track)
                                                                  └──▶ reverb send
master: Σ(busA·gA + busB·gB + busC·gC) + FreeVerb2(send) + click ──▶ master HP/LP ──▶ Compander ──▶ clip .98 ──▶ out
```

One `Phasor` in `\ld_transport` writes loop phase to an **audio bus**. Every
track derives its own phase from that bus:

```text
tph  = (phase × loopRatio) % 1        -- loopRatio 2 means the track loops twice per bar-loop
pos  = tph × (contentLen - 1)          -- read position, per-sample
```

Because the phase comes from one source, tracks cannot drift, and a track with
`loopRatio = 2` stays locked to the others.

## Rhythm engine

## Output buses

There are **three** stereo buses, each as wide as the track count, so every
track owns a pair on every bus. A track's `route` picks which one it writes
into (`~busFor`); changing it is `synth.set(\outBus, …)` plus the meter's bus, so
it takes effect without rebuilding anything. A is the main mix and carries the
reverb send, the click and the drums; B and C are dry. `\ld_master` sums each
bus with its own gain and then adapts to the device: six or more output channels
get a pair each, four gives A and (B+C) on 2/3, two folds everything into 0/1.
The master compressor and limiter run per bus, so a hot mix on C cannot clip the
main output.

Bus **C** doubles as the cue bus: a track with `cueOn` writes its post-fader
stereo pair into C *in addition to* its route, scaled by `cueSend`. On a device
with six or more outputs that is a separate headphone pair; on a stereo device
it folds into the main output like everything else. No extra bus, no extra
group, and the master limiter still guards it.

`\ld_rhythm` is a fourth source, on its own bus, summed by `\ld_master` after the
track mix:

```text
Impulse(16th) ─▶ Stepper(0..15) ─▶ BufRd(patternBuf, step + voice*16)
   kick  : Sine(kickHz) x perc-env
   snare : noise + Sine(snareHz) x perc-env
   hat   : HPF(noise, 7 kHz) x perc-env
   perc  : Sine(420) x perc-env
```

The pattern is 64 frames (4 voices × 16 steps) written with one `setn`, so
changing a pattern is one buffer write and no recompilation. Kits are parameter
sets — 16 rows of pitch/decay/drive — not samples: a shipped kit would drag a
licence along, and every voice here is a stock UGen. The step clock is
`bpm / 60 * 4`, so it follows a tempo change through `~applyRate`, and it keeps
running when every track is cleared.

## Step sequence

`\ld_track` carries an eight-step sequencer on its low pass: a `Stepper` clocked
at eighth notes (`2 / beatLen`) reads an 8-frame buffer per track and adds up to
9 kHz to the cutoff. The pattern is one `setn`, so editing it costs a buffer
write, and `seqOn` gates the whole thing. It is clocked from the track's own
`beatLen`, so it follows a tempo change without extra plumbing.

## Speed (varispeed)

`tph` — the track's read position — is normally `(masterPhase × loopRatio) % 1`,
so every track is locked to one grid and cannot drift. A speed other than 1
cannot come from that phase: `phase × 0.5 % 1` would replay the first half
forever rather than play the loop slowly. So the track free-runs its own
`Phasor` at `speed / contentLen` samples per sample, reset on the rising edge of
`playing`:

```text
tph = speed == 1 ? (masterPhase × loopRatio) % 1
                 : Phasor(playing rising edge, speed / contentLen, 0, 1)
```

One pass then takes `contentLen / speed` samples, so the material slows down and
drops in pitch (0.5× is an octave down) — varispeed, exactly the hardware's
SPEED knob. Anything that keeps the pitch is time-stretch and stays out of
scope. The seam window is computed from `pos`, so it still zeroes at both ends of
the buffer and a wrap cannot click. `loopRatio` stays what it was: a grid-locked
subdivision (½ and ¼ loop) that does not free-run.

## Input mixer

Each physical input channel has its own strip before any track sees it:

```text
SoundIn(ch) ─▶ gain ─▶ high pass ─▶ low pass ─▶ input bus (channel ch)
```

`\ld_input` is one synth per channel, created before the tracks in the same
group, writing channel `ch` of `~inBus`. `~inputChannels` then reads that bus
instead of `SoundIn`, so the gain and filters are **shared** by every track that
selects the channel — trim a microphone once, not once per track. The strips run
before the track synths in `~grpTracks`, so the bus is written before it is read.

## Input routing

`audio.in` decides how many input channels the server opens
(`s.options.numInputBusChannels`). From that number the track SynthDef builds its
record-source list once, when it is compiled:

| `input` | source |
| --- | --- |
| `0` | `(ch 0 + ch 1) × 0.7071` — the stereo sum, as one channel |
| `1..n` | channel `1..n` alone, recorded to both sides |
| `n + 1` | the internal test bus — used by the tests, never offered in the UI |

A 2-input interface therefore offers `stereo`, `L`, `R`; an 8-input one offers
`stereo`, `ch 1 … ch 8`. Nothing downstream changes: the chosen signal still
goes through gain → gate → filter → tanh before the write head. `stereo` is the
only mode that attenuates (×0.7071), because two channels summed can reach 2.0
and would clip before the tanh ever sees it.

Mono entries are recorded to **both** sides, so a mono mic or guitar is centred.
Recording two different instruments to two different tracks from one interface is
just two tracks with `input` 1 and 2 — the same signal path, no extra routing.

## contentLen vs loopLen — why a tempo change is free

`loopLen` is the live grid length in samples (`bars × beatsPerBar × 60 / bpm ×
sr`). `contentLen` is **frozen** per track when its first layer starts
recording: it is the sample length the material was recorded at.

Playback always reads `contentLen` samples over the current loop duration, so
changing the tempo or the bar count *re-pitches* the material — varispeed, like
the hardware's SPEED CHANGE — with no resampling, no scratch buffers, and no
copy. A layer recorded after a tempo change is written with the same scaling,
so old and new layers stay coherent.

The trade-off is honest: dropping the tempo a lot spreads `contentLen` samples
over a longer loop, so the material plays slower and lower. There is no
time-stretch, by design.

## Seam fade (the click killer)

Reading a buffer in a loop wraps from `contentLen-1` to `0`. If the material is
not continuous across that boundary — it almost never is — you get a click on
every pass. The track applies a window to the layer sum:

```text
seam = min((pos + 1) / xfade, ((contentLen - 1 - pos) + 1) / xfade).clip(0, 1)
layers × seam
```

`xfade` is `loop.crossfadeMs × sampleRate` (default 1.3 ms, settings 0.1–50 ms),
and the window is a **raised cosine** (`0.5 - 0.5·cos(π·x)`), not a straight
ramp: a linear ramp has a kink at each end, and a kink is broadband energy —
audible as a buzz on every pass. Hann is continuous in value *and* slope.

This is a short fade rather than a two-head crossfade: it guarantees zero at the
wrap, costs one multiply, and the 1.3 ms dip is inaudible. The write side is
*not* windowed, so nothing gets baked into a layer.

## Gain staging

- **Input:** `gain` (0–2) → gate → HP/LP → `recGain` (0–1.5) → `tanh`.
  The `tanh` means a hot pass compresses instead of destroying a layer; nothing
  can write above ±1.
- **Layers:** each layer contributes at gain 0 or 1; commit/undo is a 10 ms
  `LagUD` on that gain, never a discontinuity.
- **Track out:** `vol` (0–1.4) with 10 ms smoothing, `pan` smoothed, `muted`
  smoothed. Pressing stop ramps `playing` over 5 ms.
- **Reverb send** is post-volume (it follows the fader) and post-pan.
- **Master:** mix (32 channels = 16 stereo pairs) + reverb + click → optional
  master HP/LP → `Compander` (threshold 0.9, ratio 10:1, attack 1 ms) →
  `clip2(0.98)` → `vol`.
- **Click** has its own level (`clock.clickVol`) and its own bus, so the
  metronome can be loud without touching the music and the master fader still
  affects everything.

## Metering

`Amplitude.kr` (attack 1 ms, release 80 ms) per track out pair, plus the layer sum
(release 250 ms) so you can see what is in the buffer even when the track is
stopped. Master out and hardware input have their own entries.

Delivery is deliberately boring: the small `\ld_meter` / `\ld_global_meter` defs
write those values with `BufWr.kr` into a 64-frame buffer, sclang sends `/b_getn`
at 10 Hz, and one persistent `/b_setn` responder stores them. `/ldstate` carries
the result to the UI. `SendReply` is **not** used — from the big SynthDefs it
arrived with the wrong number of values, so meters and the playhead were dead for
a while without any error.

## CPU

Measured on the development machine (Ryzen-class, 12 threads, 48 kHz):

| configuration | avgCPU |
| --- | --- |
| 5 tracks × 4 layers, idle | 2.2 % |
| 8 tracks × 4 layers, one layer each | 3.3 % |

That is percent of one core, from `s.avgCPU`, with 11 synths running. The track SynthDef is ~45 UGens
(4 BufRd + 1 BufWr + 2 filters + gate + delay + 3 empty VST slots + meters), and
`s.options.numWireBufs = 512` leaves plenty of headroom.

## Latency

Round trip = hardware buffer + SC block + interface. With PipeWire at quantum
256 the engine reports ~6.7 ms; at the default quantum 1024 it is ~21 ms. You
can compensate the systematic offset against the grid with
`loop.recordOffset` (samples, ±8192) — positive moves the write head earlier.
The settings drawer shows the computed value under ENGINE.

## Recording model

A track is a stack of layers, not one overdub buffer:

- press on an empty track → record layer 0 (and freeze `contentLen`)
- first layer auto-closes on the loop boundary (`loop.autoClose`)
- press again → record layer 1 while 0 keeps playing
- undo sets the top layer's gain to 0 (data kept, so redo is instant)
- clear drops all gains; the next recording overwrites the buffers

At most `maxLayers` (1–4) layers per track, which is also the undo depth. Layers
are why undo costs nothing and why there is no buffer arithmetic anywhere in the
engine.

**Content, gain and the undo stack have to agree.** A layer's buffer, its gain
and the counters `layerCount`/`undone` are three views of one fact, and a bug in
any one of them shows up as "the looper ate my take". The rules the engine keeps:

- `~startRec` zeroes the buffer it is about to write, so it **clears `undone`** —
  a redo above the write head points at content that no longer exists.
- `~redo` refuses while a take is open, and refuses to restore a gain for a
  buffer that is not there.
- `~undo` down to zero layers also sets `playing = 0`. An "empty" track that is
  still playing makes the next pad tap stop instead of record.
- REPLACE mode mutes the layers a take covers, but **a take that recorded
  nothing is dropped instead**: the buffer is read back in 48 000-frame chunks
  and, below −40 dBFS (0.01 peak), the layer comes off the stack and the gains
  the mute just removed are restored. Nothing was gained, so nothing is lost.
- Dub decay is **per track and only while it is playing**, and never touches the
  layer the write head is on. The instrument does not fade a loop the player is
  not touching.

The read-back walks the whole layer buffer rather than its first second: a take
starts wherever the play head is, so the content is not necessarily at index 0
(which is also why `probe`'s `peak0..peak3`, reading 48 000 frames, can report 0
for a take that is perfectly audible — it is a diagnostic window, not a verdict).

### Merging (issue #6)

`\ld_bounce` sums up to eight buffers with per-buffer gains into one destination
over one loop pass. Both merges are that same def:

- **Merge tracks** — sources are the named tracks' layers, destination is the
  target track's layer 0; the sources are freed afterwards.
- **Merge layers** — sources are *one* track's own layers, destination is its
  layer 0, which is also source 0. Reading and writing the same buffer is safe
  here because the read is a dependency of the write, and it is the same overlap
  the original bounce has always had.

Both stop the destination's synth for the pass: it writes its buffers back every
sample (`w0..w3` closed means "write my own content"), and that write-back would
clobber the sum as it is being built. The synth is rebuilt afterwards.

Merging *layers* is destructive — the stack has no spare slot to keep the
originals in, and the layer model has no concept of a hidden backup — so the UI
arms it first (`sure?`). Merging fewer than two layers is refused rather than
silently doing nothing, and neither merge touches a track with a take open.

## Sleep and resume (issue #1)

A suspend tears the audio device down and rebuilds it. `scsynth` survives that
with no exit code, meters still moving and no sound — so an exit-code watchdog
cannot see it, and neither can the player.

The launcher detects the sleep without a D-Bus client: `CLOCK_MONOTONIC` stops
during suspend and `CLOCK_REALTIME` does not, so subtracting one elapsed time
from the other leaves the sleep duration and ~0 for an ordinary tick. Over 5 s
(an NTP step is slewed, a sleep is not) it restarts the engine.

**The restart is not the fix; the engine coming back is.** After relaunching, the
launcher waits for the engine's own `*** Looper OSS: engine ready` line, with a
byte offset taken before the restart so an old line cannot satisfy it, and logs
how long it took:

```text
--- resume detected (600s asleep): restarting the engine
--- resume: engine ready 2.8s after the relaunch
```

If the line never arrives the log says so, in those words. That distinction is
the whole point: "restarted" and "working" are different facts.

Two things then have to be true, and both are checked rather than assumed:

- **Which endpoint the engine feeds.** After every `/ldready` the bridge asks
  the graph (PipeWire on Linux, scsynth's boot log on Windows, which prints
  `Out: <backend> : <endpoint>`). It logs the answer and, if it differs from
  `audio.device`, says so in the UI — the wrong endpoint can be a dead one, with
  the meters still moving.
- **That the device opens at all.** A named interface that will not open is the
  shape of a resume that took it away. The bridge clears `audio.device`, writes
  a fresh `engine/_config.scd`, and the engine — which already reported the boot
  failure — waits 3 s, re-reads the config (`~cfgFile.load`, then `~audio =
  ~cfg[\audio]`, because `~audio` is a copy), and tries once more. One retry,
  then it reports `gave up` and the UI shows the reason instead of a silent,
  half-booted engine.

Measured here: a device the system does not have produced, in order,
`could not open the audio device` → `retrying the audio device, asking for
default` → `engine ready (5 tracks, device default)`.

## Master chain order (issue #7)

```
buses A/B/C + reverb + click + drums
  -> static HP/LP      (setup: masterHp / masterLp)
  -> DJ filter         (performance: g.dj, bipolar, centre = bypass)
  -> master FX slot 1  (filter | delay | drive | tremolo)
  -> master FX slot 2
  -> Compander + clip2(0.98) * masterVol
  -> hardware out, and the capture bus the export reads
```

Everything that can boost — a resonant sweep, a drive — is *before* the
limiter, so the limiter sees it instead of the interface. The DJ filter is
`MoogFF` rather than `BLowPass`/`BHiPass`: those recompute biquad coefficients
whenever a parameter changes, which a sweep does every block, and their help
file warns that a frequency near 0 produces glitches and very loud artefacts.
`MoogFF` is a state-variable design built for sweeping, and the frequency is
clamped to 20 Hz … 20 kHz regardless. At `dj = 0` the filter branch is not
selected at all, so centre is a true bypass rather than a filter that happens to
be transparent.

The master FX slots compute all four candidate effects and `Select` one, because
a UGen graph cannot short-circuit: the cost is one filter and one comb per slot,
which is small next to five tracks of `BufRd`/`BufWr`, and it means a slot can be
switched while it plays instead of rebuilding the master synth.
