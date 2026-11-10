# goal.md — Looper OSS 0.4.0: the six open issues

This is the plan for the next round, written before the work. The plans earlier
releases were built against are archived: [`docs/goal-0.1.md`](docs/goal-0.1.md),
[`docs/goal-0.2.md`](docs/goal-0.2.md),
[`docs/goal-0.3.0.md`](docs/goal-0.3.0.md). Everything in them is shipped.

0.3.0 is out and the instrument works: five tracks, four layers, four VST inserts
per track, rhythm, MIDI gestures, 99 memories, sessions, backup, Windows. What is
left is six GitHub issues, and they are **not the same kind of thing** — treating
them the same is how a round turns into a vague promise.

| issue | kind | what it actually is | milestone |
| --- | --- | --- | --- |
| #5 looping removes sounds | defect, **data loss** | content, gain and undo can disagree | M1 |
| #1 sleep kills the audio | defect, **silence** | detection works; the device is unverified | M2 |
| #6 merge tracks | feature, small | the hard part (`\ld_bounce`) already exists | M3 |
| #7 performance FX | feature, new surface | master FX with live knobs + a DJ filter | M4 |
| #4 Ableton Link | integration | `LinkClock` ships in the SuperCollider we target | M5 |
| #3 RC-505 parity | **roadmap** | 10 sections of a hardware manual | M6 |

There is a seventh item, reported outside the tracker: **the playhead hides its
own ruler**. It is two CSS declarations and a regression check, so it rides along
with M0 rather than earning a milestone.

The rule for the whole round: **a milestone is done when there is evidence, not
when the code compiles.** For the two defects that means a measurement that fails
before and passes after. For the features it means a check in `scripts/smoke.py`
that would fail if the feature were removed.

**How to read this file.** §1 maps every requirement of every issue to a status
and a milestone, so nothing is silently dropped. §2 is what was actually measured
or read out of the tree before planning, including the SuperCollider documentation
that changes how M4 and M5 have to be built. §4 is the work. §5 is the decisions
that need a human. §6–§9 are the boundaries.

---

## 1. Issue traceability

Statuses below are read from the code and the suite, not from `FEATURES.md`
(which has drifted — M6 fixes it).

### #5 — Looping does sometimes remove sounds → **M1**

| what the issue says | status |
| --- | --- |
| auto layering works badly | 3 mechanisms found, see §2 |
| "deletes the sounds instead of dubbing every loop over the last" | REPLACE mode mutes everything below (`~commit`) |
| "sounds get deleted when a silent part overdubs an already recorded layer" | same, plus a silent take commits as a mute |

### #1 — Audio stops working after sleep → **M2**

| what the issue says | status |
| --- | --- |
| "when I exit sleep mode with the looper open, sound out stops" | suspend detection exists and is exercised by simulation; the device coming back is **not** verified |

### #6 — Tracks should be able to merge → **M3**

| what the issue says | status |
| --- | --- |
| merge multiple tracks into one (Bounce In) | ✅ exists (`~bounce`, all tracks → one) |
| merge a selection, not everything | ❌ M3 |
| "record 4 drum layers and when happy join them" | ❌ M3 — per-track **layer** merge does not exist |

### #7 — There should be performance effects → **M4**

| what the issue says | status |
| --- | --- |
| 1–2 FX with knobs on the master | ❌ exists only as two steppers in the settings drawer |
| configurable, tweakable live | ❌ no master FX slots at all |
| DJ-style filter, filter sweep | ❌ HP/LP exist but no resonance, no knob, no sweep |

### #4 — Ableton Link → **M5**

| what the issue says | status |
| --- | --- |
| full integration | `LinkClock` verified working here; tempo + phase design in §4/M5 |
| "useful for potential users" | local multicast only, no internet, house rules intact |

### #3 — RC-505mkII parity → **M6** (subset) + declined list (§7)

This issue is ten sections of a hardware manual plus its own acceptance criteria.
Laid out against the code:

#### 1. Loop Engine

| requirement | status |
| --- | --- |
| 5 simultaneous stereo tracks | ✅ default 5, config 1–16 |
| independent controls + volume fader per track | ✅ |
| record / playback / overdub / clear per track | ✅ |
| **3 overdub modes** | ⚠️ **2 of 3**: layer-on-top, and REPLACE. The third is not named by the issue → §5 decision D1 |
| Undo / Redo | ✅ (M1 makes it consistent) |
| Mark Back | ✅ |
| Bounce In | ✅ → M3 adds selection and layer merge |
| Auto Rec, assignable to a control | ❌ **dead code**: `\ld_autodetect` is created per track (`:361`, `:544`) with an `arm` argument that nothing ever sets — no set key, no action, no UI. The detector exists; the switch does not |

#### 2. Playback Control

| requirement | status |
| --- | --- |
| half and double speed | ✅ (½ · 1 · 2 · 4) |
| continuously adjustable speed | ✅ 0.25×–4× per track |
| **speed with or without pitch change** | ❌ **declined** — "without" is time-stretch, see §7 |
| independent playback settings per track | ✅ |

#### 3. Audio Effects

