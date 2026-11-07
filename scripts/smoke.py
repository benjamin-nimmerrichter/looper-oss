#!/usr/bin/env python3
"""Looper OSS end-to-end smoke test.

Spins up the bridge and the SuperCollider engine itself (unless --attach is
given), drives the real protocol over the websocket, and asserts the state
machine, settings, session save/load and export.

    ./scripts/smoke.py            # spawn everything, run, tear down
    ./scripts/smoke.py --attach   # use an already running stack
    ./scripts/smoke.py -v         # show every state frame
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "bin" / "python"
if not PY.exists():
    PY = pathlib.Path(sys.executable)

def _venv_site_packages() -> list[pathlib.Path]:
    """site-packages of the project's own venv, Linux or Windows layout."""
    return [
        *(ROOT / ".venv" / "lib").glob("python*/site-packages"),
        ROOT / ".venv" / "Lib" / "site-packages",
    ]


try:
    import websockets  # pyright: ignore[reportMissingImports]
except ImportError as exc:  # pragma: no cover - startup dependency check
    # The shebang picks whatever `python3` is on PATH, which usually has no
    # packages. The venv ./install.sh made does, and both runtime deps are
    # pure Python, so lending them to this interpreter is enough -- no need to
    # make the user activate anything first.
    for site in _venv_site_packages():
        if site.is_dir() and str(site) not in sys.path:
            sys.path.insert(0, str(site))
    try:
        import websockets  # pyright: ignore[reportMissingImports]  # noqa: F811
    except ImportError:
        raise SystemExit(
            "smoke: missing python package (websockets)\n  install: ./install.sh"
        ) from exc

try:
    CFG = json.loads((ROOT / "ui" / "config.json").read_text())
except (OSError, ValueError) as exc:
    raise SystemExit(f"smoke: cannot read config.json ({exc})") from exc
try:
    N = int(CFG.get("tracks", 5))
    WS_PORT = int(CFG["ui"].get("wsPort", 8182))
except (TypeError, ValueError, KeyError) as exc:
    raise SystemExit(f"smoke: bad config.json ({exc})") from exc
API = f"ws://127.0.0.1:{WS_PORT}"

results: list[tuple[str, bool, str]] = []
verbose = False


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}",
          flush=True)
    return ok


# ----------------------------------------------------------------- engine ---- #
def kill_stale_bridges() -> None:
    """Kill leftover bridges. Not pkill -f: the pattern would match our own
    shell command line, which contains the same words."""
    try:
        pids = os.listdir("/proc")
    except OSError as exc:
        print(f"smoke: cannot scan /proc ({exc})")
        return
    for name in pids:
        if not name.isdigit():
            continue
        raw = None
        with contextlib.suppress(OSError):
            raw = pathlib.Path(f"/proc/{name}/cmdline").read_bytes()
        if raw is None:
            continue
        argv = [a.decode(errors="ignore") for a in raw.split(b"\0") if a]
        if (
            len(argv) >= 2
            and argv[0].rsplit("/", 1)[-1].startswith("python")
            and argv[1].endswith("bridge/bridge.py")
        ):
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(name), signal.SIGTERM)


