# Looper OSS 0.3.0 — Windows implementation and the seam

Paste the block at the bottom into `/goal`. Above it: what is actually known,
the full Windows work breakdown, and why each milestone ends the way it does.

## Status of the metallic buzz: unverified

0.2.0 shipped with one change aimed at it (`2639b83`): the layer being recorded
is no longer played back. The reasoning is sound — the write head is derived
from the read head, so during a take the play path read the very sample being
written — but **nobody has listened since**. It may be gone, reduced, or
unchanged.

So 0.3.0 starts by finding out. Two ways, cheapest first:

1. Play it. Record take 1, let it roll into the dub, listen at the seam. That is
   the only definitive test of "does it still sound wrong".
2. Measure it. Same take with a 220 Hz tone, export, read the seam with
   `scripts/wavcheck.py`: the step across the loop point, and the 5–15 kHz
   energy inside the ~100 ms window at the hand-off, layer 1 alone and the mix.

Do both: ears decide *whether*, the measurement decides *whether the fix
worked*. A number without ears can be green while it still sounds wrong.

## What "Windows support" actually means here

Not "the packaging recipe builds". It means: on a Windows 10 machine with
SuperCollider installed, the app opens, plays, records, overdubs, exports, and
survives a restart — and we have run it there.

### The work breakdown

**W1 — engine discovery.** `Server.program` must point at the `scsynth.exe` that
sits beside the `sclang.exe` we found. Derive it, do not hard-code. Same for the
`scsynth.exe` argument string in `engine/looper.scd`'s boot path.
Acceptance: `scsynth.exe` appears in Wine's process list and the log shows it
opening a device.

**W2 — engine environment.** SuperCollider's compiled-SynthDef cache
(`%LOCALAPPDATA%\SuperCollider\synthdefs`) does not exist on a fresh install;
create it before boot or every boot prints an error. Also check the UTF-8
codepage warning against non-ASCII paths (this repo lives under
`Dokumenty/`-style folders on some machines).
Acceptance: a boot log with **0 ERROR**.

**W3 — process lifecycle.** `Engine.restart()` does `s.quit → 2.5 s → 42.exit`
on Linux; Windows needs the equivalent that actually releases the WASAPI device
(`taskkill /F /PID`), plus `sweep_stale()` exercised rather than assumed. A
restart must not leave a half-dead `scsynth.exe` holding the interface — the
same failure AGENTS invariant 19 and 22 describe, on a different OS.
Acceptance: kill the app mid-playback, relaunch, audio comes back; do it twice.

**W4 — paths and files.** Everything that touches the filesystem: home
(`%APPDATA%`), logs, `sessions/`, `ui/config.json` writing, backup/restore zips,
WAV import, `os.replace` semantics, long paths, spaces in paths, and
`write_config.py`'s output. The Linux code uses `pathlib` almost everywhere,
which is why this is expected to be small — but expected is not verified.
Acceptance: save a session, back it up, restore it, import a WAV, all from the
installed app, and read the files back on the Linux side.

**W5 — audio device selection.** Do not hard-code a device index. On Windows the
default endpoint changes with whatever is plugged in, and SC's WASAPI host lists
endpoints by name. The config needs a sane default (system default), and the
setup drawer must show what was actually opened.
Acceptance: the app opens the default device; unplugging the interface and
relaunching still opens a device (or says clearly that it could not).

**W6 — MIDI.** `MIDIClient.sources` must enumerate on Windows (invariant 12 —
not `MIDIIn.sources`), the learned gestures must fire, and the MIDI map CSV must
round-trip.
Acceptance: a pad lights from a MIDI note on Windows.

**W7 — VST3.** Slot paths and plugin folder differ
(`C:\Program Files\Common Files\VST3`), and `VSTPlugin` needs its server-side
binary for Windows. This is the most likely thing to be *absent*, so it must
degrade: missing extension = setup drawer hides the FX tab, engine boots anyway
(invariant 15).
Acceptance: engine boots with and without the extension present.

**W8 — packaging.** PyInstaller `--onedir` (not onefile), pinned to Python 3.11
and PyInstaller 6.x; Inno Setup 6 per-user install into `%LOCALAPPDATA%`; the
installer detects a missing SuperCollider and says so with a link, rather than
crashing at first boot. SuperCollider is **not bundled** — GPL binaries stay on
the user's machine, and we avoid shipping another audio stack.
Acceptance: install on a clean Win10 user account, launch from the Start menu,
and a SuperCollider-less account gets a clear message instead of a crash.

**W9 — the seam (platform-independent).** One recording buffer and one playback
buffer per layer, swapped at the loop point, mixed only when it is safe — so no
synth ever reads a buffer it writes in the same block. This replaces the
workaround in `2639b83` with the design the user asked for.
Acceptance: measured seam energy below the pre-change baseline, no step at the
loop point, 63/63 on Linux, and the take's opening no longer eaten.