| requirement | status |
| --- | --- |
| 49 Input FX / 53 Track FX types | ❌ **declined as written** — see §7 (VST carries this) |
| up to 4 simultaneous effects | ✅ four VST insert slots per track |
| 4 user-configurable banks | ✅ (`vst.banks`) |
| save / recall combinations | ✅ |
| toggle + momentary activation | ✅ per slot |
| step sequence for parameters | ✅ eight-step, per slot |
| Harmony / Electric FX | ❌ VST territory, declined (§7) |
| dedicated FX/Track buttons per track | ✅ on a touchscreen, the drawers are the buttons |
| customizable function assignments | ⚠️ the learn list is **fixed**: 4 actions per track (`press`/`stop`/`undo`/`clear`) plus `g.tap`/`g.stopAll`/`g.metro` (`ui/app.js:1147`). You map those targets; you cannot give a button a different function |
| single press / hold / double-click | ✅ per note, three gestures |
| up to 16 assignable targets | ✅ 23 at five tracks, and it scales with the track count |
| secondary control layer via Undo/Redo | ❌ obscure hardware behaviour, needs definition → §5 D2 |

#### 4. Audio Mixer & Routing

| requirement | status |
| --- | --- |
| independent input mixer | ✅ `audio.inputGain[]` per channel |
| ≥2 mic + 2 stereo line pairs | ✅ `audio.in` up to 16, per-track input selector (stereo sum / ch n / test bus) |
| independent input channel volume | ✅ |
| 2 dedicated mic compressors | ⚠️ one compressor **per track**, not per input channel |
| independent EQ for all input channels | ⚠️ HP/LP per track, not per channel → M6 |
| 3 stereo output buses | ✅ A / B / C |
| output mixer, independent volumes | ✅ `g.busA/busB/busC` |
| flexible routing of tracks to outputs | ✅ per-track route |
| master reverb + compressor | ✅ `FreeVerb2` + `Compander` limiter |
| dedicated headphone monitoring mix | ✅ the cue bus: `cueOn` / `cueSend` per track → output C |
| monitor tracks and rhythm independently | ⚠️ tracks ✅ through the cue bus (`cueOn`/`cueSend` → output C, `:343`); the **rhythm has no cue send**, and the dry-input monitor is **one global level** (`loop.monitor`, `:533`), not per track → M6 |

#### 5. Rhythm Engine

| requirement | status |
| --- | --- |
| ≥200 patterns | ✅ 200, generated from rows (`:553`) |
| ≥16 kits | ✅ 16, parameter sets — no samples, no licence problem |
| sync with loop tracks | ✅ same transport |
| playback alongside loops | ✅ own bus |
| import custom patterns | ❌ needs a file format decision → §5 D3 |

#### 6. Control & MIDI

✅ all of it: notes and CC, learn per target, press/hold/double gestures,
footswitch and expression pedal (CC is continuous), and the map persisted in
`ui/config.json` under `midi.map`.

#### 7. Memories / Presets

⚠️ 99 slots save and load the whole session (`session.slot`, `smoke.py` covers
slot 99), but there is **no browser**, and "quick switching" is the actual
requirement: M6.

#### 8. Import, Export & DAW

✅ WAV import, WAV export, zip backup/restore, multichannel in and out.
⚠️ "DAW integration" is Link (M5). ❌ Custom rhythm import is the same question
as §5.

#### 9. Audio Processing

✅ 32-bit float at both ends: SuperCollider's buses are float and the export is
written `buf.write(f, "WAV", "float")` (`:1307`). ✅ Five tracks stay in sync
because they read one shared `Phasor`. ✅ Changing speed does not lose the grid.

#### 10. Customization

⚠️ Partial: the MIDI map plus `ui/config.json` cover most of it, but there is no
single "complete configuration" save/load. Folded into M6's phrase browser.

**The issue's own acceptance criteria** map as: 5 tracks ✅ · independent
volume/playback/FX ✅ · 4 FX + 4 banks ✅ (VST) · 3 overdub modes ⚠️ (D1) ·
Undo/Redo/Mark Back/Bounce ✅ (M1, M3) · speeds ✅ · 200 patterns + 16 kits ✅ ·
memory stores the session ✅ · MIDI mapping ✅ · input/track/output routing ✅ ·
WAV import/export and backup ✅.

---

## 2. What is already known — measured, not assumed

### The config carry-over — done, uncommitted

`stage()` keeps `ui/config.json` across an update, which is right. It was keeping
a file written by an *older* build: a 0.1.0 config has no `rhythm`, no
`vst.banks`, no `loop.overdubMode`. Nothing crashed only because every reader
happens to be defensive (`? default`, `?.`) — a guarantee nobody maintains.
`migrate_config(path, defaults)` now fills the gaps from the shipped default at
the update, user values winning; `load_config` falls back to `config.json.bak`
instead of refusing to start. 64/64 passes. **M0 lands it.**

### #5 — three mechanisms, and one is a real invariant violation

The layer model has three things that must agree, and today they can disagree:

- `t[\bufs][i]` — the content
- `t[\gains][i]` — whether it is audible
- `t[\layerCount]` / `t[\undone]` — what UNDO and REDO will do next

1. **`~startRec` zeroes a layer buffer and does not clear `undone`** (`:733`).
   Record, undo, record again: the second take zeroes `bufs[li]` while `undone`
   still counts the layers above it, so a later REDO restores a gain for a buffer
   that was just erased. `~redo` (`:877`) sets `gains[li] = 1` without ever
   asking whether that buffer has content.
