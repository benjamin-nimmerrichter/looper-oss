# goal.md — Looper OSS 0.2: legible, standalone, and closing the RC-505 gap

This is the plan for the next round, written before the work. The plan the first
release was built against is archived at [`docs/goal-0.1.md`](docs/goal-0.1.md);
everything in it is shipped.

## Context

0.1.0 works: record → close into an overdub → layers → undo, five tracks, touch
UI, AppImage. Using it for real surfaced three gaps about the *package* and the
*readout* more than the audio engine, plus a tracker issue that is a whole
feature matrix.

1. **You cannot see where you are in the bar.** The only position indicator is
   the blue playhead line. A musician counting a four-bar loop wants `bar 3 ·
   beat 2`.
2. **Input routing is invisible.** `\ld_track` has always had an `input` selector
   (stereo sum / channel 1 / channel 2), so mic-on-L + guitar-on-R already works —
   but it is buried in the FX drawer, labelled `STEREO / CH 1 / CH 2`, and
   hard-coded to two channels although `audio.in` allows 16.
3. **The AppImage still asks for things** (see Phase C for the measured cost).

## Constraints (unchanged)

- Library-first: audio is stock SuperCollider / sc3-plugins UGens. No hand-written DSP.
- One config file (`ui/config.json`); the launcher generates `engine/_config.scd`.
- No UI build step, no runtime network.
- Touch first: targets ≥ 48 px; destructive actions use tap-to-arm, never a
  long-press (a touchscreen claims the hold for its context menu).
- `/ldstate` stays flat and positional: a new field means engine array +
  `GLOBAL_FIELDS`/`TRACK_FIELDS` + `onState`, all three, and the suite's counts.

---

## Phase A — position in bars and beats — **done**

The engine already sends `phase` (0..1 over the loop), `bars` and `beatsPerBar`,
so this is arithmetic in the browser: it eases along the same `playPos` as the
playhead, so the number and the line can never disagree, and only touches the DOM
when the beat changes. No protocol change.

## Phase B — input routing that matches a real interface — **done**

- `~inputChannels` builds the source list from `audio.in` when the SynthDef is
  compiled: index `0` is the stereo sum, `1..n` are the mono channels, `n+1` the
  internal test bus. Backwards compatible (`1` = channel 1, `2` = channel 2).
- The UI builds its list from `audio.in` (`stereo`, `L`, `R`, `ch 3…`), and the
  track header shows a badge (`IN L`) so the choice is visible without opening
  the drawer.
- Docs: worked example in the README, a row in `CONTROLS.md`, a section in
  `AUDIO.md`.

## Phase C — how much of the AppImage can carry everything

Measured on the build machine, because the answer decides the design:

| stack | closure | largest pieces |
| --- | --- | --- |
| GTK 3 + WebKit2GTK | 281 MB, 138 libs | WebKit 98.6, JavaScriptCore 40.6, ICU 33.1 |
| SuperCollider | 477 MB, 242 libs | Qt6WebEngine 203.7, ffmpeg 22, x265 20.8 (sclang links Qt) |

Bundling both is ~760 MB of libraries → a ~300 MB AppImage that only runs within
the same glibc generation, with WebKit's bubblewrap sandbox as a known failure
mode. Decision for 0.2: **do not bundle them**; instead the app names what is
missing and installs it in one step (`--install-deps`, shipped). Full bundling
stays on the table as a separate "fat" variant, and only if a machine without
those packages actually has to run it.

## Phase D — RC-505mkII parity (issue #3) — status

| | |
| --- | --- |
| D1 speed · D2 layer tricks · D4 mixer/routing · D5 rhythm · D6 MIDI · D7 memories · D8 rest | **done**, each verified in `scripts/smoke.py` |
| D3 FX banks | **done** — 4 insert slots, 4 recallable banks, an eight-step sequencer, toggle/momentary per slot |
| Phase E drawer screenshots | blocked: this sandbox's rasteriser turns text inside composited layers into wrong glyphs; `scripts/shots.py` is ready for a GPU machine |


Issue #3 lists the hardware's manual. It is a roadmap, not a sprint. Ordered the
way it can be built on what already exists:

