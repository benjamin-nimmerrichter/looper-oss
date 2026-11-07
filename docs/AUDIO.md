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
master: Σ mix + FreeVerb2(send) + click ──▶ master HP/LP ──▶ Compander ──▶ clip .98 ──▶ out
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