2. **REPLACE mode mutes everything below the new take** (`~commit`, `:775`). One
   silent take in that mode silences the whole track, and `~undo` (`:868`) brings
   it back — exactly the shape of the report. The toggle is user-facing
   (`OVERDUB REPLACES`, `docs/CONTROLS.md:160`), so it can be on without the
   player remembering.
3. **`~dubPass` decays every layer of every track on every wrap** (`:944`),
   including the layer being recorded and the tracks nobody is touching. With
   `dubDecay` at 0 it is a no-op; with any value it fades material the player did
   not ask to fade — and it fades it *evenly across the instrument*, which is not
   what "dub" means.

A fourth, cheaper suspicion worth ruling out first: a session or autosave load
sets `t[\layerCount]` from JSON (`:1223`, `:1264`); a load that restores a count
the buffers cannot back would make the next `~startRec` zero a layer that holds
the take.

The fix is not "find the bug" — it is **make the three agree**, then prove it.

### #1 — detection works; the device is the unknown

`app/main.py:764` watches for suspend: `CLOCK_MONOTONIC` stops during a sleep and
`CLOCK_REALTIME` does not, so a drift over 5 s triggers
`engine.restart(delay=4.0)`. `LOOPER_OSS_RESUME_SIM=600` exercises that branch
without suspending, and AGENTS.md requires the log to show `resume detected` and
`engine ready` again.

That is the *detection*. What the simulation cannot show is the part that fails on
a real lid close: **whether the audio device comes back.** A USB interface that
re-enumerates under a different node name, or a PipeWire graph that takes longer
than 4 s to rebuild, leaves `scsynth` running against a device it no longer has —
process alive, meters moving, no sound, and no watchdog can see it. Two concrete
things to check when reproducing: what `audio.device` in the config actually
selects (a name, an index, or `null` for default), and whether `scsynth` reports
the same device after the restart as before.

### #6 — the hard part exists

`\ld_bounce` (`:374`) already sums up to eight buffers with per-buffer gains into
a destination over one loop length, and `~bounce` (`:895`) already frees the
sources and rebuilds the destination synth. What is missing is the *selection*:
today it is "every track into one", and there is no way to flatten the four
layers of a single track into one.

One thing to check rather than assume: `\ld_bounce` writes `dst` while reading
`b0..b7`, and when the destination track is also a source, the same buffer is
read and written at the same index. It is safe *today* because the read is a
dependency of the write, but a layer merge makes that overlap the normal case, so
M3 either proves it with a probe or sums into a spare buffer and swaps.

### #7 — the master chain has a filter, but not a performance control

`\ld_master` (`:397`) has `masterHp`/`masterLp`, applied **before** the
`Compander` limiter, and they are exposed as two steppers in the settings drawer
(`ui/app.js:1071`). Setup control, not performance: two taps deep, no knob, no
resonance (`rq` fixed at 0.707), cannot sweep.

Three facts that decide the design, from the UGens' own documentation:

- `BLowPass`/`BHiPass` `rq` is **1/Q**. The help file warns: *"frequency values
  close to 0 may cause glitches and/or extremely loud audio artifacts"* — so the
  sweep must clamp, and the clamp is not optional.
- Those biquads *"update coefficients only when a parameter changes… this can
  cause spikes in CPU performance"*. A knob that sweeps every block is exactly
  that case, which is why the first choice should be **`MoogFF`** (`ar(in, freq,
  gain)`, `gain` 0–4 as resonance — a state-variable design, made for sweeping)
  and `BLowPass`/`BHiPass` only as a fallback.
- The existing clamps are already the house style: HP `20..2000`, LP
  `1000..19000`, `rq` 0.707 — the sweep needs wider ranges but the same
  discipline.

Also: `~grpMaster = Group.after(~grpTracks)` (`:631`), so master FX sit strictly
downstream of every write head. **A master effect cannot corrupt loop content** —
worth stating in `AUDIO.md`, because that is the fear a player brings to it.

And one gap in the UI primitives: `Knob` (`ui/controls.js:78`) takes
`min`/`max`/`value`/`curve` and has **no centre detent and no reset gesture**. A
bipolar performance knob needs both, so M4 adds them to the primitive, not to one
control.

### #4 — `LinkClock` is already here, and its rules are not obvious

Verified on this machine:

```text
$ sclang /tmp/lkprobe.scd
link class: LinkClock
tempo now: 126.0
```

SuperCollider 3.14.1 ships the Ableton Link extension
(`SCClassLibrary/External/Ableton/LinkClock.sc`, `HelpSource/Classes/LinkClock.schelp`)
with the primitives compiled in. So Link is **library-first compliant**: nothing
to download, nothing to vendor, and it is local multicast — not the internet, so
the offline rule is untouched.

The parts of `LinkClock.schelp` that change the implementation:

- **`tempo` is beats per second, not BPM.** The help file's own GUI example reads
  `tempoBox.value = l.tempo * 60`. Getting this wrong makes the looper run 60×
  fast or slow, and it is the single most likely mistake in this milestone.
- **Latency must match the server.** *"To coordinate with other applications, the
  LinkClock must run earlier than the desired sounding time, by the same fixed
  offset"* — `l = LinkClock(1).latency_(Server.default.latency)`. With a positive
  latency `l.beats` reads *later* than other apps; that is normal, and ignoring it
  makes the loop land late by the server latency.
