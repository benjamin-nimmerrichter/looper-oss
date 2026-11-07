# AGENTS.md — working on Looper OSS

Read this first. It is the map, the commands, the contract between layers, and
the list of things that have already bitten us — so they do not bite you.

---

## 1. What this is

A touchscreen loop station for an 8" panel (1280×800), modelled on the Boss
RC-505 so the gestures transfer to the hardware. Three processes, **no
hand-written DSP**: everything audio is a stock SuperCollider / sc3-plugins UGen,
and VST3/VST2 via the `VSTPlugin` extension.

```text
browser (ui/)  ⇄ ws JSON ⇄  bridge (bridge/bridge.py)  ⇄ OSC ⇄  sclang (engine/)  ⇄  scsynth
   8181 static              8182 / 9000 / 9001                  engine/looper.scd     PipeWire / WASAPI
```

## 2. File map

| path | owns | change it when |
| --- | --- | --- |
| `ui/config.json` | **the only config** (tracks, layers, tempo, audio, UI scale, MIDI map, colours) | adding any setting |
| `engine/looper.scd` | SynthDefs, per-track state machine, session save/load, OSC API, metering, tempo | audio behaviour, state transitions, protocol fields |
| `engine/vst.scd` | VSTPlugin slots only. Loaded **only if** the extension exists | plugin chain, plugin persistence |
| `bridge/bridge.py` | OSC ⇄ JSON, settings persistence, audio-device enumeration, `/b_getn` fan-in | protocol, settings write-back, device lists |
| `bridge/write_config.py` | renders `config.json` → `engine/_config.scd` (used by both bridge and the launcher) | config schema changes |
| `ui/app.js` | every control, drawers, boot screen, playhead, keypad | UI behaviour |
| `ui/controls.js` | the touch primitives: Fader, Knob, Pad, Button, Segmented, Stepper, Select, Meter, `auditTapTargets` | gesture/size rules |
| `ui/style.css` | layout, `--scale`, `--tap` (48 px floor), grid | anything visual |
| `scripts/smoke.py` | 51 end-to-end checks, spawns its own engine | behaviour changes (update expectations!) |
| `scripts/uicheck.mjs` | 16 UI checks + screenshots (needs playwright) | UI layout changes |
| `scripts/sweep.py` | kills leftover `scsynth`/`sclang`/bridge/UI-server by scanning `/proc` | — |
| `scripts/release.py` | builds `dist/looper-oss-<ver>.tar.gz` + `.zip` + `SHA256SUMS`, reproducible; refuses to ship runtime state | what a release contains, versioning |
| `app/main.py` | **the application**: owns the UI server, the bridge thread, the engine process, the window, shutdown, single-instance | startup, lifecycle, packaging |
| `app/window.py` | GTK+WebKit2 window → Edge/Chromium `--app` → browser, in that order; also the "SuperCollider is missing" page | window behaviour |
| `install.sh` | one-time setup for a source check-out (venv, requirements, engine config, sclang check) | dependencies |
| `packaging/linux/` | AppImage recipe: bundles CPython + pinned deps, AppRun, `.desktop`, icon | what the Linux download contains |
| `packaging/windows/` | PyInstaller spec + Inno Setup script (per-user install, no admin) | what the Windows download contains |
| `requirements.txt` | **pinned** python deps of the bridge (`websockets`, `python-osc`) | dependency bumps |
| `VERSION` | the only version number; `release.py` reads it | any release |
| `LICENSE`, `docs/LICENSING.md`, `ACKNOWLEDGEMENTS.md` | GPL-3.0, the third-party audit, the credits | adding a dependency or copying a snippet |
| `CHANGELOG.md` | Keep-a-Changelog history | every user-visible change |

## 3. Documentation index — read the right one before editing

| question | file |
| --- | --- |
| how do I install / run / play it? | `README.md` |
| **what does this button do?** range, default, wire message, engine arg | `docs/CONTROLS.md` |
| **what is on the wire?** exact OSC/JSON field order | `docs/PROTOCOL.md` |
| **how does the audio graph work?** gain staging, seam fade, varispeed, latency, CPU | `docs/AUDIO.md` |
| what was planned, and what is left? | `goal.md`, `FEATURES.md` |

Rule of thumb: touching a **control** → `CONTROLS.md`; touching a **message** →
`PROTOCOL.md`; touching a **UGen or a level** → `AUDIO.md`. Update the doc in the
same change that changes the behaviour; they are generated from the code, not
aspirational.

## 4. Commands

