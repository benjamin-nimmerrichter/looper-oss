#!/usr/bin/env bash
# Looper OSS launcher: config -> OSC bridge -> UI server -> SuperCollider engine.
#
#   ./run.sh              start everything, then open the UI yourself
#   ./run.sh --browser    start everything and open it as an app window
#   ./run.sh --kiosk      same, fullscreen (for the 8" pedal)
#   ./run.sh --setup      create .venv + regenerate engine config, then exit
#
set -euo pipefail
cd "$(dirname "$0")"

CFG="ui/config.json"
VENV=".venv"
PY="$VENV/bin/python"
LOGDIR="logs"
MODE="${1:-}"

setup() {
  echo "== python deps"
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || {
    echo "python 3.9 or newer is required (found $(python3 -V 2>&1))"
    exit 1
  }
  [ -d "$VENV" ] || python3 -m venv "$VENV"
  "$VENV/bin/pip" -q install --upgrade pip >/dev/null 2>&1 || true
  "$VENV/bin/pip" -q install -r requirements.txt
}

# ui/config.json is the single source of truth; render it for sclang
gen_config() { python3 bridge/write_config.py "$CFG" engine/_config.scd; }

port() { python3 -c "import json;print(json.load(open('$CFG'))['ui']['port'])"; }

rotate() {
  [ -f "$1" ] || return 0
  local size
  size=$(stat -c%s "$1" 2>/dev/null || echo 0)
  if [ "$size" -gt 5000000 ]; then mv -f "$1" "$1.1"; fi
}

case "$MODE" in
  --setup) setup; gen_config; echo "ready."; exit 0 ;;
esac

[ -x "$PY" ] || setup
mkdir -p "$LOGDIR"
gen_config

# leftovers from a crashed run: orphaned scsynth on :57110 (with a different
# SynthDef set) or a bridge still holding its ports
echo "== sweeping leftovers"
python3 scripts/sweep.py "$(port)" || true
sleep 0.4

if ! command -v sclang >/dev/null 2>&1; then
  echo "missing: sclang (SuperCollider engine)"
  echo "  sudo pacman -S supercollider sc3-plugins pipewire-jack   # see README"
  exit 1
fi

UIPORT="$(port)"
rotate "$LOGDIR/bridge.log"
rotate "$LOGDIR/engine.log"

"$PY" -m http.server "$UIPORT" --bind 127.0.0.1 --directory ui >/dev/null 2>&1 &
HTTP_PID=$!
"$PY" bridge/bridge.py >> "$LOGDIR/bridge.log" 2>&1 &
BRIDGE_PID=$!

cleanup() {
  kill "$HTTP_PID" "$BRIDGE_PID" 2>/dev/null || true
  if [ -n "${APP_PID:-}" ]; then kill "$APP_PID" 2>/dev/null || true; fi
  # sclang can die before its scsynth; a stray server breaks the next start
  pkill -x sclang 2>/dev/null || true
  pkill -x scsynth 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

sleep 0.8
URL="http://127.0.0.1:$UIPORT/"

if ! python3 - "$UIPORT" <<'PYCHECK'
import socket, sys
s = socket.socket()
s.settimeout(2)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PYCHECK
then
  echo "the UI server did not start on port $UIPORT (another process holding it?)"
  exit 1
fi

if [ "$MODE" = "--browser" ] || [ "$MODE" = "--kiosk" ]; then
  FLAGS="--app=$URL"
  [ "$MODE" = "--kiosk" ] && FLAGS="--kiosk --app=$URL"
  for b in chromium chromium-browser google-chrome-stable google-chrome brave firefox; do
    if command -v "$b" >/dev/null 2>&1; then
      # shellcheck disable=SC2086
      "$b" $FLAGS --incognito >/dev/null 2>&1 &
      APP_PID=$!
      break
    fi
  done
fi

echo
echo "  UI   $URL"
echo "  OSC  engine :9000   state -> bridge :9001 -> ws :8182"
echo "  logs $LOGDIR/"
echo "  ctrl-c to stop"
echo

# sclang quits on stdin EOF, so keep stdin open when we are not on a terminal.
# exit code 42 means "restart me" (setup -> audio device changed).
run_engine() {
  if [ -t 0 ]; then
    sclang engine/looper.scd 2>&1 | tee -a "$LOGDIR/engine.log"
    return "${PIPESTATUS[0]}"
  fi
  tail -f /dev/null | sclang engine/looper.scd 2>&1 | tee -a "$LOGDIR/engine.log"
  return "${PIPESTATUS[1]}"
}

set +e
while true; do
  run_engine
  rc=$?
  if [ "$rc" = "42" ]; then
    echo "== engine restart requested; re-reading $CFG"
    gen_config
    pkill -x scsynth 2>/dev/null || true
    # scsynth must be gone (and PipeWire must have released the JACK client)
    # before the next one starts, or it dies with "could not initialize audio"
    for _ in $(seq 1 20); do
      pgrep -x scsynth >/dev/null || break
      sleep 0.25
    done
    sleep 1.5
    continue
  fi
  break
done
exit "$rc"
