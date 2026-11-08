"""Looper OSS -- desktop application launcher.

Nothing here is a shell script: this process owns the whole stack (static UI
server, OSC bridge, SuperCollider engine) and the window, so the application is
one thing you double-click and one thing you quit.

`engine/looper.scd` resolves `engine/_config.scd` and `sessions/` against the
process working directory, and `bridge/bridge.py` resolves its root from its own
location -- so the launcher only has to `chdir` into a writable home and the rest
of the project works unmodified.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import importlib.util
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from typing import Any

from app import deps
from app.window import open_window

APP_NAME = "Looper OSS"
APP_SLUG = "looper-oss"


# --------------------------------------------------------------- locations --
def bundled_root() -> pathlib.Path:
    """Where the read-only program files are (repo, AppImage, PyInstaller)."""
    if getattr(sys, "frozen", False):  # PyInstaller
        return pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(sys.executable).parent))
    appdir = os.environ.get("APPDIR")  # AppImage
    if appdir and (pathlib.Path(appdir) / APP_SLUG / "engine").is_dir():
        return pathlib.Path(appdir) / APP_SLUG
    return pathlib.Path(__file__).resolve().parent.parent


def user_home() -> pathlib.Path:
    """Writable state: sessions, logs, config, generated engine config.

    A packaged app must not write next to its own binary (an AppImage mount is
    read-only), so packaged builds run from a per-user copy of the payload.
    """
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        base = pathlib.Path(local) if local else pathlib.Path.home() / "AppData" / "Local"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = pathlib.Path(xdg) if xdg else pathlib.Path.home() / ".local" / "share"
    return base / APP_SLUG


def read_text(path: pathlib.Path, default: str = "") -> str:
    """Read a small marker file, tolerating its absence."""
    try:
        return path.read_text().strip()
    except OSError as exc:
        if path.exists():  # present but unreadable is worth saying out loud
            print(f"warning: cannot read {path} ({exc})", file=sys.stderr)
        return default


def build_stamp(root: pathlib.Path) -> str:
    """Identify this build: a rebuilt AppImage must not keep running the old
    staged payload just because the version number did not change."""
    stamp = read_text(root / "BUILD")
    if stamp:
        return stamp
    # source tree, or a build without BUILD: fall back to the payload's own size
    try:
        size = sum(p.stat().st_size for p in (root / "ui").glob("*"))
        return f"src-{size}"
    except OSError as exc:
        print(f"warning: cannot size the ui payload ({exc})", file=sys.stderr)
        return "src-0"


def resolve_home(bundled: bool) -> pathlib.Path:
    """The directory the app runs from: the source tree, or a staged copy."""
    root = bundled_root()
    if not bundled:
        return root
    version = read_text(root / "VERSION", "0") or "0"
    stamp = build_stamp(root)
    target = user_home() / "app" / version
    runnable = ((target / "engine" / "looper.scd").is_file()
                and (target / "ui" / "app.js").is_file())
    if not runnable or read_text(target / "BUILD") != stamp:
        stage(root, target)
    return target


CONFIG_VERSION = 3   # bump when a shipped default changes meaning


def migrate_config(path: pathlib.Path) -> None:
    """Carry an older user config forward without touching their tuning.

    `stage()` now keeps `ui/config.json` across an update, which is the whole
    point -- a musician's bpm, track count, audio device and MIDI map must
    survive installing a new build. The other half of that promise: when a
    *default* was wrong and shipped, an old config keeps the wrong value
    forever unless it is deliberately migrated. Configs older than 3 are the
    ones that shipped `autoDub: 0`, which stopped a take at the bar instead of
    carrying it into an overdub.
    """
    try:
        cfg = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    try:
        version = int(cfg.get("version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version >= CONFIG_VERSION:
        return
    loop = cfg.get("loop")
    if isinstance(loop, dict):
        loop["autoDub"] = 1          # 0.1.0 shipped this off; the RC-505 carries on
    cfg["version"] = CONFIG_VERSION
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(cfg, indent=2) + "\n", newline="\n")


def stage(src: pathlib.Path, dst: pathlib.Path) -> None:
    """Copy the payload out of the read-only mount/onefile image, once."""
    keep = dst / "sessions"
    user_files = [dst / "ui" / "config.json", dst / "ui" / "config.json.bak"]
    tmp = dst.with_name(dst.name + ".staging")
    if tmp.exists():
        try:
            shutil.rmtree(tmp)
        except OSError as exc:
            fatal(f"cannot clear a half-finished install at {tmp}\n{exc}")
    try:
        shutil.copytree(
            src, tmp,
            ignore=shutil.ignore_patterns(".venv", "logs", "sessions", "dist", "build",
                                          "__pycache__", ".git", ".ruff_cache", "*.pyc"),
        )
    except (shutil.Error, OSError) as exc:
        fatal(f"cannot prepare the application directory\n{exc}\n\ntarget: {tmp}")
    # The staged home is per version, so a new version would start empty and the
    # player would think their loops were gone. On a fresh install, carry the
    # sessions and settings over from the newest earlier version.
    if not dst.exists():
        older = [d for d in sorted(dst.parent.glob("*"), reverse=True)
                 if d != dst and not d.name.endswith(".staging")
                 and (d / "engine" / "looper.scd").is_file()]
        for old in older:
            if (old / "sessions").is_dir() and not (tmp / "sessions").is_dir():
                with contextlib.suppress(OSError):
                    shutil.copytree(old / "sessions", tmp / "sessions")
            cfg_old = old / "ui" / "config.json"
            if cfg_old.is_file():
                with contextlib.suppress(OSError):
                    shutil.copy2(cfg_old, tmp / "ui" / "config.json")
                break
    if keep.is_dir():  # never lose the user's music
        with contextlib.suppress(OSError):
            shutil.copytree(keep, tmp / "sessions", dirs_exist_ok=True)
    # ... nor their settings. A re-stage is an update, not a reset: bpm, track
    # count, audio device and MIDI map all live in this file.
    for path in user_files:
        if path.is_file():
            with contextlib.suppress(OSError):
                shutil.copy2(path, tmp / "ui" / path.name)
    migrate_config(tmp / "ui" / "config.json")
    if dst.exists():
        try:
            shutil.rmtree(dst)
        except OSError as exc:
            fatal(f"cannot replace the previous install at {dst}\n{exc}")
    tmp.rename(dst)


def fatal(message: str) -> None:
    """Fail with something a musician can act on, not a traceback."""
    print(message, file=sys.stderr)
    with contextlib.suppress(Exception):
        import tkinter.messagebox as mb  # noqa: PLC0415 - last resort, no GTK needed
        mb.showerror(APP_NAME, message)
    sys.exit(1)


# ------------------------------------------------------------------ helpers --
def load_config(home: pathlib.Path) -> dict[str, Any]:
    path = home / "ui" / "config.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        fatal(f"cannot read {path}\n{exc}\n\nThe file must be valid JSON.")
        return {}


def port_free(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) != 0


def log_dir(home: pathlib.Path) -> pathlib.Path:
    d = home / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def capture_output(home: pathlib.Path) -> None:
    """A windowed app has no console, so anything that goes wrong before a
    window exists would vanish. Keep the launcher's own output in app.log;
    an unhandled traceback then lands there too, because stderr points at it."""
    try:
        logs = log_dir(home)
        handle = (logs / "app.log").open("a", buffering=1, encoding="utf-8",
                                         errors="replace")
    except OSError as exc:
        print(f"warning: cannot open logs/app.log ({exc})", file=sys.stderr)
        return
    sys.stdout = handle
    sys.stderr = handle


def rotate(path: pathlib.Path, limit: int = 5_000_000) -> None:
    with contextlib.suppress(OSError):
        if path.is_file() and path.stat().st_size > limit:
            path.replace(path.with_suffix(path.suffix + ".1"))


def load_module(name: str, path: pathlib.Path) -> Any:
    """Import a project file by location (bridge.py and write_config.py are not
    installed packages, and the frozen build has no normal import path to them)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        fatal(f"this build is missing {path.name}")
        raise SystemExit(1)  # fatal() exits; this is here for the type checker
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_sclang() -> str | None:
    # An explicit override wins everywhere: it lets Wine (or an install in an
    # unusual place) point at the engine without teaching find_sclang about it.
    override = os.environ.get("SCLANG")
    if override and pathlib.Path(override).is_file():
        return override
    # On Windows never resolve a bare "sclang": PATH there can hold a foreign
    # binary. Inside Wine it found the HOST's /usr/bin/sclang and spawned that
    # silently, so the app booted an engine it could not talk to.
    found = shutil.which("sclang.exe" if os.name == "nt" else "sclang")
    if found:
        return found
    candidates = [] if os.name == "nt" else [
        "/usr/bin/sclang", "/usr/local/bin/sclang",
        "/Applications/SuperCollider.app/Contents/MacOS/sclang"]
    if os.name == "nt":
        # Windows environment variables are case-insensitive, so the upper-case
        # spellings work on a real machine and keep linters happy here.
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                candidates.append(str(pathlib.Path(base) / "SuperCollider" / "sclang.exe"))
    for candidate in candidates:
        if pathlib.Path(candidate).is_file():
            return candidate
    return None