- **Quantum is the phase reference, and it is set before the performance.**
  *"It is risky to change quantum during a performance… If you change the quantum,
  Link does not guarantee that beats will keep the same duration."* Set it once,
  at enable, to `bars × beatsPerBar` (16 for 4 bars of 4), and never move it.
- **`beats` can be negative on join.** The help file's example has a new peer
  starting at beat −3 so that barlines line up. Phase math must use a floored
  modulo and be unit-tested on negatives.
- **Peer count is free.** `numPeers` plus a `\numPeers` notification is exactly
  the "is anyone actually there?" status the UI needs.
- **Link does not stop or start anything.** SuperCollider clocks never stop; a DAW
  locks into our phase. So the looper stays the transport, and Link only supplies
  tempo and phase.

Architecturally that matters: this engine does **not** run on a `TempoClock` at
all — the grid is a server-side `Phasor` on `phaseBus` (`:179`) and `~applyRate`
(`:154`) derives its rate from `bpm`. So `LinkClock` is used as a **reference
clock**, polled in sclang and pushed through the existing state path, not as the
scheduler. Do not build a `Routine`-on-`LinkClock` transport; there is nothing for
it to drive.

### #3 — `FEATURES.md` has drifted

Five rows contradict the code, which makes the gap look bigger in some places and
smaller in one:

| row | says | actually |
| --- | --- | --- |
| §2 input compressor | ⬜ | ✅ `compOn` / `compThr` / `compRatio` in `\ld_track` |
| §2 threshold auto-record | ✅ "(arm per track)" | ❌ the detector synth exists, but the `arm` switch is never set — nothing arms it. M6 makes it real |
| §3 VST insert chain | ✅ 3 slots | ✅ **four** slots |
| §5 import a WAV as a layer | ⬜ | ✅ `smoke.py` imports one |
| §7 Windows build | ⛔ recipe only | ✅ 0.3.0 ships it |

---

## 3. Order, and why

1. **M0** lands the config fix and the playhead fix. Both are small, both are
   already diagnosed, and both are housekeeping: the tree should start clean so
   this round's own changes are distinguishable from what was already in it, and
   an archived plan that no longer describes the tree is a small lie worth paying
   off first.