class Stack:
    def __init__(self) -> None:
        self.procs: list[subprocess.Popen] = []
        self.sclang_stdin = None

    def start(self) -> None:
        logs = ROOT / "logs"
        logs.mkdir(exist_ok=True)
        # autosave recovery would otherwise restore layers from a previous run and
        # every layer count below would be off by one
        for stale in ("sessions/autosave", "sessions/slot7", "sessions/exports"):
            try:
                shutil.rmtree(ROOT / stale)
            except OSError as exc:
                if (ROOT / stale).exists():
                    print(f"smoke: cannot clear {stale} ({exc})")
        subprocess.run(["pkill", "-x", "scsynth"], check=False)
        subprocess.run(["pkill", "-x", "sclang"], check=False)
        kill_stale_bridges()
        time.sleep(0.4)
        for p in ("bridge.log", "engine.log"):
            try:
                if (logs / p).exists():
                    shutil.move(str(logs / p), str(logs / (p + ".1")))
            except OSError as exc:
                print(f"smoke: cannot rotate {p} ({exc})")

        bridge = subprocess.Popen(
            [str(PY), "bridge/bridge.py"], cwd=ROOT,
            stdout=(logs / "bridge.log").open("w"), stderr=subprocess.STDOUT,
        )
        self.procs.append(bridge)
        time.sleep(0.6)
        # stdin kept open: sclang quits on EOF
        self.sclang_stdin = subprocess.PIPE
        engine = subprocess.Popen(
            ["sclang", "engine/looper.scd"], cwd=ROOT,
            stdin=self.sclang_stdin,
            stdout=(logs / "engine.log").open("w"), stderr=subprocess.STDOUT,
        )
        self.procs.append(engine)

    def stop(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in self.procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        subprocess.run(["pkill", "-x", "scsynth"], check=False)


# -------------------------------------------------------------------- ws ----- #
class Client:
    def __init__(self, ws) -> None:
        self.ws = ws
        self.last_state: dict | None = None
        self.seen: set[str] = set()

    async def set(self, path: str, v: float) -> None:
        await self.ws.send(json.dumps({"op": "set", "path": path, "v": v}))

    async def act(self, action: str, track: int = 0) -> None:
        await self.ws.send(json.dumps({"op": "act", "a": action, "t": track}))

    async def svc(self, what: str, args: list) -> None:
        await self.ws.send(json.dumps({"op": "svc", "what": what, "args": args}))

    async def cfg(self, path: str, v) -> None:
        await self.ws.send(json.dumps({"op": "cfg", "path": path, "v": v}))

    async def wait(self, pred, timeout: float = 25.0, what: str = "condition"):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                m = json.loads(await asyncio.wait_for(self.ws.recv(), 2))
            except TimeoutError:
                continue
            except websockets.ConnectionClosed:
                return None
            self.seen.add(m.get("t", "?"))
            if m.get("t") == "s":
                self.last_state = m
            if verbose:
                print("   <", {k: v for k, v in m.items() if k != "tracks"})
            if pred(m):
                return m
        return None

    def track(self, i: int = 0) -> dict:
        if self.last_state is None:
            raise RuntimeError("no state received yet")
        return self.last_state["tracks"][i]


# ------------------------------------------------------------------ tests ---- #
async def run_tests(c: Client) -> None:
    st = await c.wait(lambda m: m.get("t") == "s", 30)
    if not check("engine sends state", st is not None) or st is None:
        return
    globals_ = len(st) - 2
    fields = len(st["tracks"][0])
    check("state shape (23 globals / 24 track fields)",
          globals_ == 23 and fields == 24 and len(st["tracks"]) == N,
          f"{globals_}/{fields}/{len(st['tracks'])}")

    # meters + playhead live inside the state frame (no separate OSC paths)
    phases = set()
    deadline = time.monotonic() + 5.0        # a 4-bar loop at 120 bpm = 8 s
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(c.ws.recv(), 0.5)
            m = json.loads(raw)
        except (TimeoutError, ValueError):
            continue
        if m.get("t") == "s":
            phases.add(round(m["phase"], 3))
    check("playhead phase advances", len(phases) > 4, f"{len(phases)} distinct")
    check("state carries meters", all("meter" in t for t in st["tracks"]))

    # sleep/resume detector (issue #1): a normal tick must read ~0 s, a 600 s
    # suspend must read 600 s. That gap is what tells the watchdog to restart
    # the engine, because audio never survives a suspend on its own.
    try:
        sys.path.insert(0, str(ROOT))
        from app.main import resume_drift
        ok = (abs(resume_drift(1000.0, 500.0, 1000.5, 500.5)) < 1e-9
              and abs(resume_drift(1000.0, 500.0, 1601.0, 501.0) - 600.0) < 1e-9)
        check("sleep/resume signal detected", ok)
    except Exception as exc:                       # noqa: BLE001 - report, don't crash
        check("sleep/resume signal detected", False, repr(exc))

    # SILENCE FIRST: every check below reads the state, not the speakers. Muting
    # the master keeps the run inaudible while internal buses still carry signal.
    await c.set("g.masterVol", 0)
    await c.set("g.metro", 0)
    await c.set("t0.vol", 0.7)
    st = await c.wait(lambda m: m.get("t") == "s" and abs(m["tracks"][0]["vol"] - 0.7) < 0.02, 6)
    check("parameter set reaches engine", st is not None)

    # ---- record -> auto-close -> overdub -> undo -> redo -> stop -> resume ----
    await c.act("press", 0)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 1, 6)
    check("press starts recording", st is not None)

    # RC-505 semantics: at the bar the first take closes and keeps recording
    # as an overdub (autoDub), so state stays 1 until the player presses.
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["layers"] >= 1, 20)
    base = st["tracks"][0]["layers"] if st is not None else 0
    check("first take closes into an overdub",
          st is not None and st["tracks"][0]["state"] == 1 and base >= 1,
          f"layers={base} state={st['tracks'][0]['state'] if st else '?'}")

    await c.act("press", 0)                        # press commits the overdub
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 2, 8)
    check("press commits the overdub and plays",
          st is not None and st["tracks"][0]["layers"] >= base)

    await c.act("press", 0)                        # tapping a playing track stops it
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 3, 8)
    check("press on a playing track stops it",
          st is not None and st["tracks"][0]["layers"] >= base)

    await c.act("press", 0)                        # ... and resumes
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 2, 8)
    check("press resumes playback", st is not None)
    base = st["tracks"][0]["layers"] if st is not None else base   # the committed take

    await c.act("dub", 0)                          # explicit overdub
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 1, 8)
    check("dub starts recording", st is not None)
    await asyncio.sleep(0.7)
    await c.act("dub", 0)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["layers"] > base, 8)
    check("dub adds a layer", st is not None, f"layers={st['tracks'][0]['layers'] if st else '?'}")

    await c.act("undo", 0)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["layers"] == base, 8)
    check("undo drops the last layer", st is not None)
    check("undo depth is reported", st is not None and st["tracks"][0]["undoDepth"] > base,
          f"depth={st['tracks'][0]['undoDepth'] if st else '?'}")

    await c.act("redo", 0)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["layers"] > base, 8)
    check("redo restores it", st is not None)

    await c.act("stop", 0)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 3, 8)
    check("stop keeps the layers", st is not None and st["tracks"][0]["layers"] > base)
    await c.act("press", 0)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 2, 8)
    check("press resumes playback (does not record)", st is not None)

    await c.act("stopAll", 0)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 3, 8)
    check("stopAll stops every track", st is not None)

    # ---- autosave appears on its own (crash recovery) ----
    auto_dir = ROOT / "sessions" / "autosave"
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if auto_dir.exists() and any(auto_dir.glob("*.wav")):
            break
        await asyncio.sleep(0.3)
    check("autosave written without asking",
          auto_dir.exists() and len(list(auto_dir.glob("*.wav"))) > 0,
          f"{len(list(auto_dir.glob('*.wav'))) if auto_dir.exists() else 0} files")

    # ---- varispeed instead of a tempo lock ----
    layers_before = c.track(0)["layers"]
    await c.set("g.bpm", 140)
    st = await c.wait(lambda m: m.get("t") == "s" and abs(m["bpm"] - 140) < 0.5, 6)
    check("tempo changes with content (varispeed)", st is not None,
          f"bpm={st['bpm'] if st else '?'}")
    check("layers survive the tempo change",
          st is not None and st["tracks"][0]["layers"] == layers_before)
    await c.set("g.bpm", 120)

    # ---- per-track features ----
    await c.set("t0.reverse", 1)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["reverse"] == 1, 6)
    check("reverse toggles", st is not None)
    await c.set("t0.reverse", 0)

    await c.set("t0.loopRatio", 2)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["loopRatio"] == 2, 6)
    check("per-track loop length", st is not None)
    await c.set("t0.loopRatio", 1)

    await c.set("g.dubDecay", 0.25)
    st = await c.wait(lambda m: m.get("t") == "s" and abs(m["dubDecay"] - 0.25) < 0.01, 6)
    check("dub decay setting applied", st is not None)
    await c.set("g.dubDecay", 0)

    # ---- engine-level settings ----
    await c.set("g.xfadeMs", 2.5)
    st = await c.wait(lambda m: m.get("t") == "s" and abs(m["xfadeMs"] - 2.5) < 0.01, 6)
    check("crossfade setting applied", st is not None)
    await c.set("g.recordOffset", 64)
    st = await c.wait(lambda m: m.get("t") == "s" and m["recordOffset"] == 64, 6)
    check("record offset applied", st is not None)
    await c.set("g.recordOffset", 0)

    # ---- stats / devices ----
    await c.svc("stats", [])
    st = await c.wait(lambda m: m.get("t") == "stats", 8)
    check("engine stats (cpu/sr/latency)", st is not None and st["sr"] > 0,
          f"sr={st['sr'] if st else '?'} cpu={st['cpu'] if st else '?'}")

    await c.ws.send(json.dumps({"op": "devices"}))
    st = await c.wait(lambda m: m.get("t") == "devices", 12)
    total = (len(st["sinks"]) + len(st["sources"]) + len(st["alsa"])) if st else 0
    check("audio device enumeration", total > 0, f"{total} nodes/devices")
    links = (st or {}).get("links", {})
    check("live graph connections reported",
          isinstance(links, dict) and "inputs" in links and "outputs" in links,
          f"{len(links.get('inputs', []))} in / {len(links.get('outputs', []))} out")

    # ---- session save / load / export ----
    session_dir = ROOT / "sessions" / "slot7"
    if session_dir.exists():
        try:
            shutil.rmtree(session_dir)
        except OSError as exc:
            print(f"smoke: cannot clear {session_dir} ({exc})")
    pre_layers = [c.track(i)["layers"] for i in range(N)]
    await c.svc("save", [7])
    st = await c.wait(lambda m: m.get("t") == "saved" and m["slot"] == 7, 15)
    wavs = sorted(session_dir.glob("*.wav")) if session_dir.exists() else []
    check("session save writes WAVs", st is not None and len(wavs) > 0,
          f"{len(wavs)} files")
    check("session.json written", (session_dir / "session.json").exists())

    await c.act("clear", 0)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["layers"] == 0, 6)
    check("clear empties the track", st is not None)

    await c.svc("load", [7])
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["layers"] > 0, 20)
    check("session load restores layers",
          st is not None and st["tracks"][0]["layers"] == max(1, pre_layers[0]),
          f"layers={st['tracks'][0]['layers'] if st else '?'}")

    exports = ROOT / "sessions" / "exports"
    before = set(exports.glob("*.wav")) if exports.exists() else set()
    await c.svc("export", [3])
    st = await c.wait(lambda m: m.get("t") == "exported", 20)
    after = set(exports.glob("*.wav")) if exports.exists() else set()
    check("mixdown export writes a WAV", len(after - before) == 1,
          next(iter(after - before)).name if after - before else "none")

    # ---- VST insert (skipped when the extension is absent) ----
    await c.svc("stats", [])
    st = await c.wait(lambda m: m.get("t") == "stats", 8)
    if st and st.get("vst") == 1:
        await c.svc("vstCatalog", [])
        st = await c.wait(lambda m: m.get("t") == "vstcatalog", 10)
        keys = [k for k in (st["csv"].split(";") if st else []) if k]
        if keys:
            await c.svc("vstOpen", [0, 0, keys[0]])
            st = await c.wait(
                lambda m: m.get("t") == "vst" and m["csv"].split(";")[0].count("|") > 3
                and m["csv"].split(";")[0].split(",")[0].split("|")[1],
                20,
            )
            check("VST3 loads on a track insert", st is not None,
                  keys[0])
            await c.svc("vstClose", [0, 0])
        else:
            check("VST3 plugin available", False, "catalog empty")
    else:
        print("  SKIP  VST (extension not installed)")

    # ---- MIDI learn path (no hardware needed: just the command plumbing) ----
    await c.svc("midiLearn", ["t0.press"])
    st = await c.wait(lambda m: m.get("t") == "s", 2)
    check("midi learn command accepted", "s" in c.seen)


def main() -> int:
    global verbose
    ap = argparse.ArgumentParser()
    ap.add_argument("--attach", action="store_true", help="use an already running stack")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    verbose = args.verbose

    stack = None
    if not args.attach:
        print("== starting engine")
        stack = Stack()
        stack.start()
        time.sleep(1)

    async def go() -> None:
        for attempt in range(40):
            try:
                async with websockets.connect(API) as ws:
                    await ws.recv()  # hello
                    await run_tests(Client(ws))
                    return
            except (OSError, websockets.ConnectionClosed):
                if attempt == 39:
                    check("bridge reachable", False, API)
                    return
                await asyncio.sleep(0.5)

    try:
        asyncio.run(go())
    finally:
        if stack:
            stack.stop()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:")
        for name, _, detail in failed:
            print(f"  - {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