def sweep_stale(home: pathlib.Path) -> None:
    """Kill a crashed run's engine so the new one can open the audio device."""
    del home  # only here for symmetry with the documented behaviour
    if os.name == "nt":
        for name in ("scsynth.exe", "sclang.exe"):
            subprocess.run(["taskkill", "/F", "/IM", name], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    procs = pathlib.Path("/proc")
    if not procs.is_dir():
        return
    for pid in procs.iterdir():
        if not pid.name.isdigit():
            continue
        try:
            name = (pid / "comm").read_text().strip()
        except OSError:
            continue
        if name in ("scsynth", "sclang"):
            with contextlib.suppress(OSError):
                os.kill(int(pid.name), signal.SIGTERM)


# ------------------------------------------------------------------- server --
def start_static_server(root: pathlib.Path, port: int) -> None:
    """Serve ui/ on 127.0.0.1; an update must take effect on the next reload."""
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer  # noqa: PLC0415

    class Handler(SimpleHTTPRequestHandler):
        """No cache (a rebuilt UI must apply at once), no request spam in logs."""

        def end_headers(self) -> None:
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - base signature
            return

    bound = functools.partial(Handler, directory=str(root / "ui"))
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), bound)
    except OSError as exc:
        fatal(f"cannot serve the interface on port {port}\n{exc}\n\n"
              f"Something else is using that port. Close it, or change "
              f'"ui" -> "port" in ui/config.json.')
        return
    threading.Thread(target=httpd.serve_forever, name="ui-http", daemon=True).start()


