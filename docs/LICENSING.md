# Licensing — Looper OSS

Looper OSS is **GPL-3.0-or-later**. The full text is in the [`LICENSE`](../LICENSE)
file at the repository root.

**This repository ships no third-party code.** There is no vendored JS, no
bundled Python packages, no copied DSP source, and no GPL-3.0 text that came
from somewhere else. Everything outside `ui/`, `bridge/`, `engine/` and
`scripts/` is something the user's own package manager installed, and each is
attributed below.

That makes the licence question simple: your obligations under GPL-3.0 are the
normal ones for your own source, and the third-party labels are informational.

---

## 1. What Looper OSS links against, and what it merely launches

The distinction matters more than the licence names.

| relation | how | consequence |
| --- | --- | --- |
| **imported** | `bridge/bridge.py` does `import websockets`, `from pythonosc…` | a derivative work ships with the bridge; both licences are permissive, so GPL-3.0 is compatible and nothing extra is required |
| **launched as a separate process** | the launcher runs `sclang engine/looper.scd`; sclang boots `scsynth` | GPL-3.0↔GPL-3.0 either way; OSC over UDP is not linking, so there is no conflict to resolve |
| **loaded into scsynth** | sc3-plugins `.so` files, `VSTPlugin` UGen, your own VST3/VST2 plugins | these are scsynth's plugin ABI, not Looper OSS code; we neither ship nor sublicense them |
| **opened as a web page** | `--browser`/`--kiosk` launch chromium/firefox | plain HTTP on `127.0.0.1`; the browser is a separate program, not a bundled dependency |

## 2. Required dependencies

| component | version tested | licence | shipped here? |
| --- | --- | --- | --- |
| [SuperCollider](https://supercollider.github.io/) — `sclang`, `scsynth` | 3.14.1 | **GPL-3.0-or-later** | no — OS package |
| [sc3-plugins](https://github.com/supercollider/sc3-plugins) | 3.14.x | **GPL-2.0-or-later** | no — OS package |
| [websockets](https://github.com/python-websockets/websockets) | 17.4 | **BSD-3-Clause** | no — pip |
| [python-osc](https://github.com/attwad/python-osc) | 1.10.2 | **Unlicense** (public domain) | no — pip |

`sc3-plugins` is GPL-**2**-or-later while Looper OSS is GPL-3.0. This is not a
conflict: `or-later` means those plugins may be used under GPLv3, and in any case
Looper OSS does not distribute them — the launcher refuses to start without them and
points at your package manager.

## 3. Optional dependencies

| component | licence | notes |
| --- | --- | --- |
| [VSTPlugin](https://github.com/Spacechild1/vstplugin) by Christof Ressi (IEM Graz) | **BSD-3-Clause** | `engine/vst.scd` is only loaded if the extension is installed. Detected with `\VSTPluginController.asClass.notNil`; absent means the slots are simply not there. |
| [PipeWire](https://pipewire.org/) + `pipewire-jack` | **MIT** (`libspa-alsa.so` is LGPL, `libjackserver.so` is GPL-2.0) | the audio server on Linux; not shipped |
| [playwright](https://playwright.dev/) | **Apache-2.0** | dev-only, runs `scripts/uicheck.mjs`; not needed to use Looper OSS |
| chromium / firefox | their own | only launched by `--browser` / `--kiosk` |

**VST plugins you load are yours to license.** `VSTPlugin` is permissively
licensed, but each plugin you open in a slot carries its own licence, and the
VST3 SDK terms apply to whoever built it. Looper OSS saves the plugin's opaque
state blob to `sessions/…/vst_*.bin` and ships nothing of it.

## 4. Not a dependency, but present in some `venv`s

`python-rtmidi` (MIT) appears in the development `venv` but **nothing imports
it** — MIDI goes through SuperCollider's own `MIDIClient`, in the engine, not
through Python. It is deliberately absent from `requirements.txt`. Remove it
with `.venv/bin/pip uninstall python-rtmidi`.

## 5. What this means when you redistribute

You may:

- redistribute Looper OSS, modified or not, provided you ship the GPL-3.0 text
  and make the corresponding source available;
- charge money for it, or for support;
- combine it with the permissive dependencies above without changing their terms.

You may not add a restriction on top of GPL-3.0 (no "personal use only" addendum,
no extra patent or DRM terms). If you build an appliance that ships Looper OSS,
the GPL-3.0 obligation to provide source travels with the device — the fact that
SuperCollider is also GPL-3.0 does not create an exemption.


## 5a. The bundled SuperCollider on Windows

The Windows build ships SuperCollider **inside the application**
(`sc/` beside `LooperOSS.exe`), so a user installs nothing else.

- SuperCollider 3.13.0 is GPL-3.0-or-later; Looper OSS is GPL-3.0. The two are
  compatible and are distributed together under the same terms.
- The binaries are **unmodified**. The bundle keeps upstream's `COPYING` and
  `AUTHORS`, and `sc/VERSION.txt` records the exact version and the source URL
  (`https://github.com/supercollider/supercollider/releases/tag/Version-3.13.0`).
  Anyone who redistributes Looper OSS must keep those files.
- If the bundled binaries are ever patched, the corresponding source has to be
  made available as well — that is the one thing "we just copy the folder" does
  not cover.
- The excluded IDE (`scide.exe`) is not redistributed. The Qt runtime is, because
  `sclang.exe` links it.

## 6. Reporting a licensing problem

If something in this repository turns out to be mis-attributed, that is a bug and
it will be treated as one: open an issue naming the file and the licence you
believe applies, and it will be corrected or removed.
