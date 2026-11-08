# Windows build — experimental

**Nothing here has been built, installed or run on Windows.** The recipe is
written; that is the whole state of it. It is the starting point for 0.3.0 (see
`goal.md`), and any artefact it produces is an experiment.

The words matter: Windows is **untested**, not "unverified" — the second one
suggests somebody had a go and it nearly worked.

## Build it on Windows

Python 3.9+ on the build machine:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

Produces:

- `dist\LooperOSS\LooperOSS.exe` — the app, onedir (onefile would unpack on every
  start, which is a visible delay on a pedal)
- `dist\looper-oss-<version>-windows-x64-portable.zip`
- `dist\looper-oss-<version>-setup.exe`, if Inno Setup's `iscc.exe` is found —
  on `PATH` or in `%ProgramFiles(x86)%\Inno Setup 6`

SuperCollider is **not** bundled. It is a separate install on Windows, exactly
like the audio driver, and the app says so in its own window when it is missing.

## Build it in CI

`.github/workflows/windows-experimental.yml` — manual dispatch, or a `v*` tag.
It runs the same script on a `windows-latest` runner and uploads
`windows-experimental-<version>` as a workflow artefact. On a tag it attaches to
an existing Release; it never creates one.

## Build it under Wine

```bash
wineboot -u                      # create the prefix
# then install a Windows CPython into the prefix, and from the repo root:
wine python.exe packaging/windows/build.ps1
```

Untested: no Windows CPython has been installed in the prefix here, and
PyInstaller's bootloader has never been exercised under Wine.

## What is known to be missing

- WASAPI and ASIO device enumeration. The engine has a `driver = asio` branch and
  it has never run.
- `Engine.stop()` calls `pkill`, which is Unix-only.
- scsynth/sclang discovery, and the staged home under `%LOCALAPPDATA%`.
- No `looper-oss.ico` in the tree, so the build ships without an icon.

## Do not

- Do not publish an artefact from here as a release.
- Do not describe Windows as supported, and not as "unverified" either.