def start_bridge(home: pathlib.Path, logfile: pathlib.Path) -> None:
    """Run the OSC<->WebSocket bridge in its own thread and event loop."""
    import asyncio  # noqa: PLC0415

    # bridge.py imports its sibling write_config by plain name, which works when
    # it is run as a script; loading it by location needs the same path entry.
    bridge_dir = str(home / "bridge")
    if bridge_dir not in sys.path:
        sys.path.insert(0, bridge_dir)
    # tells bridge.py not to os._exit() the process out from under the window
    os.environ["LOOPER_OSS_IN_PROCESS"] = "1"
    try:
        bridge = load_module("looper_bridge", home / "bridge" / "bridge.py")
    except SystemExit:  # bridge.py reports a missing dependency this way
        fatal("the OSC bridge cannot start: a required Python package is missing.\n\n"
              "Reinstall Looper OSS, or run ./install.sh once from a source check-out.")
        return

    # In-process, stdout belongs to the app, so route the bridge's own log() to
    # logs/bridge.log; the real log() also prints, which helps when run by hand.
    handle = logfile.open("a", buffering=1)
    print_too = bridge.log

    def tee(*args: Any) -> None:
        message = "bridge: " + " ".join(str(a) for a in args)
        handle.write(message + "\n")
        print_too(*args)

    bridge.log = tee

    def run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bridge.main())
        except (asyncio.CancelledError, RuntimeError):
            pass
        except Exception:  # noqa: BLE001 - a dead bridge must not kill the app
            traceback.print_exc(file=handle)
        finally:
            handle.flush()

    threading.Thread(target=run, name="bridge", daemon=True).start()


# ------------------------------------------------------------------- engine --
def resume_drift(last_wall: float, last_mono: float,
                 wall: float, mono: float) -> float:
    """Seconds the machine spent asleep between two samples.

    CLOCK_MONOTONIC stops during suspend, CLOCK_REALTIME keeps running, so
    subtracting one elapsed time from the other leaves exactly the sleep
    duration and ~0 for an ordinary tick. Windows' QPC behaves like
    CLOCK_MONOTONIC, so the same test works there.
    """
    return (wall - last_wall) - (mono - last_mono)


