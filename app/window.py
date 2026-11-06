"""The window Looper OSS lives in.

Three strategies, best first, none of them a terminal:

1. **native** - GTK + WebKit2 on Linux. No extra dependency: PyGObject and
   WebKit2 are part of any GTK desktop, and this gives a real application
   window, a title, fullscreen for the pedal, and a proper close event.
2. **app** - Chromium/Edge `--app=` window. This is what Windows uses: Edge is
   present on every Windows 10/11 machine, and `--app` hides the browser chrome
   so it reads as an application, not a tab.
3. **browser** - last resort, the default browser on a normal tab.

If an error message is passed instead of a UI to show, a plain page explains it
in the window rather than on stdout, because a musician may never see stdout.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import time


def _html_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("\n", "<br>"))


ERROR_PAGE = """<!doctype html><meta charset="utf-8">
<style>
  html,body{{height:100%;margin:0;background:#0b0f14;color:#e6e6e6;
    font:16px/1.5 system-ui,sans-serif;display:flex;align-items:center;
    justify-content:center;text-align:center}}
  div{{max-width:34rem;padding:2rem}}
  h1{{font-size:1.3rem;margin:0 0 1rem;color:#e05a4f}}
  p{{color:#9fb0c0;margin:0}}
  code{{background:#161d26;padding:.15rem .35rem;border-radius:.25rem}}
</style>
<div><h1>Looper OSS cannot start yet</h1><p>{message}</p></div>
"""


def _native(url: str, kiosk: bool, error: str | None) -> bool:
    """GTK + WebKit2. Returns False if GTK/WebKit is not usable here.

    PyGObject is a *system* package (it belongs to the desktop, not to this
    project), so it is imported dynamically: a frozen build must not treat it as
    a bundling dependency, and it is genuinely optional.
    """
    try:
        gi = importlib.import_module("gi")
        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("WebKit2", "4.1")
        except ValueError:
            gi.require_version("WebKit2", "4.0")
        Gtk = importlib.import_module("gi.repository.Gtk")
        WebKit2 = importlib.import_module("gi.repository.WebKit2")
    except (ImportError, ValueError):
        return False

    try:
        win = Gtk.Window(title="Looper OSS")
        win.set_default_size(1280, 800)
        win.set_wmclass("looper-oss", "LooperOSS")
        if kiosk:
            win.fullscreen()
        view = WebKit2.WebView()
        settings = view.get_settings()
        for prop, value in (("enable-page-cache", False),
                            ("enable-developer-extras", False),
                            ("enable-write-console-messages-to-stdout", False)):
            if settings.find_property(prop) is not None:
                setattr(settings, prop.replace("-", "_"), value)
        if error:
            view.load_html(ERROR_PAGE.format(message=_html_escape(error)), None)
        else:
            view.load_uri(url)
        win.add(view)
        win.connect("destroy", Gtk.main_quit)
        view.connect("close", lambda *_: Gtk.main_quit())
        win.show_all()
        Gtk.main()
        return True
    except Exception:  # noqa: BLE001 - any GTK failure falls through to the next mode
        return False


def _browser_exe() -> str | None:
    if os.name == "nt":
        candidates = []
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
                candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
        for c in candidates:
            if os.path.isfile(c):
                return c
    for name in ("chromium", "chromium-browser", "google-chrome-stable",
                 "google-chrome", "microsoft-edge", "brave"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _app_window(url: str, kiosk: bool) -> bool:
    exe = _browser_exe()
    if not exe:
        return False
    flags = ["--app=" + url, "--window-size=1280,800"]
    if kiosk:
        flags.append("--start-fullscreen")
    try:
        proc = subprocess.Popen([exe, *flags], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    return True


def _browser_tab(url: str) -> None:
    import webbrowser  # noqa: PLC0415
    webbrowser.open(url)
    print(f"Looper OSS is running at {url}  (close this window to quit)", flush=True)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass


def open_window(url: str, kiosk: bool = False, mode: str = "auto",
                error: str | None = None) -> None:
    """Show the UI (or an error page) and block until the window is closed."""
    if mode == "none":
        if error:
            print(error, file=sys.stderr)
            return
        print(f"Looper OSS running at {url}  (Ctrl-C to quit)", flush=True)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            return

    if mode in ("auto", "native") and _native(url, kiosk, error):
        return
    if error:  # no window toolkit at all: say it where someone can see it
        print(error, file=sys.stderr)
        return
    if mode in ("auto", "app") and _app_window(url, kiosk):
        return
    _browser_tab(url)
