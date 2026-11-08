# Looper OSS 0.3.0 — Windows support and the seam

Paste the block at the bottom into `/goal`. Everything above it is the reasoning
behind it, so the milestones are not arbitrary.

## Why these two things together

0.2.0 shipped with two known audio defects, documented in `CHANGELOG.md`:

- a **metallic buzz (~10 kHz, ~100 ms)** at the auto-dub hand-off
- **the first take losing its opening ~100 ms** when it rolls into an overdub

Both live in the same place: a layer buffer is read for playback while it is
also being written. The write head is derived from the read head
(`wpos = ((rpos - recordOffset) % contentLen)`), so during a take the play path
reads the very sample being written. In 0.2.0 the recording layer is kept silent
until it commits, which removes the most likely cause but is a workaround, not
the design.

The design that removes the class of bug is the user's: **one recording buffer,
one playback buffer per layer, swapped at the loop point, mixed only when it is
safe.** Doing that on Linux alone would mean re-verifying the audio path we
cannot currently measure. Doing it while Windows support lands means one
architectural change, measured once, on both platforms.

## Constraints that shape the plan

- **Windows 10 is the floor.** Python 3.11, SuperCollider 3.13, PyInstaller 6.x,
  Inno Setup 6. Nothing Win11-only, no Store dependencies, no winget.
- **Wine is a harness, not evidence.** It exposes MME only. It can prove the
  plumbing (bridge, IPC, sclang boot, device enumeration); it can never prove
  WASAPI latency or how it sounds. Real verification happens on the Win10
  laptop, and the plan says so at the milestone where it matters.
- **Measure before changing the audio path.** The last two blind attempts at the
  write gate cost DUB and STOP ALL. Every audio milestone ends with a
  measurement from an exported WAV (`scripts/wavcheck.py`, `scripts/smoke.py`),
  not with "the suite is green".
- **SuperCollider stays a prerequisite, not bundled.** `scsynth.exe` ships with
  its own DLLs and plugin folder under GPL; pointing at the installed copy keeps
  our archive free of GPL binaries and keeps one audio stack per machine.
- **No release until it is on real hardware.** A 0.3.0 tag means WASAPI has been
  run on the Win10 laptop and the Linux build still passes 63/63.

## Known blockers already found (do not re-discover them)

1. `scsynth.exe` is not on PATH, so SC's server boot fails: the engine must set
   `Server.program` from `sclang`'s own directory.
2. SuperCollider's compiled-SynthDef cache directory
   (`%LOCALAPPDATA%\SuperCollider\synthdefs`) does not exist on a fresh Windows
   install — create it before boot.
3. `find_sclang()` must never resolve a bare `sclang` on Windows; fixed in
   `6735d9a`, and `SCLANG` overrides it.
4. `Engine.stop()` uses `/proc` and `pkill` on Linux; the Windows branch uses
   `taskkill` but has never been exercised.

---

## Goal text

```text
Looper OSS 0.3.0: Windows support (Windows 10 floor) and a correct loop seam.

Work in dev, commit locally, push only when I say. Release 0.3.0 only when every
acceptance check below has been run for real.

M1 — the app boots end to end under Wine
  Set Server.program from sclang's own directory so scsynth.exe is found; create
  SuperCollider's synthdefs cache dir before boot. Done when, under
  ~/.wine-looper, `wine python.exe -u -m app.main --window none` serves :8181,
  sclang.exe boots engine/looper.scd with 0 ERROR in the log, and scsynth.exe
  opens an MME device (device name in the log).
  Evidence: the engine log lines, pasted.

M2 — our code paths work on Windows, not just boot
  Exercise the real protocol under Wine: /ldstate arrives, meters move, a track
  records and plays, export writes a WAV with audio in it. Fix every POSIX
  assumption found (paths, /proc, pkill, signals, file locking, non-ASCII paths).
  Done when a scripted pass under Wine reports the same state contract as Linux
  (26 globals / 32 track fields) and an exported WAV measures above zero.

M3 — measure the seam, then replace the buffer scheme
  First get numbers, not opinions: record a 220 Hz tone as take 1 with
  autoDub on, roll into the overdub, stop, export, and measure with
  scripts/wavcheck.py — the step across the loop point and the 5-15 kHz energy
  in the seam window, layer 1 alone and the mix. Record those numbers as the
  baseline in goal.md.
  Then implement one recording buffer and one playback buffer per layer, swapped
  at the loop point, mixed only when it is safe — so no synth ever reads a
  buffer it writes in the same block.
  Done when the same measurement, repeated after the change, shows the 5-15 kHz
  seam energy below the pre-change baseline and no step at the loop point, and
  63/63 still passes.

M4 — 0.3.0 runs on real Windows 10
  PyInstaller onedir build + Inno Setup per-user installer. SuperCollider is a
  documented prerequisite (3.13.x), not bundled: scsynth.exe and its plug-ins
  stay on the user's machine.
  Done when, on my Win10 laptop: it installs, opens, records a loop, overdubs,
  exports a WAV, and the exported file has the expected duration and audible
  content. Report the WASAPI device name and the round-trip latency that SC
  reports.

M5 — ship 0.3.0
  VERSION 0.3.0, CHANGELOG with the seam fix measured and Windows stated as
  supported on Windows 10 with SuperCollider 3.13 installed, README updated for
  the Windows install path, docs/AUDIO.md updated for the buffer pair, AppImage
  rebuilt and installed over 0.1.0 locally.
  Done when 63/63 passes on Linux, the UI check says UI OK, scripts/sweep.py
  leaves nothing running, and I have tagged v0.3.0 after telling you.

Rules while doing this:
- Measure before touching the audio path. A green suite is not evidence that the
  instrument works; you found three real bugs that way already.
- Never claim Windows works from a Wine result. Wine only has MME.
- If something cannot be verified here, say so plainly and leave it as a named
  blocker instead of shipping it as done.
- Commit messages: Conventional Commits, one subject line under 72 characters.
```