```bash
./install.sh                      # venv + pinned requirements + generated engine config
python3 looper_oss.py             # the app: window, engine, everything
python3 looper_oss.py --kiosk     # fullscreen for the pedal (one-shot)
python3 -m app.main --window none # headless stack, for tests and probes
LOOPER_OSS_RESUME_SIM=600 python3 -m app.main --window none
                                  # pretend the machine slept 10 min: the log must
                                  # say `resume detected` and `engine ready` again

python3 scripts/sweep.py          # kill leftovers (do this after any test run)
./scripts/smoke.py                # 51 checks, spawns its own stack
./scripts/smoke.py --attach       # ...against a stack you started
PLAYWRIGHT_DIR=/tmp/uitest/node_modules node scripts/uicheck.mjs
sclang /tmp/probe.scd             # boots the engine alone (write your own probes)
./scripts/release.py --check      # required files present, no runtime state in the tree
./scripts/release.py              # build dist/ archives (byte-identical on rebuild)
```

**Always finish a test session with `python3 scripts/sweep.py`.** Leaving a
`scsynth` alive means the user hears your test signal, or the next start talks to
a server that no longer has the SynthDefs.

## 5. The contract between the three layers

`/ldstate` is a **flat positional** message. Three places must agree, in order:

1. `engine/looper.scd` → `~sendState` builds the array (23 globals, then 24 per track)
2. `bridge/bridge.py` → `GLOBAL_FIELDS` / `TRACK_FIELDS` name them by position
3. `ui/app.js` → `onState` reads those names

Change one, change all three, then run `smoke.py` (it asserts `23 / 24`). Per-track
messaging is generic on purpose: `/ld/set "t2.hp" 300` → `synth.set(\hp, 300)`, so
a new knob usually needs **no new OSC path** — just a field in the state array.

## 6. Invariants that already cost days

SuperCollider and its corners, in the order they bit us. Read before editing
`engine/` or `bridge/`.

1. **`SendReply` layout is `[cmdName, nodeID, replyID, v1, v2, …]`** — the first
   value is `msg[4]`, not `msg[3]`. Getting this wrong silently kills meters.
2. **Do not put `SendReply` inside the big SynthDefs.** From `\ld_track` /
   `\ld_transport` the replies arrived with 1–2 values instead of 4, at the wrong
   rate, or not at all. Metering lives in tiny `\ld_meter` / `\ld_global_meter`
   defs; anything that must reach sclang belongs in a small def.
3. **Meters are read through a buffer, not `SendReply`.** `\ld_meter` writes
   `BufWr.kr` into a 64-frame buffer; sclang polls `/b_getn` at 10 Hz and one
   persistent `/b_setn` responder stores it. Do not use `Buffer:getToFloatArray`
   for this — it arms a per-call timeout and floods `streamToFloatArray failed`.
4. **`BufWr`/`BufRd` buffer numbers must be fixed at Synth creation.** One writer
   per layer, bound to its own buffer; "recording into layer N" is a *gate*
   (`w0..w3`), never a buffer switch. An idle layer writes its own content back
   with **interpolation 1** (exact) — interpolation 2 blurs the buffer every pass
   and sounds like a screech.
5. **`Synth(def, args, group)` defaults to `\addToHead`.** Pass `\addToTail`, and
   give the master its own group *after* the tracks (`~grpMaster`), or the master
   sums a bus the tracks have not written yet.
6. **`Buffer:copyData(buf, dstStartAt, srcStartAt, numSamples)`** — arg order, not
   the obvious one. Wrong order copies **zero** samples, silently.
7. **`Buffer:getToFloatArray(index, count, wait, timeout, action)`** — the action
   is the **5th** argument.
8. **No `^` (non-local return) in OSC/MIDI responders or Routines.** It unwinds
   the responder's thread and the reply vanishes without an error.
9. **sclang needs every `var` at the top of a block**, before any statement.
   A `var` mid-block is a syntax error far from the real cause.
10. **`Changed.kr` requires a control-rate input** (it is `HPZ1.kr`): convert with
    `A2K.kr(arSignal)` first, and `K2A.ar(krSignal)` to go back up.
11. **No varargs.** `{ |...| }` is invalid sclang, and only **one** `*splat` per
    call (`sendMsg(path, *a, *b)` is a syntax error — build the array first).
12. **`MIDIClient.sources`**, not `MIDIIn.sources` (which does not exist in 3.14 and
    aborts the whole block that mentions it).
13. **`VSTPluginController:open(path, editor, verbose, action)`** — `action` is a
    *named* argument and is called as `action.value(ctrl, ok)`.
14. **SynthDefs load asynchronously** (`/d_recv` may be a temp file): `s.sync`
    after `~addDefs` before creating any synth.
15. **A missing class aborts the block that mentions it.** Optional extensions must
    be probed with `\ClassName.asClass.notNil` and kept in their own file
    (`engine/vst.scd`) — never referenced from the main engine.
16. **A recovered session never auto-plays.** Autosave restores layers with
    `playing = 0`, `state = 3`: opening the app must not blast the last session.
    An explicit slot load (`tag >= 0`) does start playing.