class Engine:
    """SuperCollider, supervised: exit code 42 means 'restart me'."""

    def __init__(self, home: pathlib.Path, sclang: str, log: pathlib.Path):
        self.home, self.sclang, self.log = home, sclang, log
        self.proc: subprocess.Popen[bytes] | None = None
        self.restarts = 0

    def start(self) -> None:
        with self.log.open("a", buffering=1) as out:
            self.proc = subprocess.Popen(
                [self.sclang, str(self.home / "engine" / "looper.scd")],
                cwd=str(self.home),
                stdin=subprocess.PIPE,  # sclang quits on EOF, so hold it open
                stdout=out, stderr=subprocess.STDOUT,
            )

    def poll(self) -> int | None:
        return None if self.proc is None else self.proc.poll()

    def restart(self, delay: float = 2.0, count: bool = True) -> None:
        if count:
            self.restarts += 1
        self.stop(force=True)
        time.sleep(delay)  # let PipeWire/WASAPI release the device first
        self.start()

    def stop(self, force: bool = False) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.terminate()
            try:
                proc.wait(timeout=1.5 if force else 4.0)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    proc.kill()
        if os.name != "nt":
            # sclang can die before its scsynth; a stray server breaks the next start
            for name in ("sclang", "scsynth"):
                subprocess.run(["pkill", "-x", name], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # a stray scsynth.exe keeps the WASAPI device open, so the next start
            # dies with "could not initialize audio" (invariants 19 and 22). This
            # is the same reason the POSIX branch pkills, not an extra nicety.
            sweep_stale(self.home)


# --------------------------------------------------------------------- main --
def reexec_in_venv(argv: list[str]) -> int | None:
    """Source check-outs keep their dependencies in .venv, while a double-click
    may hand us a bare system python. Re-run under the venv instead of asking
    anyone to activate it. Packaged builds bundle their deps and skip this."""
    if getattr(sys, "frozen", False) or os.environ.get("APPDIR"):
        return None
    if os.environ.get("LOOPER_OSS_REEXEC"):
        return None
    venv = pathlib.Path(__file__).resolve().parent.parent / ".venv"
    exe = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not exe.is_file():
        return None
    # Compare sys.prefix, never the resolved executable: .venv/bin/python is a
    # symlink to the system python, so resolve() compares equal and the venv
    # would be skipped -- which is exactly how the deps go missing.
    try:
        if pathlib.Path(sys.prefix).resolve() == venv.resolve():
            return None
    except OSError:
        return None
    env = dict(os.environ, LOOPER_OSS_REEXEC="1")
    return subprocess.run([str(exe), "-m", "app.main", *argv], env=env).returncode


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--no-reexec" not in argv:
        rc = reexec_in_venv(argv)
        if rc is not None:
            return rc

    ap = argparse.ArgumentParser(prog=APP_SLUG, description=f"{APP_NAME} launcher")
    ap.add_argument("--window", choices=["auto", "native", "browser", "app", "none"],
                    default="auto", help="how to show the UI (default: auto)")
    ap.add_argument("--kiosk", action="store_true", help="fullscreen window")
    ap.add_argument("--no-engine", action="store_true", help="UI + bridge only (tests)")
    ap.add_argument("--check", action="store_true", help="report what would happen, then exit")
    ap.add_argument("--no-reexec", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--deps", action="store_true",
                    help="report the system dependencies and exit")
    ap.add_argument("--install-deps", action="store_true",
                    help="install the missing system dependencies and exit")
    args = ap.parse_args(argv)

    # These two print to the terminal, so they must run before capture_output()
    # redirects stdout into logs/app.log.
    if args.deps:
        print(deps.summary())
        return 0 if not deps.missing_keys() else 1
    if args.install_deps:
        return deps.install()

    bundled = getattr(sys, "frozen", False) or bool(os.environ.get("APPDIR"))
    home = resolve_home(bundled)
    os.chdir(home)
    # from here on, everything leaves a trace in logs/app.log
    capture_output(home)
    print(f"--- {APP_NAME} {APP_SLUG} starting: home={home}")

    try:
        version = (home / "VERSION").read_text().strip()
    except OSError:
        version = "0.0.0"

    # Say it once, in the log, before anything tries to open a window: a missing
    # library that only shows up as "no window appeared" is the worst kind.
    gap = deps.hint()
    if gap:
        print(gap)

    cfg = load_config(home)
    ui_cfg = cfg.get("ui", {})
    port = int(ui_cfg.get("port", 8181))
    ws = int(ui_cfg.get("wsPort", 8182))
    url = f"http://127.0.0.1:{port}/"
    sclang = None if args.no_engine else find_sclang()

    if args.check:
        print(f"{APP_NAME} {version}")
        print(f"  home    {home}{' (staged)' if bundled else ''}")
        print(f"  ui      {url}   ws :{ws}")
        print(f"  sclang  {sclang or 'NOT FOUND'}")
        print(f"  window  {args.window}{' kiosk' if args.kiosk else ''}")
        missing = deps.missing_keys()
        print(f"  deps    {'all present' if not missing else ', '.join(missing) + ' missing'}")
        return 0 if sclang or args.no_engine else 1

    if not port_free(port):
        # a second double-click should raise the running app, not fail on ports
        open_window(url, kiosk=args.kiosk, mode=args.window)
        return 0

    logs = log_dir(home)
    for name in ("bridge.log", "engine.log", "app.log"):
        rotate(logs / name)

    if not args.no_engine and not sclang:
        how = ("Run Looper OSS with --install-deps and it will install them "
               "for you." if os.environ.get("LOOPER_OSS_PACKAGED") else
               "Install them with:  python3 looper_oss.py --install-deps")
        open_window(url, kiosk=args.kiosk, mode=args.window,
                    error="SuperCollider was not found.\n\n"
                          "Looper OSS needs sclang and scsynth. " + how)
        return 1

    write_config = load_module("looper_write_config", home / "bridge" / "write_config.py")
    write_config.write_from(home / "ui" / "config.json", home / "engine" / "_config.scd")

    start_static_server(home, port)
    start_bridge(home, logs / "bridge.log")

    engine: Engine | None = None
    if sclang:
        sweep_stale(home)
        engine = Engine(home, sclang, logs / "engine.log")
        # the bridge reads the engine's boot log to report which audio device was
        # actually opened where there is no PipeWire graph to ask (Windows)
        try:
            import bridge.bridge as _bridge
            _bridge.ENGINE_LOG = logs / "engine.log"
        except ImportError:                       # pragma: no cover - packaged path
            pass
        engine.start()

    stop = threading.Event()

    # Closing the window runs the cleanup below; a logout/shutdown/crash-stop
    # sends a signal instead, and without a handler that skips cleanup and
    # leaves scsynth holding the audio device.
    def _signal_quit(signum: int, _frame: Any) -> None:
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, _signal_quit)

    def watchdog() -> None:
        last_wall, last_mono = time.time(), time.monotonic()
        # Test hook: pretend the machine has just slept N seconds, so the resume
        # path can be exercised without actually suspending. Shifting the wall
        # baseline backwards makes the first tick report a drift of N. Never set
        # in normal use; AGENTS.md documents the manual probe.
        try:
            sim = float(os.environ.get("LOOPER_OSS_RESUME_SIM", "0") or 0)
        except ValueError:                     # a bad value must not kill the watcher
            sim = 0.0
        if sim > 0:
            last_wall -= sim
        while not stop.is_set():
            time.sleep(0.5)
            if engine is None:
                continue

            # A suspend freezes CLOCK_MONOTONIC but not CLOCK_REALTIME, so the
            # two clocks drift apart by exactly the time the machine slept. It
            # is the resume signal we get for free -- no D-Bus client, no new
            # dependency, and Windows' QPC behaves the same way.
            #
            # Audio does not survive it: the device is torn down and rebuilt,
            # and scsynth keeps running with no output. The process is alive,
            # so the exit-code watchdog below never fires; without this the
            # app looks fine and simply goes silent after a lid close.
            wall, mono = time.time(), time.monotonic()
            drift = resume_drift(last_wall, last_mono, wall, mono)
            last_wall, last_mono = wall, mono
            if drift > 5.0 and engine.poll() is None:
                # 5 s: a real sleep, not an NTP step (those are slewed). Give
                # PipeWire a moment to rebuild its graph before scsynth asks
                # for the device again.
                print(f"--- resume detected ({drift:.0f}s asleep): restarting the engine")
                engine.restart(delay=4.0, count=False)
                last_wall, last_mono = time.time(), time.monotonic()
                continue

            code = engine.poll()
            if code is None:
                continue
            if code == 42 or (code != 0 and engine.restarts < 3):
                engine.restart()

    threading.Thread(target=watchdog, name="watchdog", daemon=True).start()

    code = 0
    try:
        open_window(url, kiosk=args.kiosk, mode=args.window)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    finally:
        stop.set()
        if engine:
            engine.stop()
        time.sleep(0.2)
        sweep_stale(home)
    return code


if __name__ == "__main__":
    sys.exit(main())
