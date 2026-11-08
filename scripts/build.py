#!/usr/bin/env python3
"""Build Looper OSS for every platform from one command.

    ./scripts/build.py                 # everything this machine can build
    ./scripts/build.py linux           # AppImage + source archives
    ./scripts/build.py windows         # Windows build, driven through Wine
    ./scripts/build.py --check         # what is available, change nothing

Why a single entry point: the Windows build is the fragile one -- it needs a
Windows CPython inside a Wine prefix, PyInstaller, an .ico and (optionally) Inno
Setup. Doing that by hand once is fine; doing it every day for a month is how a
release gets shipped from a stale tree.

## Windows artefacts and the SmartScreen warning

An unsigned .exe can always raise "Windows protected your PC". That is not a bug
to work around: SmartScreen warns low-reputation binaries, and reputation is only
earned by *signed* ones, so an unsigned installer starts at zero every time.

So the build emits three things, in order of how little they annoy the user:

  looper-oss-<ver>-windows-x64.zip          portable, no installer, no admin.
                                            Extract and run. Nothing to click
                                            through, nothing to trust.
  looper-oss-<ver>-windows-x64-setup.exe    Inno Setup installer, per-user into
                                            %LOCALAPPDATA%. This one *can* show
                                            the SmartScreen prompt until it is
                                            signed.
  (signed)                                  set LOOPER_OSS_SIGNTOOL,
                                            LOOPER_OSS_PFX and
                                            LOOPER_OSS_PFX_PASS and the build
                                            signs the installer and the exe.
                                            That, and only that, ends the
                                            warning -- there is no trick.

The portable zip is the recommended download while we have no certificate.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
WIN_PREFIX = pathlib.Path(os.environ.get("LOOPER_OSS_WINEPREFIX",
                                         pathlib.Path.home() / ".wine-looper"))
WIN_PY = r"C:\Python311\python.exe"
ISCC_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def version() -> str:
    return (ROOT / "VERSION").read_text().strip()


def run(cmd: list[str], cwd: pathlib.Path | None = None, env: dict | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    printable = " ".join(str(c) for c in cmd)
    print(f"   $ {printable}")
    return subprocess.run(cmd, cwd=cwd or ROOT, env=env, check=check,
                          capture_output=True, text=True)


def wine_env() -> dict:
    env = dict(os.environ)
    env["WINEPREFIX"] = str(WIN_PREFIX)
    env.setdefault("WINEDEBUG", "-all")
    return env


def wine_path(path: pathlib.Path) -> str:
    """A host path as the Windows build sees it: Z:\\home\\... """
    resolved = path.resolve()
    return "Z:" + str(resolved).replace("/", "\\")


# --------------------------------------------------------------------- linux --
def build_linux() -> list[pathlib.Path]:
    print("== linux")
    out: list[pathlib.Path] = []
    appimage = ROOT / "packaging" / "linux" / "build-appimage.sh"
    if not appimage.exists():
        print("   no packaging/linux/build-appimage.sh — skipped")
    else:
        res = run(["bash", str(appimage)], check=False)
        if res.returncode != 0:
            print("   AppImage build FAILED")
            print("\n".join((res.stdout + res.stderr).strip().splitlines()[-8:]))
        else:
            out += sorted(DIST.glob("*.AppImage"))
    res = run([sys.executable, "scripts/release.py"], check=False)
    if res.returncode != 0:
        print("   source archive build FAILED")
        print("\n".join((res.stdout + res.stderr).strip().splitlines()[-8:]))
    else:
        out += sorted(DIST.glob("*.tar.gz")) + sorted(DIST.glob("*.zip"))
    return out


# ------------------------------------------------------------------- windows --
def wine_python_ready() -> bool:
    if not (WIN_PREFIX / "drive_c" / "Python311" / "python.exe").exists():
        return False
    res = run(["wine", WIN_PY, "-c", "import PyInstaller, websockets"],
              env=wine_env(), check=False)
    if res.returncode == 0:
        return True
    print("   installing build dependencies into the Wine Python")
    run(["wine", WIN_PY, "-m", "pip", "install", "--quiet",
         "--disable-pip-version-check", "pyinstaller", "-r", "requirements.txt"],
        env=wine_env(), check=False)
    return run(["wine", WIN_PY, "-c", "import PyInstaller"], env=wine_env(),
               check=False).returncode == 0


def make_icon() -> pathlib.Path | None:
    """SVG -> ICO, only if ImageMagick is around; the build works without it."""
    ico = ROOT / "packaging" / "windows" / "looper-oss.ico"
    if ico.exists():
        return ico
    svg = ROOT / "packaging" / "linux" / "looper-oss.svg"
    if not svg.exists() or not shutil.which("magick"):
        return None
    run(["magick", "-background", "none", str(svg),
         "-define", "icon:auto-resize=256,64,32,16", str(ico)], check=False)
    return ico if ico.exists() else None


def sign(path: pathlib.Path) -> None:
    """Sign with signtool when a certificate is configured; otherwise say so once."""
    signtool = os.environ.get("LOOPER_OSS_SIGNTOOL")
    pfx = os.environ.get("LOOPER_OSS_PFX")
    password = os.environ.get("LOOPER_OSS_PFX_PASS", "")
    if not (signtool and pfx):
        return
    run(["wine", signtool, "sign", "/fd", "SHA256", "/f", wine_path(pathlib.Path(pfx)),
         "/p", password, "/tr", "http://timestamp.digicert.com", "/td", "SHA256",
         wine_path(path)], env=wine_env(), check=False)
    print(f"   signed {path.name}")


SC_VERSION = "3.13.0"
SC_ZIP = f"https://github.com/supercollider/supercollider/releases/download/Version-{SC_VERSION}/SuperCollider-{SC_VERSION}-win64.zip"
SC_CACHE = ROOT / "build" / "supercollider"


def fetch_supercollider() -> pathlib.Path:
    """Download the official Windows build once, into build/supercollider/.

    The CI runner has no SuperCollider, and a release built without it would be
    the one thing this application must never be: something the user has to
    finish installing by hand. Cached, so repeated builds cost nothing.
    """
    # the archive extracts to a plain "SuperCollider" directory, not a versioned
    # one -- find sclang.exe rather than trusting the layout
    for found in SC_CACHE.rglob("sclang.exe"):
        return found.parent
    target = SC_CACHE / "SuperCollider"
    SC_CACHE.mkdir(parents=True, exist_ok=True)
    archive = SC_CACHE / f"sc-{SC_VERSION}.zip"
    if not archive.exists():
        print(f"   downloading SuperCollider {SC_VERSION} (92 MB, once)")
        import urllib.request
        try:
            urllib.request.urlretrieve(SC_ZIP, archive)
        except OSError as exc:
            print(f"   download failed: {exc}")
            return target
    print("   extracting")
    import zipfile
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(SC_CACHE)
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"   cannot unpack {archive.name}: {exc}")
        return target
    for found in SC_CACHE.rglob("sclang.exe"):
        return found.parent
    return target


def bundle_supercollider(appdir: pathlib.Path, allow_download: bool = True) -> None:
    """Copy SuperCollider next to the app so Windows needs no separate install.

    SuperCollider is GPL-3.0 and so is Looper OSS, so shipping the unmodified
    binaries together is allowed; the runtime keeps its own COPYING and AUTHORS,
    and sc/VERSION.txt records the exact upstream version and where its source
    lives. Only the IDE is left out (scide.exe, HelpSource, examples, sounds) --
    sclang.exe links Qt5WebEngineWidgets, so the Qt runtime has to stay.
    """
    src = pathlib.Path(os.environ.get("LOOPER_OSS_SC_DIR", r"C:\SC\SuperCollider"))
    # a Linux host path also works, which is how the Wine builds are made here
    if not (src / "sclang.exe").exists():
        src = WIN_PREFIX / "drive_c" / "SC" / "SuperCollider"
    if not (src / "sclang.exe").exists() and allow_download:
        src = fetch_supercollider()
    if not (src / "sclang.exe").exists():
        print(f"   SuperCollider not found at {src} — the build will need it installed")
        return
    dest = appdir / "sc"
    if dest.exists():
        try:
            shutil.rmtree(dest)
        except OSError as exc:
            print(f"   could not clear {dest}: {exc}")
            return
    skip = {"scide.exe", "HelpSource", "examples", "sounds", "supernova.exe"}
    copied = 0
    for path in src.rglob("*"):
        try:
            rel = path.relative_to(src)
        except ValueError:
            continue
        if rel.parts[0] in skip:
            continue
        target = dest / rel
        try:
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied += 1
        except OSError as exc:
            # a locked file in the source install must not lose the whole build
            print(f"   skipped {rel}: {exc}")
    version = "3.13.0"
    (dest / "VERSION.txt").write_text(
        f"SuperCollider {version} for Windows (unmodified upstream build).\n"
        f"Licence: GPL-3.0-or-later — see COPYING in this directory.\n"
        f"Source: https://github.com/supercollider/supercollider/releases/tag/Version-{version}\n"
        "Bundled with Looper OSS so the application needs no separate install.\n",
        newline="\n")
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    print(f"   bundled SuperCollider: {copied} files, {size / 1048576:.0f} MB")


def build_windows() -> list[pathlib.Path]:
    print("== windows (through Wine)")
    if not (WIN_PREFIX / "drive_c").exists():
        print(f"   no Wine prefix at {WIN_PREFIX} — set LOOPER_OSS_WINEPREFIX")
        return []
    if not wine_python_ready():
        print("   Windows CPython or PyInstaller missing in the prefix — skipped")
        print("   one-time setup: winetricks, or install python-3.11 into the prefix")
        return []

    make_icon()
    ver = version()
    res = run(["wine", WIN_PY, "-m", "PyInstaller", "--clean", "--noconfirm",
               "packaging\\windows\\looper_oss.spec"], env=wine_env(), check=False)
    if res.returncode != 0:
        print("   PyInstaller FAILED")
        print("\n".join((res.stdout + res.stderr).strip().splitlines()[-12:]))
        return []

    appdir = DIST / "LooperOSS"
    if not (appdir / "LooperOSS.exe").exists():
        print("   dist/LooperOSS/LooperOSS.exe missing after the build — FAILED")
        return []

    bundle_supercollider(appdir)

    out: list[pathlib.Path] = []
    zipath = DIST / f"looper-oss-{ver}-windows-x64.zip"
    print(f"   packing {zipath.name}")
    with zipfile.ZipFile(zipath, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(appdir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(DIST))
    out.append(zipath)

    iscc = next((p for p in ISCC_CANDIDATES
                 if (WIN_PREFIX / "drive_c" / p.replace("C:\\", "").replace("\\", "/")).exists()),
                None)
    if iscc is None:
        print("   Inno Setup not installed in the prefix — installer skipped")
        print("   (portable zip above has no installer and no admin prompt)")
        return out
    # the .iss takes the version as a define and defaults to 0.0.0: without this
    # the installer called itself looper-oss-0.0.0-setup.exe
    res = run(["wine", iscc, "/Q", f"/DAppVersion={ver}",
               "packaging\\windows\\looper-oss.iss"], env=wine_env(), check=False)
    setups = sorted(DIST.glob("*setup*.exe")) + sorted(DIST.glob("looper-oss-*-windows-x64.exe"))
    for setup in setups:
        sign(setup)
        out.append(setup)
    if not setups:
        print("   ISCC ran but produced no installer")
    return out


# ---------------------------------------------------------------------- main --
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="build Looper OSS")
    ap.add_argument("targets", nargs="*", default=[],
                    help="linux | windows | bundle-sc <dir>")
    ap.add_argument("--check", action="store_true", help="report, build nothing")
    args = ap.parse_args(argv[1:])
    DIST.mkdir(exist_ok=True)

    if args.check:
        print(f"Looper OSS {version()}")
        print(f"  wine prefix   {WIN_PREFIX} "
              f"{'present' if (WIN_PREFIX / 'drive_c').exists() else 'MISSING'}")
        print(f"  wine python   {'yes' if (WIN_PREFIX / 'drive_c' / 'Python311' / 'python.exe').exists() else 'no'}")
        print(f"  appimagetool  {'yes' if (ROOT / 'build' / 'appimagetool.runtime').exists() else 'no'}")
        print(f"  inno setup    {'yes' if any((WIN_PREFIX / 'drive_c' / p.replace('C:\\', '').replace('\\', '/')).exists() for p in ISCC_CANDIDATES) else 'no'}")
        print(f"  signing       {'configured' if os.environ.get('LOOPER_OSS_PFX') else 'not configured (SmartScreen will warn on .exe)'}")
        return 0

    if args.targets and args.targets[0] == "bundle-sc":
        # used by packaging/windows/build.ps1 on a real Windows machine, so the
        # bundle logic exists exactly once and is tested here
        dest = pathlib.Path(args.targets[1]) if len(args.targets) > 1 else DIST / "LooperOSS"
        bundle_supercollider(dest, allow_download="--no-download" not in argv)
        return 0

    targets = args.targets or (["linux"] + (["windows"] if (WIN_PREFIX / "drive_c").exists() else []))
    built: list[pathlib.Path] = []
    if "linux" in targets:
        built += build_linux()
    if "windows" in targets:
        built += build_windows()

    print("\n== artefacts")
    for path in built:
        if path.exists():
            print(f"   {path.relative_to(ROOT)}   {path.stat().st_size / 1048576:.1f} MB")
    if not built:
        print("   nothing built")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
