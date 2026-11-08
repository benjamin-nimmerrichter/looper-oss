# Windows build

Built and run **under Wine** on this repository: the packaged application starts,
serves the UI, boots SuperCollider, opens an MME device and passes
`scripts/wincheck.py` 12/12. It has **not yet been run on real Windows 10** —
Wine exposes MME only, so WASAPI/ASIO, real latency and MIDI input are still
open. Windows support is claimed in 0.3.0, after a run on hardware.

## One command

```bash
python3 scripts/build.py windows     # everything below, through Wine
python3 scripts/build.py             # Linux + Windows
python3 scripts/build.py --check     # what is available, builds nothing
```

What it does, in order:

1. `pip install pyinstaller -r requirements.txt` into the Wine prefix's Windows
   CPython (`C:\Python311\python.exe`), skipping if already importable.
2. `magick` converts `packaging/linux/looper-oss.svg` to `looper-oss.ico`
   (optional — the build continues without an icon).
3. `PyInstaller --clean --noconfirm packaging\windows\looper_oss.spec` → onedir
   build in `dist/LooperOSS/`. Onedir, not onefile: onefile unpacks ~30 MB to
   `%TEMP%` on every start, which is a visible delay on a pedal.
4. `bundle_supercollider()` copies the SuperCollider runtime into
   `dist/LooperOSS/sc/` (see below).
5. Zips `dist/LooperOSS/` → `dist/looper-oss-<ver>-windows-x64.zip`.
6. `ISCC.exe /DAppVersion=<ver> packaging/windows/looper-oss.iss` → the
   installer, if Inno Setup 6 is installed in the prefix.
7. Signs both, if `LOOPER_OSS_SIGNTOOL`, `LOOPER_OSS_PFX` and
   `LOOPER_OSS_PFX_PASS` are set.

## Artefacts, and the SmartScreen truth

| file | what it is |
| --- | --- |
| `looper-oss-<ver>-windows-x64.zip` | portable, no installer, no admin. **Recommended download.** |
| `looper-oss-<ver>-setup.exe` | per-user installer into `%LOCALAPPDATA%\Programs\Looper OSS`, Start-menu entry, uninstaller. |

An unsigned `.exe` can always raise "Windows protected your PC". That is not a
bug to work around: SmartScreen warns low-reputation binaries, and reputation is
only earned by **signed** ones, so an unsigned installer starts at zero on every
machine. The zip avoids the prompt; a code-signing certificate (OV ~€200/yr,
EV ~€400/yr) is the only thing that removes it from the `.exe`.

## SuperCollider is bundled

Windows needs no separate SuperCollider install: `scripts/build.py` copies the
runtime into `dist/LooperOSS/sc/` and `app/main.py:find_sclang()` looks for
`sc/sclang.exe` beside the executable **before** anywhere else on the machine.
`engine/looper.scd` then finds `scsynth.exe` from `Platform.resourceDir`, which
is the same directory, so the server boots from the bundle too.

Verified by inspecting the running processes: the packaged app executes
`…\LooperOSS\sc\sclang.exe` and `…\LooperOSS\sc\scsynth.exe`.

Costs and constraints:

- **Size**: ~213 MB of runtime → ~71 MB installer. The IDE is excluded
  (`scide.exe`, `HelpSource`, `examples`, `sounds`, `supernova.exe`), but the Qt
  DLLs cannot be: `sclang.exe` links `Qt5WebEngineWidgets`, so most of that
  weight is WebEngine whether we use it or not.
- **Licence**: SuperCollider is GPL-3.0-or-later and so is Looper OSS, so
  shipping the unmodified binaries together is allowed. The bundle keeps its own
  `COPYING` and `AUTHORS`, and `sc/VERSION.txt` names the exact upstream version
  and the source URL. Do not modify the bundled binaries; if you ever do, the
  source you ship has to match.
- Set `LOOPER_OSS_SC_DIR` to build from a different SuperCollider install.

## Inno Setup

The installer script needs `ISCC.exe` in the prefix. Install it once:

```bash
curl -LO https://github.com/jrsoftware/issrc/releases/download/is-6_4_0/innosetup-6.4.0.exe
WINEPREFIX=~/.wine-looper wine innosetup-6.4.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

Without it the build still produces the portable zip and says so.

`DefaultDirName` is `%LOCALAPPDATA%\Programs\Looper OSS` **on purpose**: the app
stages itself into `%LOCALAPPDATA%\looper-oss\app\<version>\` and keeps
`sessions/` there, so installing into that tree would let an uninstall take the
user's music.

## Testing it here

```bash
# source tree, with the Wine prefix's Python
SCLANG='C:\SC\SuperCollider\sclang.exe' WINEPREFIX=~/.wine-looper \
  wine 'C:\Python311\python.exe' -u -m app.main --window none

# the packaged build (finds its own bundled SuperCollider)
WINEPREFIX=~/.wine-looper wine dist/LooperOSS/LooperOSS.exe --window none

.venv/bin/python scripts/wincheck.py     # 12/12, generous timeouts
```

`wincheck.py` exists because `scripts/smoke.py` is tuned for native timing:
under Wine a round trip can take hundreds of milliseconds and every timing
assertion fails while the feature works. Wine also runs the audio clock ~25×
slower than wall time, so the playhead crawls — that is the environment, not a
regression.

## What Wine cannot tell you

- **WASAPI / ASIO**: Wine offers MME only. `scsynth` prints the backends it has;
  on Wine the list contains nothing else.
- **Latency**: the audio clock above makes any measurement meaningless.
- **MIDI input**: PortMidi cannot open an input under Wine ("Host error"). The
  app degrades — MIDI setup runs in its own thread behind `protect` so a failure
  there cannot stop the engine — but MIDI must be checked on hardware.
- **Install/uninstall**: needs a real Windows account.
- **`Engine.stop()`**: `sclang` exit code 42 and `taskkill` are exercised, but
  whether the WASAPI device is really released can only be seen on Windows.