### Order, and why

W1 → W2 get a boot. W3 → W5 get it to *make sound and keep making it*. W9 is
platform-independent but must be measured on Linux first, because Linux is where
we have a working audio path and a 63-check suite to catch regressions. W6 → W8
are breadth. M4 (real hardware) gates the release.

## Constraints

- **Windows 10 is the floor.** Python 3.11, SuperCollider 3.13, PyInstaller 6.x,
  Inno Setup 6. Nothing Win11-only, no Store, no winget.
- **Wine is a harness, never evidence.** It exposes MME only. It can prove
  plumbing and enumeration; it cannot prove WASAPI latency or how it sounds.
- **Measure before touching the audio path.** The last two blind attempts at the
  write gate cost DUB and STOP ALL.
- **No release until it runs on the Win10 laptop**, and Linux still passes
  63/63 after W9.

---

## Goal text

```text
Looper OSS 0.3.0: Windows 10 support, and the loop seam fixed for real.

Work in dev, commit locally, push only when I say. Release 0.3.0 only when every
acceptance check has been run for real, and never claim Windows works from a
Wine result — Wine has MME only.

M0 — does the metallic buzz still exist?
  0.2.0 silenced the layer being recorded (2639b83), which should have removed
  the read-while-write overlap, but nobody has listened since. Record take 1,
  roll into the dub, and listen. Then record the same with a 220 Hz tone,
  export, and measure with scripts/wavcheck.py: step across the loop point and
  5-15 kHz energy in the ~100 ms window at the hand-off, layer 1 and the mix.
  Write both answers into goal.md: does it still sound wrong, and what are the
  numbers. Do not proceed to M3 without these.

M1 — the app boots end to end under Wine
  Set Server.program from sclang's own directory so scsynth.exe is found; create
  SuperCollider's synthdefs cache directory before boot. Done when, under
  ~/.wine-looper, `SCLANG='C:\SC\SuperCollider\sclang.exe' wine python.exe -u -m
  app.main --window none` serves :8181, sclang.exe boots engine/looper.scd with
  0 ERROR, and scsynth.exe opens a device (name it from the log).

M2 — our code paths work on Windows, not just boot
  Exercise the real protocol under Wine: /ldstate arrives, meters move, a track
  records and plays, export writes a WAV with audio in it. Fix every POSIX
  assumption found (paths, /proc, pkill, signals, locking, non-ASCII).
  Done when a scripted pass under Wine reports the same state contract as Linux
  (26 globals / 32 track fields) and an exported WAV measures above zero.

M3 — replace the buffer scheme, measured
  One recording buffer and one playback buffer per layer, swapped at the loop
  point, mixed only when it is safe: no synth ever reads a buffer it writes in
  the same block. Done when the M0 measurement repeated afterwards shows 5-15
  kHz seam energy below the M0 baseline, no step at the loop point, the take's
  opening intact, and 63/63 on Linux.

M4 — process lifecycle and devices on Windows
  s.quit -> taskkill /F with the device actually released; sweep_stale exercised
  not assumed; default audio device chosen by name, never a hard-coded index.
  Done when, on Wine, killing and relaunching twice brings audio back each time,
  and the app opens the default device and says which one.

M5 — breadth: files, MIDI, VST3
  Save/backup/restore/import round-trip; MIDIClient.sources enumerates and a
  learned gesture fires; VSTPlugin degrades cleanly when the extension is absent.
  Done when each is either working or named as a blocker with the exact error.

M6 — 0.3.0 runs on real Windows 10
  PyInstaller onedir + Inno Setup per-user installer; SuperCollider 3.13 is a
  documented prerequisite, not bundled; a machine without it gets a clear
  message, not a crash.
  Done when, on my Win10 laptop: it installs, opens, records a loop, overdubs,
  exports an audible WAV, and survives a relaunch. Report the WASAPI device name
  and the round-trip latency SC reports.

M7 — ship 0.3.0
  VERSION 0.3.0; CHANGELOG stating Windows 10 support with SuperCollider 3.13
  installed and the seam fix with its measured numbers; README Windows install
  path; docs/AUDIO.md updated for the buffer pair; AppImage rebuilt and
  installed over 0.1.0 locally.
  Done when 63/63 passes on Linux, UI check says UI OK, scripts/sweep.py leaves
  nothing running, and I have tagged v0.3.0 after telling you.

Rules:
- Measure before touching the audio path. A green suite is not evidence the
  instrument works.
- Never claim Windows works from Wine. Wine has MME only.
- If it cannot be verified here, say so plainly and leave it a named blocker.
- Conventional Commits, subject line under 72 characters.
```