### D1 — playback speed, per track — **done**
Half, double and continuous `loopRatio` (the engine already has `loopRatio` per
track; the UI and the transport rate need to expose 0.25×–4× continuously).
Speed changes pitch (varispeed) — the hardware's pitch-preserving mode is
time-stretch, which stays a non-goal.

### D2 — the layer tricks — **done**
- **Mark Back:** return to a previous state of the layer stack without stepping
  through undo one layer at a time (the `undone` counter already tracks it).
- **Bounce In:** sum several tracks into one and free the sources.
- **Overdub modes:** replace-while-playing is the one we do not have; the layer
  model already supports "record a new layer" and "gate the old one", so this is
  a mode switch, not new DSP.
- **Auto Rec:** the engine already has `\ld_autodetect`; it needs to be exposed
  as a binding (see D6).

### D3 — FX banks — **done** (four insert slots, four recallable banks, an eight-step sequencer, toggle/momentary per slot)
4 simultaneous effects per track-bank, four banks, toggle/momentary, and a step
sequence for parameters. The VST slot path (VSTPlugin) is the natural carrier:
a bank is a list of plugin slots plus their parameters. The 49/53-effect
libraries are the real cost and will be curated to what exists as free plugins,
not invented.

### D4 — mixer and routing — **done**
Independent input mixer (2 mic + 2 stereo pairs), input EQ and mic compressors,
3 stereo output buses with per-track routing, a headphone/cue mix. The engine
already sums per-track stereo buses; this is bus gymnastics plus UI.

### D5 — rhythm engine — **done** (synthesised)
200 patterns and 16 kits. This is *content*, not code: the transport already has
a phasor to trigger against. It needs a sample source whose licence allows
redistribution (see `docs/LICENSING.md`), or generated patterns from stock UGens.

### D6 — MIDI depth — **done**
Press/hold/double-click per control, expression pedals, 16 assignable targets,
save/recall of the map. The MIDI learn path exists; this is the mapping model and
the UI to edit it.

### D7 — memories, import/export, backup — **done**
99 memories that store the whole session (tracks + FX + playback + control map +
rhythm), WAV import, backup/restore as one archive. `sessions/slotN` is the seed;
`session.json` already serialises the UI state.

### D8 — the rest — **done (verified, not built)**

All three bullets already hold, so they were audited rather than re-implemented:

- **Independent per-track playback settings.** `CONTROLS.md` lists 21 per-track
  controls, each sent as `set t<N>.<field>`: speed, loop ratio, reverse, fade
  in/out, delay, high/low pass, gate, volume, pan, mute, reverb send, record
  gain, and the record source.
- **"Bounce to DAW" style export.** Two levels already exist: `save slot` writes
  one WAV **per track per layer** (`sessions/slot1/t0_l0.wav`, `t0_l1.wav`, …),
  which is stems; `export` bounces the master to
  `sessions/exports/mixdown_*.wav`.
- **Custom control assignments.** The MIDI map assigns any target (`t0.press`,
  `g.stopAll`, …) to a CC or a note, and a note carries press/hold/double.
  What does **not** exist is an on-screen button-layout editor — assignment is
  through the map, not by dragging controls. That is the one gap, recorded here
  rather than glossed over.

**Scope honesty:** D1–D2 are reachable on the current engine. D3–D7 are each a
release of their own, and D5 depends on a sample licence that does not exist yet.

## Phase E — documentation and screenshots

- README: input routing example, position readout, what the AppImage carries and
  the measured reason it does not carry more.
- `CONTROLS.md`: position readout, input source control, `driver = asio` row.
- `AUDIO.md`: how many channels reach the engine and how `input` selects them.
- **Every screenshot regenerated** after any UI change: seven settings tabs, the
  main screen, the FX drawer.

---

## Closed from the tracker

| issue | state |
| --- | --- |
| **#1** "audio stops working after the PC sleeps" | **fixed in 0.1.0** — the launcher detects the resume (realtime vs monotonic clock drift) and restarts the engine; the session comes back stopped. |
| **#3** "not all RC-505mkII features implemented" | mapped to Phase D above. |
| **#2** "Windows support not tested" | **deferred, deliberately.** It becomes its own task later: verify under Wine first, then a real Windows host. No Windows work is planned in this document. |

