#!/usr/bin/env python3
"""Regenerate the documentation screenshots — automatically.

    python3 scripts/shots.py             # start the stack if needed, shoot, stop it
    python3 scripts/shots.py --keep      # leave a stack you started running
    python3 scripts/shots.py main loop   # only these shots

Why WebKit and not a headless browser: in a container without a GPU, Chromium
rasterises the settings drawer's text as the wrong glyphs — every label comes
out gibberish — while the same page in WebKit (the engine the app itself draws
with) is correct. A screenshot tool whose output cannot be trusted is worse than
no tool, so this one asks WebKit for the pixels through its own snapshot API.

Needs PyGObject and WebKit2 for *some* interpreter on the box, the same ones the
app window uses; the venv python usually lacks them, so it looks for a system
python that has them.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import pathlib
import socket
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"
# The bridge serves the page and the socket on one origin; 8182 is that port.
UI_PORT = int(os.environ.get("UI_PORT", "8182"))
URL = os.environ.get("UI_URL", f"http://127.0.0.1:{UI_PORT}/")
SIZE = (1280, 800)

TAP = ("el => el.dispatchEvent(new PointerEvent('pointerup', "
       "{bubbles: true, cancelable: true}))")

TABS = ("audio", "loop", "clock", "ui", "midi", "session", "505")

# name -> JavaScript that puts the page into the state to photograph
SHOTS: list[tuple[str, str]] = [("ui-main", "true")]
for tab in TABS:
    SHOTS.append((f"setup-{tab}", f"""
        const tap = {TAP};
        const setupBtn = [...document.querySelectorAll('#transport button')]
            .find(b => b.textContent.trim().toLowerCase() === 'setup');
        if (setupBtn) tap(setupBtn);
        setTimeout(() => {{
            const t = document.querySelector('#settings .tabs button[data-tab="{tab}"]');
            if (t) tap(t);
        }}, 700);
        true
    """))
SHOTS.append(("ui-fx", f"""
    const tap = {TAP};
    const track = document.querySelector('.track');
    const fx = track && [...track.querySelectorAll('button')]
        .find(b => b.textContent.trim() === 'fx');
    if (fx) tap(fx);
    true
"""))

# Runs under an interpreter that has gi. argv: url, js, out, width, height
SNAPSHOT = r'''
import sys, gi
gi.require_version("Gtk", "3.0")
try:
    gi.require_version("WebKit2", "4.1")
except ValueError:
    gi.require_version("WebKit2", "4.0")
from gi.repository import Gtk, WebKit2, GLib

url, js, out = sys.argv[1], sys.argv[2], sys.argv[3]
width, height = int(sys.argv[4]), int(sys.argv[5])

win = Gtk.Window()
win.set_default_size(width, height)
win.set_decorated(False)
view = WebKit2.WebView()
win.add(view)
win.show_all()

def done(v, res, _):
    surface = v.get_snapshot_finish(res)
    surface.write_to_png(out)
    Gtk.main_quit()

def shoot(v):
    v.get_snapshot(WebKit2.SnapshotRegion.VISIBLE,
                   WebKit2.SnapshotOptions.NONE, None, done, None)
    return False

def loaded(v, event):
    if event == WebKit2.LoadEvent.FINISHED:
        GLib.timeout_add(3500, lambda: (v.run_javascript(js, None, None, None), False)[1])
        GLib.timeout_add(12000, lambda: shoot(v))
    return False

view.connect("load-changed", loaded)
view.load_uri(url)
GLib.timeout_add(30000, lambda: (print("timeout", file=sys.stderr), Gtk.main_quit(), False)[2])
Gtk.main()
'''


def gi_python() -> str | None:
    """An interpreter that can import gi + WebKit2 (the venv usually cannot)."""
    candidates = [os.environ.get("PYGI_PYTHON"), "/usr/bin/python3", sys.executable]
    for cand in candidates:
        if not cand or not pathlib.Path(cand).exists():
            continue
        try:
            ok = subprocess.run(
                [cand, "-c", "import gi; gi.require_version('Gtk','3.0')"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
            ).returncode == 0
        except (OSError, subprocess.SubprocessError):
            continue
        if ok:
            return cand
    return None


def port_open(port: int) -> bool:
    with contextlib.suppress(OSError), \
            socket.create_connection(("127.0.0.1", port), timeout=1):
        return True
    return False


def wait_for_port(port: int, seconds: float = 45.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if port_open(port):
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="regenerate docs/img screenshots")
    ap.add_argument("only", nargs="*", help="shot names to take (default: all)")
    ap.add_argument("--keep", action="store_true", help="do not stop a stack we started")
    args = ap.parse_args()

    py = gi_python()
    if not py:
        print("no interpreter with PyGObject + WebKit2 found (set PYGI_PYTHON)",
              file=sys.stderr)
        return 1

    started: subprocess.Popen[bytes] | None = None
    if not port_open(UI_PORT):
        print("starting the stack…")
        env = dict(os.environ, LOOPER_OSS_KEEP_ALIVE="1")
        started = subprocess.Popen(
            [sys.executable, "-m", "app.main", "--window", "none"],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        if not wait_for_port(UI_PORT):
            print("the UI did not come up", file=sys.stderr)
            started.terminate()
            return 1

    chosen = [s for s in SHOTS if not args.only or s[0] in args.only]
    OUT.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name, js in chosen:
        path = OUT / f"{name}.png"
        done = subprocess.run([py, "-c", SNAPSHOT, URL, js, str(path),
                               str(SIZE[0]), str(SIZE[1])])
        if done.returncode == 0 and path.is_file() and path.stat().st_size > 0:
            print(f"  docs/img/{name}.png  {path.stat().st_size // 1024} kB")
        else:
            print(f"  FAILED {name}", file=sys.stderr)
            failed += 1

    if started is not None and not args.keep:
        started.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            started.wait(timeout=10)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "sweep.py")],
                       check=False, stdout=subprocess.DEVNULL)

    print(f"\n{len(chosen) - failed}/{len(chosen)} screenshots written to docs/img/")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
