#!/usr/bin/env python3
"""Build a distributable release archive for Looper OSS.

    ./scripts/release.py                 # dist/looper-oss-<version>.tar.gz + .zip + SHA256SUMS
    ./scripts/release.py --out /tmp/dl   # somewhere else
    ./scripts/release.py --check         # verify the tree, build nothing

What this guarantees, because a release archive is the first thing a stranger
touches:

  * no runtime state ships -- sessions/ (the user's audio), logs/, .venv/,
    __pycache__/, engine/_config.scd (generated), editor cruft
  * run.sh and the scripts keep their executable bit, in the .zip too
    (the zip format drops Unix modes unless you set them by hand)
  * every file the launcher actually needs is present in the archive, checked
    by opening the archive again and looking for it
  * archives are reproducible: mtimes are normalised, so building twice from
    the same tree gives the same SHA256 (unless SOURCE_DATE_EPOCH says otherwise)
  * the version comes from the VERSION file and nowhere else
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import pathlib
import stat
import sys
import tarfile
import time
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = "looper-oss"  # archive/directory slug; display name is "Looper OSS"

# never ship these, at any depth
SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".ruff_cache", ".pytest_cache",
    ".mypy_cache", "sessions", "logs", "dist", "build", "node_modules",
}
SKIP_FILES = {
    "engine/_config.scd",     # generated from ui/config.json on every start
    "ui/config.json.bak",     # local edit backup
    ".DS_Store", "Thumbs.db", "desktop.ini",
}
SKIP_SUFFIXES = {".pyc", ".pyo", ".orig", ".rej", ".swp", ".log", ".wav"}

# the launcher dies without these; a release missing one is worse than no release
REQUIRED = [
    "README.md", "LICENSE", "VERSION", "CHANGELOG.md", "ACKNOWLEDGEMENTS.md",
    "requirements.txt", "run.sh", "run.ps1",
    "ui/index.html", "ui/app.js", "ui/controls.js", "ui/style.css", "ui/config.json",
    "bridge/bridge.py", "bridge/write_config.py",
    "engine/looper.scd", "engine/vst.scd",
    "docs/PROTOCOL.md", "docs/CONTROLS.md", "docs/AUDIO.md", "docs/LICENSING.md",
    "scripts/smoke.py", "scripts/uicheck.mjs", "scripts/sweep.py", "scripts/wavcheck.py",
]

# checked out by git or by the user's unzip, then run directly
EXECUTABLE = {"run.sh", "scripts/smoke.py", "scripts/sweep.py", "scripts/wavcheck.py",
              "scripts/release.py", "bridge/bridge.py"}


def version() -> str:
    """Read VERSION; refuse anything that is not a plain dotted number."""
    try:
        raw = (ROOT / "VERSION").read_text().strip()
    except OSError as exc:
        sys.exit(f"release: cannot read VERSION ({exc})")
    parts = raw.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        sys.exit(f"release: VERSION must be MAJOR.MINOR.PATCH, found {raw!r}")
    return raw


# zip cannot store a timestamp before 1980-01-01 00:00:00 UTC; tar cannot go
# negative. One fixed stamp for every member is what makes rebuilds identical.
STAMP = 315532800


def epoch() -> int:
    """Fixed timestamp for every archive member -> byte-identical rebuilds."""
    env = os.environ.get("SOURCE_DATE_EPOCH", "")
    try:
        return int(env)
    except ValueError:
        return STAMP


def collect() -> list[pathlib.Path]:
    """Every file that belongs in the archive, in a stable order."""
    out: list[pathlib.Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if str(rel).replace(os.sep, "/") in SKIP_FILES:
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        out.append(rel)
    return out


def missing(files: list[pathlib.Path]) -> list[str]:
    present = {str(p).replace(os.sep, "/") for p in files}
    return [name for name in REQUIRED if name not in present]


def mode_of(rel: pathlib.Path) -> int:
    name = str(rel).replace(os.sep, "/")
    return 0o755 if name in EXECUTABLE else 0o644


def build_tar(files, out_path: pathlib.Path, base: str, stamp: int) -> None:
    # mtime=0 and no embedded filename keeps the gzip header stable
    try:
        with open(out_path, "wb") as raw, \
                gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9,
                              mtime=0) as gz, \
                tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for rel in files:
                relposix = str(rel).replace(os.sep, "/")
                data = (ROOT / rel).read_bytes()
                info = tarfile.TarInfo(f"{base}/{relposix}")
                info.size = len(data)
                info.mtime = stamp
                info.mode = mode_of(rel)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                tar.addfile(info, io.BytesIO(data))
    except OSError as exc:
        sys.exit(f"release: cannot write {out_path} ({exc})")


def build_zip(files, out_path: pathlib.Path, base: str, stamp: int) -> None:
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in files:
            relposix = str(rel).replace(os.sep, "/")
            info = zipfile.ZipInfo(
                f"{base}/{relposix}",
                date_time=time.gmtime(max(stamp, STAMP))[:6],
            )
            info.external_attr = (stat.S_IFREG | mode_of(rel)) << 16  # keep the +x bit
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, (ROOT / rel).read_bytes())


def verify(archive: pathlib.Path, files, base: str) -> None:
    """Reopen the archive and prove it contains what the launcher needs."""
    want = {f"{base}/" + str(p).replace(os.sep, "/") for p in files}
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            got = set(zf.namelist())
    else:
        with tarfile.open(archive) as tar:
            got = set(tar.getnames())

    absent = sorted(want - got)
    extra = sorted(got - want)
    if absent or extra:
        sys.exit(f"release: {archive.name} does not match the tree\n"
                 f"  missing: {absent[:5]}\n  unexpected: {extra[:5]}")

    forbidden = [n for n in got
                 if "/sessions/" in n or "/logs/" in n or "/.venv/" in n
                 or "__pycache__" in n or n.endswith("/_config.scd")]
    if forbidden:
        sys.exit(f"release: refused to ship runtime state: {forbidden[:5]}")


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError as exc:
        sys.exit(f"release: cannot read {path} ({exc})")
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="build a Looper OSS release archive")
    ap.add_argument("--out", default="dist", help="output directory (default: dist)")
    ap.add_argument("--check", action="store_true", help="verify the tree, build nothing")
    args = ap.parse_args()

    ver = version()
    files = collect()
    absent = missing(files)

    print(f"Looper OSS {ver}")
    print(f"  tree    {len(files)} files, {sum((ROOT / f).stat().st_size for f in files) / 1024:.0f} KiB")
    if absent:
        print("  missing required files:")
        for name in absent:
            print(f"    - {name}")
        return 1
    if args.check:
        print("  ok — nothing runtime-state-like in the tree, all required files present")
        return 0

    out = ROOT / args.out if not args.out.startswith("/") else pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = f"{PKG}-{ver}"
    stamp = epoch()

    tar_path = out / f"{base}.tar.gz"
    zip_path = out / f"{base}.zip"
    build_tar(files, tar_path, base, stamp)
    build_zip(files, zip_path, base, stamp)
    verify(tar_path, files, base)
    verify(zip_path, files, base)

    sums = out / "SHA256SUMS"
    sums.write_text("".join(f"{sha256(p)}  {p.name}\n" for p in (tar_path, zip_path)))

    print()
    for p in (tar_path, zip_path):
        print(f"  {p.relative_to(ROOT)}  {p.stat().st_size / 1024:.0f} KiB  {sha256(p)[:16]}…")
    print(f"  {sums.relative_to(ROOT)}")
    print()
    print("  unpack, then:  ./run.sh --setup && ./run.sh --browser")
    return 0


if __name__ == "__main__":
    sys.exit(main())
