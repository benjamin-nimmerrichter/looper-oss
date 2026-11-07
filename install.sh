#!/usr/bin/env bash
# Looper OSS -- one-time setup for a source check-out.
#
#   ./install.sh                 venv + engine config, report system dependencies
#   ./install.sh --install-deps  ... and install the missing ones in one step
#
# Packaged builds (AppImage, Windows .exe) do not need this: they carry their
# own Python. This is only for running from the repository.
set -euo pipefail
cd "$(dirname "$0")"

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || {
  echo "Looper OSS needs Python 3.9 or newer." >&2
  exit 1
}

echo "== python environment"
# --system-site-packages matters: PyGObject (gi) is a *system* package built
# against the distro's python. A plain venv cannot see it, the native window
# then fails to import, and the app silently falls back to a browser tab.
[ -d .venv ] || python3 -m venv --system-site-packages .venv
.venv/bin/pip -q install --upgrade pip >/dev/null 2>&1 || true
.venv/bin/pip -q install -r requirements.txt

echo "== system dependencies"
rc=0
if .venv/bin/python -m app.main --deps; then
  :
elif [ "${1:-}" = "--install-deps" ]; then
  .venv/bin/python -m app.main --install-deps || rc=1
  .venv/bin/python -m app.main --deps || rc=1
else
  rc=1
  echo
  echo "Install the missing ones in a single step (one password prompt):"
  echo "   ./install.sh --install-deps"
fi

echo "== engine config from ui/config.json"
python3 bridge/write_config.py ui/config.json engine/_config.scd

echo
echo "ready. start it with:"
echo "   python3 looper_oss.py           # an app window"
echo "   python3 looper_oss.py --kiosk   # fullscreen, for a pedal"
echo
echo "to build the distributable app instead:  ./packaging/linux/build-appimage.sh"
exit "$rc"
