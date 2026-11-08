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

import contextlib
import importlib
import os
import shutil
import subprocess
import sys
import time

from app.deps import gi_paths


def _html_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("\n", "<br>"))


ERROR_PAGE = """<!doctype html><meta charset="utf-8">
<style>
  html,body{{height:100%;margin:0;background:#0b0f14;color:#e6e6e6;
    font:16px/1.5 system-ui,sans-serif;display:flex;align-items:center;
    justify-content:center;text-align:center}}
  div{{max-width:34rem;padding:2rem}}
  .app{{font-size:2.4rem;font-weight:700;letter-spacing:.16em;
    text-transform:uppercase;color:#8b96a5;margin:0 0 1.2rem}}
  h1{{font-size:1.3rem;margin:0 0 1rem;color:#e05a4f}}
  p{{color:#9fb0c0;margin:0}}
  code{{background:#161d26;padding:.15rem .35rem;border-radius:.25rem}}
</style>
<div><div class="app">Looper OSS</div><h1>cannot start yet</h1><p>{message}</p></div>
"""


def _native(url: str, kiosk: bool, error: str | None) -> bool:
    """GTK + WebKit2. Returns False if GTK/WebKit is not usable here.

    PyGObject is a *system* package (it belongs to the desktop, not to this
    project), so it is imported dynamically: a frozen build must not treat it as
    a bundling dependency, and it is genuinely optional.
    """
    # A source check-out runs inside a virtualenv, and venvs are created with
    # include-system-site-packages = false: the distro's PyGObject is invisible
    # there, so `import gi` failed and the app silently fell back to a browser
    # even when python3-gi was installed. Put the distro's site-packages back
    # before trying (the packaged app gets the same from AppRun).
    for extra in gi_paths():
        if extra not in sys.path:
            sys.path.append(extra)

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

    # Building the window may fail on a host without a usable WebKit -- that is
    # the only thing worth falling back for. Once the window exists and the loop
    # is running, a later exception must NOT silently hand off to a browser that
    # probably is not installed, leaving the app hidden and holding the ports.
    try:
        win = Gtk.Window(title="Looper OSS")
        win.set_default_size(1280, 800)
        with contextlib.suppress(Exception):  # deprecated in GTK3, fine to miss
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
        # show_all() only maps the window: the compositor still decides where it
        # goes and whether it may take focus, so starting the app while a
        # maximised browser is in front used to put Looper OSS *behind* it --
        # running, ui connected, engine ready, and invisible. present() asks to
        # raise and activate, which is what a freshly launched app owes the
        # person who just clicked it.
        win.present()
    except Exception as exc:  # noqa: BLE001 - no window toolkit, try the next mode
        print(f"native window failed: {exc}", file=sys.stderr)
        return False

    Gtk.main()  # blocks until the window is closed; exceptions here are real
    return True


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
    """Start an app window and return at once: the caller owns the lifetime."""
    exe = _browser_exe()
    if not exe:
        return False
    # --no-proxy-server: a system proxy / PAC / VPN client will happily try to
    # proxy even 127.0.0.1, and then the page never loads while the app's own
    # request to the same URL succeeds -- which is precisely the reported symptom.
    flags = ["--app=" + url, "--window-size=1280,800", "--no-proxy-server",
             "--proxy-bypass-list=127.0.0.1;localhost"]
    if kiosk:
        flags.append("--start-fullscreen")
    try:
        subprocess.Popen([exe, *flags], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    # NOT proc.wait(): on Windows msedge/chrome hands the URL to the instance that
    # is already running and exits immediately, so waiting on this process means
    # "the window closed" a fraction of a second after opening it -- the app then
    # tore itself down, and the fallback chain opened more windows on the way out.
    return True


def _browser_tab(url: str) -> bool:
    """Open the UI in the default browser. Returns True when a tab was opened.

    Does not block: the browser process is not a proxy for the user's window, so
    the caller waits on the bridge's UI clients instead.
    """
    import webbrowser  # noqa: PLC0415

    opened = False
    try:
        opened = bool(webbrowser.open(url))
    except (OSError, webbrowser.Error) as exc:
        print(f"cannot open a browser: {exc}", file=sys.stderr)
    if not opened:
        print("no window and no browser available; stopping.", file=sys.stderr)
        return False
    print(f"Looper OSS is running at {url}  (close it to quit)", flush=True)
    return True


# public: main.py falls back to this when an --app window never appears
browser_tab = _browser_tab


def open_window(url: str, kiosk: bool = False, mode: str = "auto",
                error: str | None = None) -> str:
    """Show the UI.

    Returns "closed" when this function waited for the window and it is now gone,
    or "launched" when a window was started and the *caller* owns the lifetime --
    a browser process exiting is not the user closing the window.
    """
    # Say which path was taken: a silent fallback from "native" to a browser is
    # exactly the kind of failure that looks like "I click it and nothing happens".
    print(f"window: mode={mode} kiosk={kiosk} error={bool(error)}", flush=True)
    if mode == "none":
        if error:
            print(error, file=sys.stderr)
            return "closed"
        print(f"Looper OSS running at {url}  (Ctrl-C to quit)", flush=True)
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            return "closed"

    if mode in ("auto", "native"):
        if _native(url, kiosk, error):
            print("window: native (GTK/WebKit2) closed", flush=True)
            return "closed"
        print("window: native unavailable (no PyGObject/WebKit for this python)", flush=True)
    if error:  # no window toolkit at all: say it where someone can see it
        print(error, file=sys.stderr)
        return "closed"
    if mode in ("auto", "app"):
        if _app_window(url, kiosk):
            print("window: chromium/edge --app started", flush=True)
            return "launched"
        print("window: no chromium/edge for an app window", flush=True)
    if _browser_tab(url):
        print("window: default browser tab opened", flush=True)
        return "launched"
    return "closed"
