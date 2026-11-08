#!/usr/bin/env bash
# Build a self-contained AppImage of Looper OSS.
#
#   ./packaging/linux/build-appimage.sh            -> dist/looper-oss-<ver>-<arch>.AppImage
#
# What ends up inside: the app payload, a bundled CPython (interpreter, stdlib,
# libpython) and the two pinned Python packages. SuperCollider is *not* bundled
# -- it is a system package, like the audio server itself, and the app tells you
# in its own window if sclang is missing.
#
# A native GTK window is used when the host has PyGObject (any GTK desktop does);
# otherwise the app opens Edge/Chromium in app mode, so it still runs on a bare
# system.
set -euo pipefail
cd "$(dirname "$0")/../.."

VERSION="$(cat VERSION)"
ARCH="$(uname -m)"
APPDIR="build/appimage/LooperOSS.AppDir"
OUT="dist"
NAME="looper-oss-${VERSION}-${ARCH}.AppImage"
PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
STDLIB="$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["stdlib"])')"

echo "== Looper OSS $VERSION ($ARCH)"
rm -rf build/appimage
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib/looper-oss/lib" \
         "$APPDIR/usr/lib/python$PY_VER" "$APPDIR/looper-oss" "$OUT"

echo "== payload"
tar -cf - \
  --exclude='./.venv' --exclude='./sessions' --exclude='./logs' --exclude='./dist' \
  --exclude='./build' --exclude='./.git' --exclude='./.ruff_cache' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='./ui/config.json.bak' \
  --exclude='./engine/_config.scd' \
  . | tar -xf - -C "$APPDIR/looper-oss"

echo "== build stamp"
# A rebuilt AppImage keeps the same VERSION, so the launcher needs something
# content-based to notice that the payload changed; otherwise it happily reuses
# the staged copy from the previous build. Sorted sha256 of the payload = stable.
STAMP="$VERSION-$(cd "$APPDIR/looper-oss" && find . -type f | sort | xargs sha256sum | sha256sum | cut -c1-16)"
printf '%s\n' "$STAMP" > "$APPDIR/looper-oss/BUILD"
echo "   $STAMP"

echo "== bundled python $PY_VER"
cp -L "$(command -v python3)" "$APPDIR/usr/bin/python3"
# python3 is dynamically linked here: carrying libpython is the difference
# between "works" and "cannot find libpython3.x.so" on the next machine
for lib in $(ldd "$(command -v python3)" | awk '/libpython/ {print $3}'); do
  cp -L "$lib" "$APPDIR/usr/lib/"
done
# Ubuntu ships libpython unstripped (29 MB against Arch's ~5 MB), and its stdlib
# is 93 MB -- the first CI build produced a 35 MB AppImage instead of 12 MB.
# The symbols are not ours to ship.
STRIP_BIN="$(command -v strip || true)"
if [ -n "$STRIP_BIN" ]; then
  find "$APPDIR/usr/lib" -type f -name '*.so*' \
    -exec "$STRIP_BIN" --strip-unneeded {} + || true
fi
tar -cf - -C "$STDLIB" \
  --exclude='test' --exclude='tests' --exclude='idlelib' --exclude='tkinter' \
  --exclude='turtledemo' --exclude='ensurepip' --exclude='site-packages' \
  --exclude='__pycache__' . | tar -xf - -C "$APPDIR/usr/lib/python$PY_VER"
# no interpreter config to find: keep it from looking for a venv it cannot have
rm -f "$APPDIR/usr/lib/python$PY_VER/site-packages"/*.pth 2>/dev/null || true

echo "== pinned dependencies"
# Arch ships python without pip; the project's own .venv has one, and either is
# fine because --target installs into the AppDir, not into that environment.
PIP=""
if [ -x .venv/bin/pip ]; then
  PIP=".venv/bin/pip"
elif python3 -m pip --version >/dev/null 2>&1; then
  PIP="python3 -m pip"
elif command -v pip >/dev/null 2>&1; then
  PIP="pip"
else
  echo "no pip available: install python-pip, or run ./install.sh first" >&2
  exit 1
fi
$PIP install --quiet --disable-pip-version-check \
  --target "$APPDIR/usr/lib/looper-oss/lib" -r requirements.txt

echo "== desktop integration"
install -m 755 packaging/linux/AppRun "$APPDIR/AppRun"
install -m 644 packaging/linux/looper-oss.desktop "$APPDIR/looper-oss.desktop"
if [ -f packaging/linux/looper-oss.png ]; then
  install -m 644 packaging/linux/looper-oss.png "$APPDIR/looper-oss.png"
elif command -v inkscape >/dev/null 2>&1; then
  inkscape -w 512 -h 512 packaging/linux/looper-oss.svg -o "$APPDIR/looper-oss.png" >/dev/null 2>&1
elif command -v convert >/dev/null 2>&1; then
  convert -background none -resize 512x512 packaging/linux/looper-oss.svg "$APPDIR/looper-oss.png"
else
  echo "   no icon renderer (inkscape/convert); shipping without an icon"
fi
[ -f "$APPDIR/looper-oss.png" ] && ln -sf looper-oss.png "$APPDIR/.DirIcon"

echo "== appimagetool"
TOOL="build/appimagetool"
if [ ! -x "$TOOL" ]; then
  URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
  if ! curl -fsSL "$URL" -o "$TOOL"; then
    echo "cannot download appimagetool. Install it, or place it at $TOOL" >&2
    echo "  $URL" >&2
    exit 1
  fi
  chmod +x "$TOOL"
fi

echo "== building $OUT/$NAME"
# --no-appstream: do not fail the build on a missing metainfo file
# GitHub's runtime download 504s often enough to matter; if we already have one
# (extracted from a previous AppImage), use it instead of fetching again.
RUNTIME="$TOOL.runtime"
RUNTIME_ARG=""
[ -f "$RUNTIME" ] && RUNTIME_ARG="--runtime-file $RUNTIME"
ARCH="$ARCH" VERSION="$VERSION" "$TOOL" --no-appstream $RUNTIME_ARG "$APPDIR" "$OUT/$NAME"

(cd "$OUT" && sha256sum "$NAME" > "$NAME.sha256")
echo
ls -lh "$OUT/$NAME"
echo "  run it:  chmod +x $OUT/$NAME && ./$OUT/$NAME"
echo "  on a system without FUSE:  ./$OUT/$NAME --appimage-extract-and-run"
