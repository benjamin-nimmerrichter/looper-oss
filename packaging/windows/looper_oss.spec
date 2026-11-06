# PyInstaller spec for the Windows build of Looper OSS.
#
#   pyinstaller --clean packaging/windows/looper_oss.spec
#
# onedir, not onefile: onefile unpacks ~30 MB to %TEMP% on every start, which is
# a visible delay on a pedal. Inno Setup then wraps dist/LooperOSS into an
# installer (packaging/windows/looper-oss.iss).
#
# SuperCollider is not bundled -- it is installed separately on Windows, exactly
# like the audio driver. The app reports it in its own window if sclang is absent.
import pathlib
import sys

ROOT = pathlib.Path(SPECPATH).resolve().parent          # packaging/windows
REPO = ROOT.parent                                       # repository root
sys.path.insert(0, str(REPO))

PAYLOAD = ["app", "bridge", "engine", "ui", "docs"]
TOP_FILES = ["VERSION", "LICENSE", "ACKNOWLEDGEMENTS.md", "CHANGELOG.md",
             "README.md", "requirements.txt"]
# generated on first start from ui/config.json, so it must not be shipped stale
SKIP = {"engine/_config.scd", "ui/config.json.bak"}


def payload_entries():
    datas = []
    for folder in PAYLOAD:
        for path in (REPO / folder).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                rel = path.relative_to(REPO).as_posix()
                if rel not in SKIP:
                    datas.append((str(path), folder))
    for name in TOP_FILES:
        path = REPO / name
        if path.is_file():
            datas.append((str(path), "."))
    return datas


a = Analysis(
    [str(REPO / "looper_oss.py")],
    pathex=[str(REPO)],
    binaries=[],
    datas=payload_entries(),
    hiddenimports=["websockets", "pythonosc", "app.main", "app.window"],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest", "pydoc_data"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LooperOSS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # a window, not a console: no black box on double-click
    disable_windowed_traceback=False,
    icon=str(ROOT / "looper-oss.ico") if (ROOT / "looper-oss.ico").is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LooperOSS",
)