17. **Clearing a track deletes its autosave** — but a *load* must not, or it
    deletes the files it is reading (`~loading` guards this).
18. **A restart must release the audio device first.** `s.quit` → wait 2.5 s →
    `42.exit`; the launcher then waits for the process to disappear. Otherwise the new
    `scsynth` dies with `could not initialize audio` and the engine half-boots.
19. **A click at the loop point is a *discontinuity*, so its size equals the
    signal's amplitude at the cut.** It scales with the input and disappears on
    silence — that is the signature. Never diagnose it with peak/RMS; use ratios
    against the body of the take (`scripts/wavcheck.py`: hole, HF, and the step the
    audio makes on its way out). Three separate cuts have been found this way: an
    unwritten final frame, a mid-pass punch-out with no fade, and a stale tail
    beyond the loop length after a load.
20. **`BufRd`'s `loop` flag wraps at the buffer size, not at the loop length.**
    A layer buffer is `maxSeconds` long but a loop is `contentLen`, so a fractional
    position just under `contentLen` used to interpolate against `buffer[contentLen]`
    -- a zero -- instead of `buffer[0]`. That is a click at the seam, proportional to
    the material's amplitude. `~lerpRead` reads floor/ceil with the index taken
    modulo `contentLen`, and there is a unit check for it: 8-frame loop, `b[7]=b[0]=1`,
    read at 7.5 must return 1 (not 0.5).
21. **A half-booted engine must not throw on every command.** Command handlers are
    no-ops until `~tracks` exists; the boot watchdog retries twice, then reports
    `could not open the audio device` to the UI.
22. **Suspend kills the audio, not the process.** After a sleep the device is
    torn down and rebuilt, but `scsynth` keeps running against the dead node: no
    exit code, meters still moving, no sound. An exit-code watchdog cannot see
    it. `app/main.py` compares `CLOCK_REALTIME` drift against `CLOCK_MONOTONIC`
    (monotonic stops during suspend) and restarts the engine on >5 s. The session
    comes back **stopped** from autosave — that is intended, not a bug.

## 7. Change checklist

Before you call a change done:

- [ ] `sclang /tmp/probe.scd` → `0 ERROR`, and the log has no `streamToFloatArray`
- [ ] `./scripts/smoke.py` → 51/51 (update the expectations if the behaviour changed
      *on purpose* — see §8)
- [ ] `node scripts/uicheck.mjs` → `UI OK` if you touched the UI
- [ ] `python3 scripts/sweep.py` → no processes left, user hears nothing
- [ ] the doc that describes it is updated (CONTROLS / PROTOCOL / AUDIO)
- [ ] if you added a state field: engine array + `GLOBAL_FIELDS`/`TRACK_FIELDS` + `onState`

## 8. Testing rules learned the hard way

- Tests must start from a **clean `sessions/`** — an autosave from a previous run
  restores layers and every layer count is off by one. `smoke.py` wipes it.
- Keep expectations **relative** (`layers > base`) rather than absolute; `autoDub`
  changes how many layers exist after the first pass.
- Read **fresh** frames. Buffered websocket frames from before an action will make
  a working feature look broken (tap tempo "failed" this way twice).
- Never `pkill -f <pattern>` when your own shell command line contains that
  pattern — it kills the caller. Use `scripts/sweep.py`, or match on `/proc`.
- A capture buffer must be longer than what you record into it, or it wraps and
  the overwrite looks exactly like the click you were measuring.
- When measuring a click or a level, use a **separate** synth added with
  `\addToTail`, not the same synth (UGen order inside one def is not your source order).

## 9. House rules

- **Library-first.** No new DSP if a UGen exists. If a feature needs maths, find
  the UGen or cut the feature.
- **One config file.** `ui/config.json`; `engine/_config.scd` is generated, never edited.
- **No build step for the UI.** Plain ES modules, no bundler, no runtime network.
- **Touch first.** Every interactive target ≥ 48 px (`--tap`), no hover-only
  affordances, long-press for destructive actions.
- **Offline.** No telemetry, no CDN, no cloud.

## 10. Current limitations (deliberate, documented)

- Loop length comes from **BARS + BPM**, not from the first take (no AUTO mode).
- **4 layers** per track (undo depth) instead of the RC-505's single loop + Mark Back.
- No drum **rhythm patterns** — metronome only.
- **No time-stretch**: a tempo change re-pitches (varispeed), like SPEED CHANGE.
- VST editor is the plugin's own window, or VSTPlugin's generic parameter UI when
  the plugin ships none.
- **Windows is unverified** (the PyInstaller + Inno Setup recipe ships; no Windows host was available to boot
  WASAPI). Everything else is verified on CachyOS + PipeWire.
- Meters/latency: PipeWire quantum governs round-trip (~21 ms at 1024, ~6.7 ms at
  256); `loop.recordOffset` compensates the grid alignment.
