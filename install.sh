#!/usr/bin/env bash
# Looper OSS -- one-time setup for a source check-out.
#
#   ./install.sh        prepare .venv and the generated engine config
#
# Packaged builds (AppImage, Windows .exe) do not need this: they carry their
# own dependencies. This is only for running from the repository.
set -euo pipefail
cd "$(dirname "$0")"

echo "== checking prerequisites"
ok=1
if command -v sclang >/dev/null 2>&1; then
  echo "   sclang    $(command -v sclang)"
else
  echo "   sclang    MISSING -- install SuperCollider"
  case "$(uname -s)" in
    Linux)
      if command -v pacman >/dev/null 2>&1; then
        echo "             sudo pacman -S supercollider sc3-plugins pipewire-jack"
      elif command -v apt >/dev/null 2>&1; then
        echo "             sudo apt install supercollider sc3-plugins pipewire-jack"
      elif command -v dnf >/dev/null 2>&1; then
        echo "             sudo dnf install supercollider sc3-plugins pipewire-jack"
      fi ;;
  esac
  ok=0
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  && echo "   python    $(python3 -V)" \
  || { echo "   python    need 3.9 or newer"; ok=0; }

if ! command -v sclang >/dev/null 2>&1 && [ "${1:-}" != "--allow-missing-sclang" ]; then
  echo
  echo "SuperCollider is the audio engine, so it has to be installed first."
  echo "Run ./install.sh again once it is, or pass --allow-missing-sclang"
  echo "to prepare the Python side anyway."
  exit 1
fi

echo "== python environment"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip -q install --upgrade pip >/dev/null 2>&1 || true
.venv/bin/pip -q install -r requirements.txt

echo "== engine config from ui/config.json"
python3 bridge/write_config.py ui/config.json engine/_config.scd

echo
echo "ready. start it with:"
echo "   python3 looper_oss.py           # an app window"
echo "   python3 looper_oss.py --kiosk   # fullscreen, for a pedal"
echo
echo "to build the distributable app instead:  ./packaging/linux/build-appimage.sh"
exit $((1 - ok))