## Definition of done

- [ ] `./scripts/smoke.py` → all checks, including per-track record source
- [ ] `node scripts/uicheck.mjs` → `UI OK`, screenshots regenerated
- [ ] `sclang /tmp/probe.scd` → no errors
- [ ] the AppImage boots and plays on this machine
- [ ] README/CONTROLS/AUDIO/PROTOCOL updated in the same change
- [ ] `python3 scripts/sweep.py` → nothing left running

## Risks / unknowns

1. **RC-505mkII parity is content-heavy.** The 49/53-effect libraries and the 200
   rhythm patterns are the parts that cannot be written in a weekend, and the
   drum kits need a redistributable sample licence.
2. **Per-channel input past two** needs the device to actually expose that many
   channels; `audio.in` already feeds `numInputBusChannels` and scsynth reports a
   mismatch at boot.
3. **Speed/pitch.** Half and double speed re-pitch. Anything that keeps pitch is
   time-stretch, which stays out of scope.

## Non-goals

- Time-stretch, timeline/arrangement, MIDI sequencing.
- Bundling the audio server (see Phase C's numbers).
- Windows support **in this round** — it is the whole of 0.3.0, below.

---

## Fixed: the mixdown export was silent

Every `svc export` wrote a correctly sized WAV of zeros, and always had — the
suite only ever asserted the file existed. Two causes, both now fixed:

1. **Group order.** Audio buses are cleared at the start of every block, so a
   synth in an *earlier* group reading a bus written by a *later* one sees zeros
   for ever. `\ld_capture` sat in `~grpClock`; `\ld_master` runs in `~grpMaster`.
   The capture is now created in `~grpMaster` with `\addToTail`. `RecordBuf`
   itself was never broken — the same code placed after the writer records at
   full level (0.2 peak on a 0.2 tone).
2. **Interleaving.** While chasing (1) the capture was rewritten with `BufWr`,
   whose index is in frames and which interleaves by itself; scaling the index
   by the channel count wrote one frame in two. Reverted to `RecordBuf`, which
   handles it.

Verified: an exported WAV now holds interleaved stereo at the recorded level.

**Still open — the audio-truth tests.** They need a *known signal* recorded and
then measured out of an exported WAV, and the suite must stay silent.
`svc silence` (`s.mute`) silences the hardware *and* the captured bus (measured:
export rms 0.0043 while the track meter read 0.13), so a muted run cannot
measure anything. Pick one before writing them:
- capture the pre-master sum (busA+busB+busC) instead of `masterBus`, which
  `s.mute` does not touch, or
- run the audio checks with `masterVol` around 0.08 and accept a quiet tone.


## M0 result (measured, 0.2.0 engine + 2639b83)

Two findings, and the first is not about the seam at all.

1. **The mixdown export was silent again, for a third reason.**
   `Buffer.alloc` is asynchronous, and `~exportMix` created the capture synth in
   the same message. `BufDur.kr` therefore read **0**, `Line.kr(0, 1, 0)` was done
   on the first block, and the file was written with about 100 ms of audio and
   the rest zeros. The capture now allocates, waits (`s.sync`) in a `fork`, then
   starts, and takes `secs` as an explicit argument instead of asking the buffer
   its duration. Export rms went 0.0035 -> 0.1804 on the same take.
   That also explains every earlier "the export is empty" report that survived
   the group-order fix.
2. **No metallic burst with a steady tone.** `scripts/seamprobe.py` records take
   1 with a 220 Hz tone, rolls through the auto-dub hand-off, exports and reports
   5-15 kHz band energy per 100 ms window against the take's median:
   worst window **2.2x**, threshold 4x -> *no burst found*. Biggest sample step
   0.065 at t=1.02 s (**13x** the median step), i.e. a discontinuity, but not in
   the 5-15 kHz band.
   A steady tone is not drums; the listening half of M0 is still the user's.

## Open: metallic buzz at the auto-dub transition

Heard by the user, while overdubbing several layers in a row: at the hand-off
between the first take and the dub there is a digital buzz, main energy around
**10 kHz, metallic**. It is a transition artefact, not a level problem, and it
only appears when the loop rolls from take 1 into take 2.

**Measured fact: it lasts about 100 ms.** That rules out the hand-off window
(a few ms), and it is longer than the seam: `xfadeMs` is clamped to 0.1-50 ms and
defaults to 2, so the seam dip is 4 ms unless it was driven to the ceiling. A
100 ms artefact at the moment a *new layer* opens points instead at **that
layer's buffer**: `~startRec` sends `buf.zero` and opens the write gate in the
same breath, so whatever the layer held before -- an earlier take, or content
restored from a session -- is played into the mix until the zero lands. That is
also a better explanation for the missing drum downbeat than a few ms of
overwrite: it would take out ~100 ms of the take's first pass, every time.

**Candidates, none proven -- do not "fix" one of these blind:**

1. **The overdub hand-off itself.** `/ldwrap` reaches sclang after the wrap, so
   the write head stays open at position 0 for a few ms and writes the incoming
   signal over the start of the committed take: a discontinuity at the seam,
   repeated every pass. (`SetResetFF` tried to fix this and broke DUB and STOP
   ALL -- reverted in 7b82ad0. Any retry must be measured.)
2. **The seam fade.** `seam` dips the layer stack to 0 at `pos` 0 and
   `contentLen`; a fade that short plus a jump in `wpos`/`rpos` at the wrap reads
   as "metallic" rather than "click".
3. **`~lerpRead` at the wrap.** Invariant 20 fixed the modulo, but the
   fractional index just under `contentLen` is where a rounding error would
   still show, and it would show as HF noise.

**Settle it before touching code:** record a 220 Hz tone as take 1 with
`autoClose`/`autoDub` on, stop after the second pass, export, and read the seam
with `scripts/wavcheck.py` -- the step across the loop point and the HF energy in
the 5-15 kHz band, layer 1 alone and the mix. Whichever number moves identifies
the candidate.


## Carried into 0.3.0 (decided with the user)

0.2.0 ships with these known and documented rather than fixed:

- **metallic buzz at the auto-dub hand-off** (~10 kHz, ~100 ms). The recording
  layer is no longer played back while it records (2639b83), which removes the
  most likely cause -- the write head is derived from the read head, so playback
  read the sample being written. If it survives that, measure the seam.
- **the first take can lose its opening ~100 ms** into an overdub (the drum
  downbeat). Same neighbourhood.
- **0.3.0 also adds Windows**: WASAPI/ASIO, the Windows paths in the engine
  (`pkill` in `Engine.stop()`), an installer that has actually been run.
- The longer-term shape both of these want is the user's design: **one record
  buffer and one play buffer per layer, swapped at the loop point, mixed only
  when it is safe** -- instead of reading a buffer that is being written.

## Next: v0.3.0 — Windows

Linux is the supported platform in 0.2.0. Windows is the next release, and the
bar is not "it compiles" but the same evidence the Linux build carries.

**Audio backend.** WASAPI and ASIO device enumeration; `driver = asio` verified
against PortAudio's actual device names (`ASIO : <name>`) rather than assumed;
`hardwareBufferSize` mapped to the ASIO buffer; a device picker that offers only
what scsynth will accept, instead of ALSA names on a Windows host.

**Engine.** scsynth/sclang start-up and shutdown on Windows paths (`\`
separators, `sessions/`, the staged home), and `Engine.stop()`'s `pkill` fallback,
which is Unix-only today — a Windows build must not depend on it.

**Packaging.** PyInstaller onedir plus the Inno Setup per-user installer, verified
by installing on a clean machine, recording a loop, exporting a mixdown. The
pipeline already exists — `.github/workflows/windows-experimental.yml` builds on
`windows-latest` and uploads `-experimental` artefacts — so 0.3.0 starts from a
working build step, not from an empty workflow.

**Evidence.** `scripts/smoke.py` and `scripts/uicheck.mjs` run on Windows, or a
documented subset with the reason each check is excluded. The "unverified"
caveat in the README is removed only when that exists.

**Order.** Wine first — it is cheap and catches path and registry assumptions —
then a real Windows host.

**Acceptance.** A Windows user installs from the `.exe`, records and overdubs
five tracks, saves a session, exports a mixdown, and the suite passes — with the
evidence quoted in the release notes.