2. **M1 (#5)** — the only issue that *destroys the player's work*. Everything else
   can wait a release; this cannot.
3. **M2 (#1)** — silence with no error: the failure a player cannot diagnose and
   cannot work around.
4. **M3 (#6)** — small, reuses `\ld_bounce`, and makes the layer model honest
   before M4 adds a second way to touch the master.
5. **M4 (#7)** — the first new surface. It comes after the layer work because a
   performance filter is only useful on material that stays where you put it.
6. **M5 (#4)** — integration with an external peer. It wants a stable transport,
   which M1–M4 leave alone.
7. **M6 (#3)** — breadth, last: the only milestone that can be cut without leaving
   the instrument broken.
8. **M7** — release.

---

## 4. The work — milestones M0–M7

Each milestone names what it is for, what is already known about it, the work,
the **evidence to record**, and what "done" means. The evidence line is the
important one: it is the difference between a milestone and an intention.

## M0 — clear the deck: the config carry-over, and the playhead that hides its ruler

**Why.** Two small, already-diagnosed things stand between the tree and the real
work. The first is finished and uncommitted. The second is the one defect every
player sees all the time: the loop ruler is unreadable exactly where it matters.

### M0a — land the config carry-over

**Work.** One Conventional Commit (`fix(app): complete a carried-over config
instead of trusting it`) with `app/main.py`, `scripts/smoke.py`, `AGENTS.md`,
`README.md`, `CHANGELOG.md`. The archive move (`goal.md` → `docs/goal-0.2.md`) and
`README.md`'s three references are already done as part of writing this plan.

**Done when.** `git status` clean · `./scripts/smoke.py` 64/64 ·
`python3 scripts/sweep.py` leaves nothing.

### M0b — the bar ruler is painted underneath the playhead

**The bug.** The ruler marks are children of the playhead bar:

```html
<div class="playhead">      <!-- 0.22rem tall, black, overflow hidden -->
  <div class="ticks"></div> <!-- the beat and bar marks -->
  <i></i>                   <!-- the accent fill, width = loop position -->
</div>
```

`.playhead .ticks` is `position: absolute` and `.playhead > i` is
`position: relative`, and **neither sets `z-index`** (`ui/style.css:135`, `:146`).
Among positioned siblings with `z-index: auto`, paint order is DOM order, so the
accent fill paints **over** every beat and bar mark it has passed. The ruler is
therefore visible only ahead of the playhead — the part you cannot read yet — and
it is erased behind it, which is the half that tells you where you are.

**Work.** Put the marks in front of the fill: an explicit `z-index` on both, not
one, so the order is stated rather than inherited from markup order. Then look at
the result: the bar is 0.22rem tall and the beat marks are 1 px at 30 % alpha, so
if the snapshot still cannot resolve them the answer is a contrast or height
bump — a separate, deliberate decision, not a quiet redesign.

**Evidence to record.** A WebKit snapshot (AGENTS §8: `scripts/shots.py`, **not**
Chromium — text and thin marks rasterise wrong in a GPU-less container) with the
loop at roughly 50 %, showing the beat and bar marks in front of the fill.

**Regression check.** In `scripts/uicheck.mjs`, compare the *computed* `z-index`
of `.playhead .ticks` against `.playhead > i` and require the marks to be on top —
that is the exact defect, and it cannot come back quietly. Also confirm the ticks
still follow a `bars` change (1 → 8) rather than being frozen at the default.

**Done when.** `uicheck.mjs` says `UI OK` including the new check, and the
snapshot shows the ruler across the filled part as well as ahead of it.

**Docs.** `CHANGELOG.md` (`fix(ui): draw the bar ruler above the playhead fill`).
Nothing in `CONTROLS.md`: this changes how a control looks, not what it does.

---

## M1 — #5: the layer model must not lose sound

**Why.** The only issue that destroys work. A player who loses a take does not
file a second issue, they stop using the app.

**Work.**

1. **Reproduce before touching anything.** A scripted sequence over the existing
   OSC API, asserting with `~probeTrack` (per-layer peak and gain) after every
   step:
   - record → undo → record → redo: does layer 0 still have its content?
   - record → **silent** overdub with `overdubMode = 1`: is layer 0 still audible?
   - two layers → `dubDecay = 0.3` → three wraps: what is left, and of *which
     track*?
   - load a session with a high `layerCount`, then record: which buffer is zeroed?

   Write it as a smoke check that **fails on today's code**. That failing check is
   the deliverable of this step, not a fix.

   **If all four pass**, the reported mechanism is not one of these and this plan
   is wrong — do not "fix" a bug that was not reproduced. Then log every
   `~startRec` / `~commit` / `~undo` / `~redo` / wrap with track and layer index,
   record one real take that loses sound, and read the log back. Write down what
   it shows before touching the model again.
2. **Make content, gain and undo agree.** One function that sets a layer's gain,
   one predicate for "does this layer have content", used by `~commit`, `~undo`,
   `~redo`, `~startRec`, `~setMark`. `~startRec` clears `undone` when it erases a
   layer; `~redo` refuses to restore a gain for an empty buffer.
3. **REPLACE mode cannot silence a track by accident.** A take that recorded
   nothing must not mute the layers below it: a silent commit is a no-op and the
   UI says so.
4. **`~dubPass` becomes per-track and skips the recording layer.** Dub decay is a
   property of the material being dubbed over, not of the instrument.
5. **Make the mode visible.** When `OVERDUB REPLACES` is on, the track header says
   so. A setting that can silence a track silently is a design defect on its own.

**Evidence to record.** The probe output for each of the four sequences, before
and after, and `scripts/wavcheck.py` on an export of "record, silent overdub,
play" showing the first take at its original level.

**Done when.** The step-1 check passes, the new checks pass, and the counts in
`AGENTS.md`/`README.md` match. 64 → ~69.

**Docs.** `CONTROLS.md` (REPLACE row + header badge) · `AUDIO.md` (dub decay is
per track) · `CHANGELOG.md`.

---

## M2 — #1: the audio must come back after a sleep

**Why.** The app looks alive and is silent. No error, no log line the player would
find, no workaround but restarting.

**Work.**

1. **Instrument the wake path.** Log, in order: the drift that triggered the
   restart, the engine's exit code, the device name `scsynth` reports *after* the
   restart, and the time from restart to `engine ready`. Today the log says
   "resume detected" and nothing about the device.
2. **Verify the device, do not assume it.** After `engine.restart()`, compare the
   device the engine reports with `audio.device`. Not the configured one → retry
   once with the default device and tell the UI instead of going quiet. The
   comparison needs no new plumbing: the bridge already reports the opened device
   as `links.inputs`/`links.outputs` in the existing `t: "devices"` frame — from
   `pw-dump` on Linux and from scsynth's own boot log on Windows
   (`opened_from_log`, `bridge/bridge.py:344`, called at `:222`). Add a `reason` to that frame only
   if it cannot already carry the answer.
3. **Give the graph time, and say why.** 4 s is a guess. Measure how long PipeWire
   takes to rebuild after a real suspend here, use the measurement, with a
   bounded retry rather than a longer sleep.
4. **Reproduce for real, once.** `LOOPER_OSS_RESUME_SIM=600` proves the branch;
   only a real lid close proves the device. Do both and quote both logs.

**Evidence to record.** Both logs (simulated and real), the measured rebuild time,
and the device name before and after.

**Done when.** The simulated resume logs `resume detected` → device name →
`engine ready`, and a real suspend on this machine does the same and plays
afterwards. If the real test cannot be run here, that is a **named blocker** in
the release notes, not a claim.

**Docs.** `AUDIO.md` (the wake path) · AGENTS.md invariant 22 (extend with the
device check) · `CHANGELOG.md`.

---

## M3 — #6: merge layers, and merge tracks by selection

**Why.** Four layers is the undo depth, but it is also how a drum part gets built:
kick, snare, hats, fill. Once it is right the player wants one loop, not four
layers that have to stay in step.

**Work.**

1. **MERGE LAYERS, per track.** Sum `b0..b3` with their gains into one layer,
   `layerCount = 1`. Reuse `\ld_bounce`. Because the destination is also a source,
   either prove the same-index read/write is safe with a probe, or sum into a
   spare buffer and swap — the swap is cheap and removes the argument.
   **It must be undoable**; a merge that cannot be undone would need tap-to-arm.
2. **MERGE TRACKS, by selection.** `~bounce` today is "every track into one".
   Extend to a source list and a destination, so two of five can be joined and the
   other three left alone.
3. **One command each on the wire**, so the UI *and* a MIDI pedal reach them:
   `svc mergeLayers [track]`, `svc mergeTracks [dst, srcs…]`. Add the learn rows.

**Evidence to record.** Two layers with known content → merge → the probe peak is
their sum, `layerCount` is 1, the sources are free, UNDO restores the layers, and
a `wavcheck` export matches.

**Done when.** Both checks pass and the merge is undoable.

**Docs.** `CONTROLS.md` (two rows) · `PROTOCOL.md` (two commands) · `AUDIO.md`
(why the sum is safe) · `CHANGELOG.md`.

---

## M4 — #7: performance FX on the master, and a DJ filter

**Why.** Everything on the master today is a setup control. A performer needs one
hand on a knob that does something musical without opening a drawer.

**Work.**

1. **A DJ filter as one bipolar knob.** Centre is bypass; left sweeps a low-pass
   down, right sweeps a high-pass up, with resonance. **`MoogFF` first**
   (`ar(in, freq, gain)`, resonance `gain` 0–4) because it is built for sweeping;
   `BLowPass`/`BHiPass` only if it falls short, and then with the frequency
   clamped away from 0 per their help file's warning. It sits where
   `masterHp`/`masterLp` sit: before the limiter, so resonance is caught.
2. **A real bypass at centre.** With the knob at 0 the output must be
   *bit-identical* to no filter at all — not "close". A filter that colours the
   sound when it is off is a bug report waiting to happen.
3. **Reconcile with the existing steppers.** Two controls for one filter is a
   future bug report. The steppers stay the *static* setting; the knob is a
   *performance offset* on top, and the UI shows both so the player can see why
   the sound is filtered.
4. **Two master FX slots with knobs.** Each slot picks one effect from a small
   curated list of **stock UGens only** — `MoogFF`/`BLowPass` (filter), `CombN` /
   `DelayN` (delay), `Shaper` / `Fold` (drive), `SinOsc`-modulated gain
   (tremolo) — two or three knobs each, live, on the main screen, persisted in
   `ui/config.json` under `master.fx`.
5. **The primitive needs two things it does not have.** `Knob`
   (`ui/controls.js:78`) has no centre detent and no reset gesture: add both, plus
   the ≥48 px tap target. A performance control that cannot be reset mid-song is
   a trap.

**Evidence to record.** Probe or `wavcheck` on an export for: knob hard left
(low-pass), hard right (high-pass), centre (bit-identical to bypass), and a full
sweep at the configured block size with the CPU number from `/ldstats` before and
during — the biquad-coefficient warning is a measurement, not a guess.

**Done when.** A smoke check covers both extremes and the centre-bypass identity,
a master FX slot survives a restart, `uicheck.mjs` says `UI OK`, and the tap-target
audit passes.

**Docs.** `CONTROLS.md` (new controls, ranges, defaults) · `AUDIO.md` (position in
the chain, why before the limiter, and that master FX cannot corrupt loop
content) · `PROTOCOL.md` (new fields) · `CHANGELOG.md`.

---

## M5 — #4: Ableton Link

**Why.** A looper that cannot follow the rest of the rig is a looper used alone.

**Work.**

1. **`engine/link.scd`, optional and probed.** Per AGENTS invariant 15, a missing
   class aborts the block that mentions it: probe `\LinkClock.asClass.notNil` and
   keep every reference in its own file, loaded only if the probe passes. The
   engine must boot identically with Link absent.
2. **Tempo in, with the unit trap handled.** `bpm = link.tempo * 60`; that drives
   `~state[\bpm]` and therefore `~applyRate`, so the grid moves, not just a number.
   A `\tempo` notification is the trigger, plus a slow poll as a fallback.
3. **Phase in.** `link = LinkClock(1).latency_(Server.default.latency)`, quantum
   set **once** at enable to `bars × beatsPerBar`, never mid-performance. Apply
   the offset to the transport as a wrapped phase offset so the read side sees an
   aligned grid. Floored modulo, unit-tested with negative `beats` (the join case).
   Document the trade-off: a hard jump is a click, a soft `LagUD` correction
   wavers — prefer soft and say so.
4. **A switch, a status, and a peer count.** `clock.link` in the config, a toggle
   in the settings drawer, `numPeers` in the UI (from the `\numPeers`
   notification) so "Link is on but nobody is there" is visible. Off means the
   internal clock with no residue — and with no peer at all the app behaves
   exactly as it does today.
5. **Do not build a scheduler.** `LinkClock` is a reference here, not the
   transport: the engine's grid is a server-side `Phasor`. No `Routine`-on-clock
   transport, and nothing pushed through `SendReply` — AGENTS invariants 2 and the
   Windows note say replies are not reliable; push through the existing state path.

**Evidence to record.** With a peer on the network: BPM follows a tempo change
within 100 ms; the loop stays phase-locked across a bar boundary; peer count
updates when a peer leaves. Without a peer: the looper keeps its own tempo.
Turning Link off returns to the internal clock with no jump.

**Done when.** The no-peer and toggle-off paths are smoke checks (a peer cannot be
assumed in CI) and the with-peer path is demonstrated manually and recorded.

**Docs.** `CONTROLS.md` · `PROTOCOL.md` (the link message) · `AUDIO.md` (phase
alignment and the latency offset) · `README.md` (how to try it) · `CHANGELOG.md`.

---

## M6 — #3: the parity subset, and making `FEATURES.md` true

**Why.** The issue is a manual. This milestone closes what is cheap on the model
that already exists, and reclassifies the rest in the open (§7), so the issue stops
reading like a bug list.

**Work, in this order — each its own commit and its own smoke check.**

1. **The small gaps first**, because they are what the matrix in §1 actually
   flags: **Auto Rec becomes reachable** (the detector synth already exists and is
   armed by an argument nothing sets — it needs a set key, an action, a learn row
   and a UI switch), **input EQ per channel** rather than only per track (HP/LP
   already exist per track; the change is where the settings live), and a
   **rhythm cue send** so the drums can be monitored without being in the mix.
2. **Per-layer mixer** — volume, mute and delete per layer. The model already has
   `gains[0..3]`; this is three args per layer plus a UI, no new audio, and it
   makes M1's invariant visible to the player.
3. **One-shot playback** — a track that plays once and stops. The transport
   already knows the wrap; this is a per-track flag.
4. **Track copy / swap / duplicate** — buffer-level, like `~bounce`, no new DSP.
5. **Phrase browser** — 99 memory slots already save and load. What is missing is
   the browser: list, name, load, delete, slot number visible, and **quick
   switching**, which is the actual requirement in issue #3 §7.
6. **`FEATURES.md` truth pass** — fix the four stale rows in §2, move everything
   this round ships to ✅, and put §7's declined list in the backlog section with
   its reason.
7. **Comment on issue #3** with the §1 matrix: what this round closed, what is
   deferred, and why. Then close it or retitle it as the roadmap it is.

**Decisions needed before step 1.** §5 D1 (the third overdub mode) and D3 (custom
rhythm import format). D2 (secondary control layer) can stay open and deferred.

**Done when.** Each of 1–5 has a smoke check, `FEATURES.md` has no row that
contradicts the code, and the issue comment exists.

---

## M7 — ship 0.4.0

**Work.** `VERSION` → 0.4.0. `CHANGELOG.md` with a section per issue **quoting the
evidence** — the probe numbers for #5, both resume logs for #1, the tempo
measurement for #4, the centre-bypass identity for #7. `README.md` for the new
controls. `./scripts/release.py --check`, then the archives. Rebuild the AppImage
and install it over 0.3.0 locally, so M0's config carry-over is exercised by a
real upgrade rather than by a test.

**Done when.** `./scripts/smoke.py` green at its new count · `uicheck.mjs` →
`UI OK` · `scripts/sweep.py` leaves nothing · `release.py --check` passes · the
tag is created only after telling the user.

---

## 5. Decisions needed from the user

Each has a recommended default so a run is never blocked.

| # | question | blocks | recommended |
| --- | --- | --- | --- |
| **D1** | Issue #3 names **3 overdub modes** and describes one (replace). We have two: layer-on-top and replace. What is the third? | M6 | Ship two, and say so in the issue reply — a mode nobody can name is not a requirement yet |
| **D2** | "Secondary control layer accessible through Undo/Redo" — the RC-505's A/B control layer. Do you want it? | M6 | Defer: it is a hardware-controller concept and the touchscreen has room for both layers at once |
| **D3** | "Import custom rhythm patterns" — a file format has to be chosen | M6 | Defer to 0.5.0 with the format question: MIDI file, or a text row like the generator uses |
| **D4** | #3 asks for **49 input FX and 53 track FX types**. Four VST slots already carry any effect that exists as a plugin. | M6 | Decline the count, keep the mechanism, and say it plainly in the issue reply |

---

## 6. Risks

| risk | where | mitigation |
| --- | --- | --- |
| #5 is not the bug the code suggests | M1 | step 1 is a *failing check*; if it passes, instrument and reproduce before changing anything |
| a merge in place smears the buffer | M3 | spare buffer + swap, or prove the read/write order with a probe |
| `rq`/`freq` glitch at the end of a sweep | M4 | clamp per the UGen's own warning; centre must be an exact bypass |
| biquad coefficient spikes during a sweep | M4 | `MoogFF` first; measure CPU; quantize the frequency update if needed |
| Link tempo unit (beats/s vs BPM) | M5 | the `* 60` is stated in §2 — test it against a peer, not by inspection |
| Link quantum changed mid-performance | M5 | set once at enable; never exposed as a live control |
| a real suspend cannot be tested here | M2 | named blocker in the release notes, never a claim |
| `FEATURES.md` and the issue drift apart again | M6 | the truth pass is a deliverable, and the issue comment is the receipt |
| `smoke.py` wipes `sessions/` | every milestone | back up before, restore after (AGENTS §8) |

---

## 7. Declined, with reasons

Stated here so the issue can be answered honestly instead of left open forever.

- **Time-stretch ("speed with or without pitch change").** Varispeed only, like the
  hardware's SPEED. Pitch-preserving stretch is exactly the hand-written DSP the
  house rules forbid; it is a 0.5.0 conversation about a library, not a milestone.
- **49 input FX / 53 track FX *types*.** The count is a content programme, not a
  feature. Four VST insert slots per track already carry any effect that exists as
  a plugin; inventing 100 built-ins would be an enormous surface with worse sound.
- **Harmony / Electric FX specifically.** Same: VST territory.
- **A master VST chain.** Per-track VST exists; a master slot is a later option and
  M4's built-in FX are the 505's model.
- **`MeterSync` / barline sync between SuperCollider peers.** `LinkClock` offers it,
  but there is one engine per instance and nothing to sync it with.
- **Rewriting the layer model.** M1 makes the existing one consistent; it does not
  replace it.

## 8. Non-goals for the round

- Any cloud, account, telemetry or internet dependency. Link is local multicast.
- Multi-user or networked sessions.
- A DAW-style arrangement view, MIDI sequencing, plugin delay compensation.
- Localisation, theming, phone remote — backlog, not this round.

## 9. Definition of done, whole round

- [ ] every open issue is either closed with evidence or has a comment saying what
      was deferred and why
- [ ] `./scripts/smoke.py` green, and its count in `AGENTS.md` and `README.md`
      updated to match
- [ ] `node scripts/uicheck.mjs` → `UI OK` (M0, M4 and M6 touch the UI)
- [ ] `python3 scripts/sweep.py` → nothing running, the user hears nothing
- [ ] `sessions/` backed up before every suite run and restored after
- [ ] `CONTROLS.md` / `PROTOCOL.md` / `AUDIO.md` updated in the same change as the
      behaviour they describe
- [ ] `CHANGELOG.md` has an entry per user-visible change
- [ ] no claim in the release notes that was not measured on this machine

---

## Paste this into `/goal`

```text
Implement goal.md (Looper OSS 0.4.0) milestone by milestone, M0 through M7, in
order. Read AGENTS.md first: its invariants are not suggestions, and §6.27 is the
config rule this round starts from.

M0  clear the deck: (a) land the uncommitted config carry-over as one commit,
    tree clean, 64/64; (b) fix the playhead painting over its own ruler — set an
    explicit z-index on BOTH .playhead .ticks and .playhead > i so the marks
    paint above the fill, add a uicheck.mjs check comparing their computed
    z-index, and prove it with a WebKit snapshot at ~50% loop position.
M1  #5 layer model: write the FAILING probe check first (record/undo/record/redo,
    a silent overdub in REPLACE mode, dubDecay across three wraps, and a session
    load followed by record). If it passes, stop and instrument instead of
    "fixing". Then make content, gain and undo agree; REPLACE cannot silence a
    track; dubPass is per-track and skips the recording layer; the mode is
    visible in the track header.
M2  #1 sleep/resume: log drift, exit code, the device actually reported after
    restart, and time to engine ready; verify the device and retry once with the
    default; prove it with LOOPER_OSS_RESUME_SIM=600 and once for real, quoting
    both logs.
M3  #6 merge: MERGE LAYERS per track (undoable, spare buffer + swap) and MERGE
    TRACKS by selection, both reusing \ld_bounce; one command each on the wire
    plus MIDI learn rows.
M4  #7 performance FX: a bipolar DJ filter (MoogFF first, gain as resonance,
    before the limiter, centre = exact bypass, frequency clamped per the UGen
    warning) reconciled with the existing HP/LP steppers, plus two master FX
    slots with live knobs persisted under master.fx. Add centre detent and
    reset to the Knob primitive. Measure CPU during a full sweep.
M5  #4 Ableton Link: engine/link.scd, optional and probed with
    \LinkClock.asClass.notNil; bpm = link.tempo * 60; latency_(s.latency);
    quantum set once at enable to bars*beatsPerBar and never changed; floored
    modulo for negative beats; phase offset with soft correction; a switch, a
    peer count, a clean no-peer fallback. Link is a reference clock, NOT a
    scheduler: no Routine transport, no SendReply.
M6  #3 parity subset, small gaps first: make Auto Rec reachable (the detector
    synth exists, the arm switch is never set — set key + action + learn row +
    UI), per-channel input EQ, rhythm cue send, then per-layer mixer, one-shot,
    track copy/swap, and a phrase browser with quick switching. Then make
    FEATURES.md true (five stale rows) and comment on #3 with the traceability
    matrix, the deferred list and the reasons.
M7  ship 0.4.0: VERSION, CHANGELOG quoting the measured evidence per issue,
    README, release check, AppImage installed over 0.3.0.

Rules:
- Measure before touching the audio path. A green suite is not evidence the
  instrument works.
- Every milestone ends with a check in scripts/smoke.py that would fail if the
  feature were removed. Update the counts in AGENTS.md and README.md.
- Back up sessions/ before a suite run and restore it after; finish every
  session with python3 scripts/sweep.py.
- A new state field means engine array + GLOBAL_FIELDS/TRACK_FIELDS + onState,
  all three, in the same change.
- Decisions D1 and D3 in §5 block M6; if the user has not answered, the
  recommended default is what to take. D2 and D4 are already defaulted to
  "defer" / "decline", so they do not block anything.
- If something cannot be verified on this machine, say so plainly and leave it a
  named blocker. Never claim a result you did not measure.
- Conventional Commits, subject line under 72 characters.
```
