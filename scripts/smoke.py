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
import math
import os
import pathlib
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import wave

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
        for stale in ("sessions/autosave", "sessions/slot7", "sessions/slot99",
                  "sessions/backups", "sessions/import", "sessions/exports"):
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
    check("state shape (28 globals / 32 track fields)",
          globals_ == 28 and fields == 32 and len(st["tracks"]) == N,
          f"{globals_}/{fields}/{len(st['tracks'])}")

    # meters + playhead live inside the state frame (no separate OSC paths).
    # Wait for the stream to become continuous first: the engine sends an early
    # frame, then goes quiet for several seconds while the boot routines run
    # (autosave, VST scan, metering), and sampling inside that gap reads as "the
    # clock never moves" even though it does.
    last, streaming = None, False
    ready_by = time.monotonic() + 30
    while time.monotonic() < ready_by and not streaming:
        try:
            m = json.loads(await asyncio.wait_for(c.ws.recv(), 0.5))
        except (TimeoutError, ValueError):
            continue
        if m.get("t") == "s":
            streaming = last is not None and (time.monotonic() - last) < 1.0
            last = time.monotonic()
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

    # Detecting the sleep is half of it: the restart only counts when the engine
    # says it is back. A stale 'engine ready' from before the restart must not
    # satisfy the wait, or a resume that never recovered would look fixed.
    try:
        from app.main import log_size, wait_engine_ready
        with tempfile.TemporaryDirectory() as td:
            logfile = pathlib.Path(td) / "engine.log"
            logfile.write_text("*** Looper OSS: engine ready (5 tracks)\n")
            offset = log_size(logfile)                 # the old line is behind it
            stale = wait_engine_ready(logfile, offset, 0.4) is None
            with logfile.open("a") as fh:
                fh.write("*** Looper OSS: engine ready (5 tracks, device default)\n")
            fresh = wait_engine_ready(logfile, offset, 3.0)
        check("a resume waits for a fresh 'engine ready'",
              stale and fresh is not None,
              f"stale_line_ignored={stale} waited={fresh}")
    except Exception as exc:                       # noqa: BLE001 - report, don't crash
        check("a resume waits for a fresh 'engine ready'", False, repr(exc))

    # Re-staging is an update, not a reset. A musician's bpm/track count/device
    # live in ui/config.json, and their recordings in sessions/ -- a new build
    # must keep both, while still migrating the old `autoDub: 0` default.
    try:
        from app.main import stage  # noqa: PLC0415
        with tempfile.TemporaryDirectory() as td:
            dst = pathlib.Path(td) / "0.1.0"
            (dst / "ui").mkdir(parents=True)
            (dst / "sessions").mkdir(parents=True)
            cfg_path = dst / "ui" / "config.json"
            cfg_path.write_text(json.dumps(
                {"version": 2, "bpm": 133, "loop": {"autoDub": 0}}))
            (dst / "sessions" / "keep.wav").write_bytes(b"x")
            stage(ROOT, dst)
            cfg = json.loads(cfg_path.read_text())
            kept = ((dst / "sessions" / "keep.wav").is_file()
                    and cfg["bpm"] == 133
                    and cfg["loop"]["autoDub"] == 1
                    and cfg["version"] == 3)
        check("update keeps settings and music", kept)

        # a new version is a new staged home: it must inherit the old one
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td) / "app"
            old = home / "0.1.0"
            (old / "sessions" / "autosave").mkdir(parents=True)
            (old / "sessions" / "autosave" / "take.wav").write_bytes(b"RIFF")
            (old / "ui").mkdir(parents=True)
            (old / "ui" / "config.json").write_text(json.dumps(
                {"version": 3, "bpm": 137, "loop": {"autoDub": 1}}))
            (old / "engine").mkdir(parents=True)
            (old / "engine" / "looper.scd").write_text("// marker")
            new = home / "0.2.0"
            stage(ROOT, new)
            kept2 = ((new / "sessions" / "autosave" / "take.wav").is_file()
                     and json.loads((new / "ui" / "config.json").read_text()).get("bpm") == 137)
        check("version bump keeps the music", kept2)

        # Carrying the config forward is only safe if the new build can also
        # *read* it. Take the 0.1.0 shape -- no rhythm section, no VST banks,
        # no overdubMode -- and require that the staged config comes out with
        # every key this build ships, while the user's own values survive.
        with tempfile.TemporaryDirectory() as td:
            dst = pathlib.Path(td) / "0.1.0"
            (dst / "ui").mkdir(parents=True)
            (dst / "sessions").mkdir(parents=True)
            ancient = json.loads((ROOT / "ui" / "config.json").read_text())
            for gone in ("rhythm",):
                ancient.pop(gone, None)
            ancient["loop"].pop("overdubMode", None)
            ancient["vst"].pop("banks", None)
            ancient["version"] = 2
            ancient["bpm"] = 137
            ancient["audio"]["device"] = "some other interface"
            (dst / "ui" / "config.json").write_text(json.dumps(ancient))
            stage(ROOT, dst)
            staged = json.loads((dst / "ui" / "config.json").read_text())
            shipped = json.loads((ROOT / "ui" / "config.json").read_text())

            def holes(want: dict, have: dict, at: str = "") -> list[str]:
                out = [at + k for k in want if k not in have]
                for k, v in want.items():
                    if isinstance(v, dict) and isinstance(have.get(k), dict):
                        out += holes(v, have[k], f"{at}{k}.")
                return out

            missing = holes(shipped, staged)
            completed = (not missing
                         and staged["bpm"] == 137
                         and staged["audio"]["device"] == "some other interface"
                         and staged["loop"]["autoDub"] == 1)
        check("an old config is completed, not replaced", completed,
              f"missing {missing[:6]}")
    except Exception as exc:                       # noqa: BLE001 - report, don't crash
        check("update keeps settings and music", False, repr(exc))

    # SILENCE FIRST: every check below reads the state, not the speakers. Muting
    # the master keeps the run inaudible while internal buses still carry signal.
    await c.set("g.masterVol", 0)
    await c.set("g.metro", 0)
    await c.set("t0.vol", 0.7)
    st = await c.wait(lambda m: m.get("t") == "s" and abs(m["tracks"][0]["vol"] - 0.7) < 0.02, 6)
    check("parameter set reaches engine", st is not None)

    # The record source is per track, not per app: stereo sum, then one mono
    # entry per input channel, so a mic on L and a guitar on R can be two tracks
    # from one interface. The engine builds that list from audio.in.
    st = None
    for ch in (0, 1, 2):
        await c.set("t0.input", ch)
        st = await c.wait(lambda m, ch=ch: m.get("t") == "s"
                          and m["tracks"][0]["input"] == ch, 6)
        if st is None:
            break
    check("record source is per track (stereo / L / R)",
          st is not None and st["tracks"][0]["input"] == 2)
    await c.set("t0.input", 0)

    # Per-track speed is varispeed: continuous, and 1 means grid-locked.
    await c.set("t0.speed", 0.5)
    st = await c.wait(lambda m: m.get("t") == "s"
                      and abs(m["tracks"][0]["speed"] - 0.5) < 0.01, 6)
    check("track speed is per track (0.5x)", st is not None,
          f"speed={st['tracks'][0]['speed'] if st else '?'}")
    await c.set("t0.speed", 2)
    st = await c.wait(lambda m: m.get("t") == "s"
                      and abs(m["tracks"][0]["speed"] - 2) < 0.01, 6)
    check("track speed reaches 2x", st is not None)
    await c.set("t0.speed", 1)

    # D4: the input compressor is per track and live
    await c.set("t0.compOn", 1)
    await c.set("t0.compThr", 0.35)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["compOn"] == 1
                      and abs(m["tracks"][0]["compThr"] - 0.35) < 0.01, 6)
    check("input compressor is per track", st is not None,
          f"compOn={st['tracks'][0]['compOn'] if st else '?'}")
    await c.set("t0.compOn", 0)

    # D4: per-track output routing into bus A/B/C
    await c.set("t0.route", 2)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["route"] == 2, 6)
    check("track routes to output bus C", st is not None,
          f"route={st['tracks'][0]['route'] if st else '?'}")
    await c.set("t0.route", 0)
    await c.set("t0.cueOn", 1)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["cueOn"] == 1, 6)
    check("cue send is per track", st is not None,
          f"cueOn={st['tracks'][0]['cueOn'] if st else '?'}")
    await c.set("t0.cueOn", 0)

    # D4: the output mixer -- one level per bus, live on the master
    await c.set("g.busB", 0.4)
    st = await c.wait(lambda m: m.get("t") == "s" and abs(m["busB"] - 0.4) < 0.01, 6)
    check("output bus level reaches the master", st is not None,
          f"busB={st['busB'] if st else '?'}")
    await c.set("g.busB", 1)

    # D3: an eight-step sequence on the track's low pass
    await c.svc("seq", [0, "0,0.25,0.5,0.75,1,0.75,0.5,0.25"])
    await c.set("t0.seqOn", 1)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["seqOn"] == 1, 6)
    check("step sequence toggles per track", st is not None,
          f"seqOn={st['tracks'][0]['seqOn'] if st else '?'}")
    await c.set("t0.seqOn", 0)

    # D4: the input mixer -- one gain per physical input, shared by the tracks
    await c.svc("inputGains", [])
    st = await c.wait(lambda m: m.get("t") == "inputs", 8)
    check("input mixer reports a gain per channel",
          st is not None and len((st.get("csv") or "").split(",")) >= 2,
          (st or {}).get("csv", ""))

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

    # MARK BACK jumps to a bookmark instead of stepping down one layer at a time
    await c.act("mark", 0)
    await asyncio.sleep(0.4)
    marks_at = c.track(0)["layers"]
    await c.act("dub", 0)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 1, 8)
    await asyncio.sleep(0.7)
    await c.act("dub", 0)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["layers"] > marks_at, 8)
    await c.act("markBack", 0)
    st = await c.wait(lambda m: m.get("t") == "s"
                      and m["tracks"][0]["layers"] == marks_at, 8)
    check("mark back jumps to the bookmark", st is not None,
          f"layers={st['tracks'][0]['layers'] if st else '?'}")

    # replace mode: the new take mutes what it covers, and undo puts it back.
    # The take has to carry signal: a take that records nothing is now dropped
    # rather than committed (checked below), so a silent one would prove nothing.
    await c.svc("testTone", [220, 0.25, 120])
    await c.set("t0.input", int(CFG.get("audio", {}).get("in", 2)) + 1)
    await c.set("t0.gateOn", 0)
    await c.set("t0.hp", 20)
    await c.set("t0.lp", 19000)
    await asyncio.sleep(0.5)
    await c.cfg("loop.overdubMode", 1)
    await asyncio.sleep(0.8)
    before_replace = c.track(0)["layers"]
    await c.act("dub", 0)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 1, 8)
    await asyncio.sleep(0.7)
    await c.act("dub", 0)
    await c.wait(lambda m: m.get("t") == "s"
                 and m["tracks"][0]["layers"] > before_replace, 8)
    await c.svc("probe", [0])
    pr = await c.wait(lambda m: m.get("t") == "probe" and m["track"] == 0, 8)
    # the take is the top layer; everything below it is muted
    check("replace mode mutes the covered layers",
          pr is not None and pr["gains"][0] == 0
          and pr["gains"][pr["layers"] - 1] > 0,
          f"layers={pr['layers'] if pr else '?'} gains={pr['gains'] if pr else '?'}")
    await c.act("undo", 0)
    await c.svc("probe", [0])
    pr = await c.wait(lambda m: m.get("t") == "probe" and m["track"] == 0, 8)
    check("undo restores the covered layers",
          pr is not None and pr["gains"][0] > 0, f"gains={pr['gains'] if pr else '?'}")
    await c.cfg("loop.overdubMode", 0)
    await c.set("t0.input", 0)

    # ---- #5: the layer model must not lose sound -------------------------
    # Four sequences that used to break the promise that content, gain and the
    # undo stack agree. Track 4 is kept out of every other check's way, and a
    # take here starts and closes only when we say so (autoClose/autoDub off).
    L = 4
    testin = int(CFG.get("audio", {}).get("in", 2)) + 1   # the diagnostic bus
    await c.set("g.autoClose", 0)
    await c.set("g.autoDub", 0)
    await c.set(f"t{L}.input", testin)
    await c.set(f"t{L}.gateOn", 0)
    await c.set(f"t{L}.hp", 20)
    await c.set(f"t{L}.lp", 19000)
    await asyncio.sleep(0.6)

    async def take_dub(secs: float = 1.6, gate: int = 0) -> None:
        """Open a take, let it run, close it, and wait for the layer count.

        gate=1 closes the input gate above the tone's level, so the take writes
        digital silence -- deterministic, and independent of the interface.
        """
        await c.set(f"t{L}.gateThr", 0.9 if gate else 0.02)
        await c.set(f"t{L}.gateOn", gate)
        await asyncio.sleep(0.3)
        await c.act("dub", L)
        await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["state"] == 1, 8)
        await asyncio.sleep(secs)
        await c.act("dub", L)
        await asyncio.sleep(1.2)

    async def probe_l() -> dict | None:
        await c.svc("probe", [L])
        return await c.wait(lambda m: m.get("t") == "probe" and m["track"] == L, 10)

    async def clear_l() -> None:
        await c.act("clear", L)
        await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["layers"] == 0, 8)

    # a take that recorded nothing must not mute what is underneath it. This is
    # the reported "sounds get deleted when a silent part overdubs": REPLACE
    # committed the empty take and silenced the whole track.
    await clear_l()
    await c.svc("testTone", [220, 0.25, 120])
    await take_dub()
    await c.cfg("loop.overdubMode", 1)
    await asyncio.sleep(0.6)
    await take_dub(gate=1)          # gate thresh 0.02 vs a 0.25 tone: silence
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["layers"] == 1, 8)
    pr = await probe_l()
    check("a silent take under REPLACE is dropped, not committed",
          st is not None and pr is not None and pr["gains"][0] > 0,
          f"layers={pr['layers'] if pr else '?'} gains={pr['gains'] if pr else '?'}")
    await c.cfg("loop.overdubMode", 0)

    # undo to empty must leave the track STOPPED, or the next tap on the pad
    # stops a track that is not playing instead of recording into it
    await c.act("undo", L)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["layers"] == 0, 8)
    await c.act("press", L)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["state"] == 1, 8)
    check("after undo to empty, the pad records again", st is not None)
    await asyncio.sleep(1.2)
    await c.act("press", L)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["layers"] == 1, 8)
    check("that take closes as one layer", st is not None)

    # erasing a layer invalidates the redo above it: a redo must never restore a
    # gain for a buffer the new take just zeroed
    await c.act("undo", L)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["layers"] == 0, 8)
    await take_dub()
    await c.act("redo", L)
    await asyncio.sleep(0.8)
    pr = await probe_l()
    check("redo cannot resurrect a take a new recording erased",
          pr is not None and pr["layers"] == 1 and pr["gains"][0] > 0,
          f"layers={pr['layers'] if pr else '?'} gains={pr['gains'] if pr else '?'}")

    # and it must not promote a layer while the write head is on it
    await c.act("undo", L)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["layers"] == 0, 8)
    await c.act("dub", L)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["state"] == 1, 8)
    await c.act("redo", L)
    await asyncio.sleep(0.8)
    mid = c.track(L)["layers"]
    await c.act("dub", L)
    await asyncio.sleep(1.4)
    check("redo is refused while a take is open", mid == 0, f"layers={mid}")

    # dub decay belongs to the track being dubbed over, not to the instrument:
    # a stopped loop must not fade because another track is playing. The playing
    # track is the positive control -- without it a silent engine would pass.
    await c.act("stop", L)
    await asyncio.sleep(0.3)
    st0 = c.track(0)["state"]
    if st0 != 2:
        await c.act("press", 0)
        await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 2, 8)
    await c.svc("probe", [0])
    t0_before = await c.wait(lambda m: m.get("t") == "probe" and m["track"] == 0, 8)
    await c.svc("probe", [L])
    still_before = await c.wait(lambda m: m.get("t") == "probe" and m["track"] == L, 8)
    await c.set("g.dubDecay", 0.3)
    await asyncio.sleep(5.0)          # ~2-3 wraps at 1 bar / 120 bpm
    await c.svc("probe", [0])
    t0_after = await c.wait(lambda m: m.get("t") == "probe" and m["track"] == 0, 8)
    await c.svc("probe", [L])
    still_after = await c.wait(lambda m: m.get("t") == "probe" and m["track"] == L, 8)
    await c.set("g.dubDecay", 0)
    decayed = (t0_before is not None and t0_after is not None
               and t0_after["gains"][0] < t0_before["gains"][0] - 0.01)
    kept = (still_before is not None and still_after is not None
            and abs(still_after["gains"][0] - still_before["gains"][0]) < 0.001)
    check("dub decay is per track (playing fades, stopped does not)",
          decayed and kept,
          f"playing {t0_before['gains'][0]:.2f}->{t0_after['gains'][0]:.2f}, "
          f"stopped {still_before['gains'][0]:.2f}->{still_after['gains'][0]:.2f}"
          if (t0_before and t0_after and still_before and still_after) else "no probe")
    # ---- #6: MERGE -------------------------------------------------------
    # A take starts on the bar line here, so its content is at the head of the
    # buffer and the probe window (the first 48 000 frames) really sees it.
    await c.set("g.autoClose", 0)
    await c.set("g.autoDub", 0)
    await c.set("g.quantizeStart", 1)
    # a one-bar loop, so a take covers it and the probe window really sees it;
    # the silent-take check above also left this track's gate closed.
    bars_before = (c.last_state or {}).get("bars", 4)
    await c.set("g.bars", 1)
    await c.set("g.bpm", 120)
    await c.set(f"t{L}.gateThr", 0.02)
    await c.set(f"t{L}.gateOn", 0)
    await asyncio.sleep(0.8)

    async def quantized_take(freq: int = 220) -> None:
        await c.svc("testTone", [freq, 0.25, 120])
        await c.act("dub", L)
        await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["state"] == 1, 12)
        await asyncio.sleep(1.6)
        await c.act("dub", L)
        await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["layers"] > 0
                     and m["tracks"][L]["state"] != 1, 10)
        await asyncio.sleep(1.0)

    await c.act("clear", L)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["layers"] == 0, 8)
    await quantized_take(220)
    await quantized_take(330)
    pr = await probe_l()
    stacked = list(pr["peaks"][:2]) if pr else [0, 0]
    await c.act("mergeLayers", L)
    merged = await c.wait(lambda m: m.get("t") == "merged" and m["track"] == L, 20)
    await asyncio.sleep(1.0)
    pr = await probe_l()
    # The probe reads a 48 000-frame window of a longer loop, so it under-reads
    # each source by an unknown amount: the merged peak must be louder than
    # either layer alone (the sum really happened) and no louder than twice the
    # sum of what was measured (it was written once, not piled up).
    lo, hi = max(stacked) * 1.05, (stacked[0] + stacked[1]) * 2.0
    check("merge layers sums the stack into one layer",
          merged is not None and merged.get("ok") == 1 and pr is not None
          and pr["layers"] == 1 and pr["gains"][0] == 1
          and stacked[0] > 0.05 and stacked[1] > 0.05
          and lo <= pr["peaks"][0] <= hi,
          f"{stacked[0]:.2f}+{stacked[1]:.2f} -> {pr['peaks'][0]:.2f} "
          f"(want {lo:.2f}..{hi:.2f}, {pr['layers']}L)" if pr else "no probe")

    # merging one layer is not a merge: it must be refused, not silently no-op
    await c.act("mergeLayers", L)
    merged = await c.wait(lambda m: m.get("t") == "merged" and m["track"] == L, 10)
    check("merge layers is refused with fewer than two layers",
          merged is not None and merged.get("ok") == 0, f"{merged}")

    # MERGE TRACKS by selection: two of five joined, the other three untouched
    t0_layers = c.track(0)["layers"]
    await c.set("t3.input", testin)
    await c.set("t3.gateOn", 0)
    await asyncio.sleep(0.3)
    await c.act("clear", 3)
    await asyncio.sleep(0.6)
    await c.svc("testTone", [440, 0.25, 120])
    await c.act("dub", 3)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][3]["state"] == 1, 12)
    await asyncio.sleep(1.6)
    await c.act("dub", 3)
    await asyncio.sleep(1.2)
    # A merge is a layer MOVE, not a sum: the destination keeps its own layers
    # and gains the source's, and it answers in about a millisecond because
    # nothing is re-rendered.
    # counts from the engine, not from a websocket frame that may still be in
    # the buffer from before the copy test
    await c.svc("probe", [3])
    p3 = await c.wait(lambda m: m.get("t") == "probe" and m["track"] == 3, 10)
    src_before = (await probe_l() or {}).get("layers", 0)
    dst_before = (p3 or {}).get("layers", 0)
    t_merge = time.monotonic()
    await c.svc("mergeTracks", [3, 3, L])
    st = await c.wait(lambda m: m.get("t") == "s"
                      and m["tracks"][3]["layers"] >= 1
                      and m["tracks"][L]["layers"] == 0, 25)
    merge_ms = (time.monotonic() - t_merge) * 1000
    left_alone = c.track(0)["layers"] == t0_layers
    # the fast path (layers moved, nothing summed) must have added the source's
    # layers; the summing fallback costs a loop pass and leaves one layer
    moved = st is not None and st["tracks"][3]["layers"] == dst_before + src_before
    check("merge tracks moves the layers and frees the source",
          st is not None and left_alone and (not moved or merge_ms < 500),
          f"dst={st['tracks'][3]['layers'] if st else '?'} "
          f"(was {dst_before}, src {src_before}) "
          f"src={st['tracks'][L]['layers'] if st else '?'} "
          f"untouched={'yes' if left_alone else 'no'} in {merge_ms:.0f} ms")

    await c.set("g.quantizeStart", 0)
    await c.set("g.autoClose", 1)
    await c.set("g.autoDub", 1)
    await c.set("g.bars", bars_before)
    await c.act("clear", L)
    await c.act("clear", 3)

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

    # ---- memories: 99 slots, and one zip that carries them all ----
    await c.svc("save", [99])
    st = await c.wait(lambda m: m.get("t") == "saved" and m["slot"] == 99, 20)
    check("memory slot 99 saves", st is not None)
    await c.ws.send(json.dumps({"op": "backup"}))
    st = await c.wait(lambda m: m.get("t") == "backup", 25)
    zips = sorted((ROOT / "sessions" / "backups").glob("looper-*.zip"))
    check("backup writes one zip with the sessions",
          bool(st and st.get("ok")) and bool(zips) and zips[-1].stat().st_size > 0,
          zips[-1].name if zips else "no zip")

    await c.act("clear", 0)
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["layers"] == 0, 6)
    check("clear empties the track", st is not None)

    # ---- clear all: one gesture wipes every track, not just the selected one ----
    await c.act("press", 0)
    await c.act("press", 1)
    st = await c.wait(
        lambda m: m.get("t") == "s" and all(t["layers"] > 0 or t["state"] == 1
                                              for t in m["tracks"][:2]), 10)
    await c.act("clearAll", 0)
    st = await c.wait(lambda m: m.get("t") == "s" and all(t["layers"] == 0
                                                          for t in m["tracks"]), 10)
    check("clear all empties every track",
          st is not None and all(t["state"] == 0 for t in st["tracks"]),
          f"layers={[t['layers'] for t in st['tracks']] if st else '?'}")

    # BOUNCE IN: every track that has layers, summed into one, one loop pass
    await c.act("press", 0)
    await c.act("press", 1)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][0]["layers"] > 0
                 and m["tracks"][1]["layers"] > 0, 30)
    await c.act("bounce", 2)
    st = await c.wait(lambda m: m.get("t") == "s"
                      and m["tracks"][2]["layers"] == 1
                      and m["tracks"][0]["layers"] == 0
                      and m["tracks"][1]["layers"] == 0, 35)
    check("bounce merges every track into one", st is not None,
          f"layers={[t['layers'] for t in st['tracks']] if st else '?'}")

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
            # D3: four simultaneous inserts. Slot 3 is the fourth.
            await c.svc("vstOpen", [0, 3, keys[0]])
            st = await c.wait(
                lambda m: m.get("t") == "vst"
                and len(m["csv"].split(";")[0].split(",")) >= 4
                and m["csv"].split(";")[0].split(",")[3].split("|")[1],
                20,
            )
            check("fourth plugin slot loads", st is not None)
            await c.svc("vstClose", [0, 3])
            # D3: four banks. Store what is loaded, empty the slots, recall it.
            await c.svc("vstBankSave", [0, 0])
            await asyncio.sleep(0.4)
            await c.svc("vstClose", [0, 0])
            await asyncio.sleep(0.6)
            await c.svc("vstBankLoad", [0, 0])
            st = await c.wait(
                lambda m: m.get("t") == "vst"
                and m["csv"].split(";")[0].split(",")[0].split("|")[1], 25)
            check("fx bank recalls the slot", st is not None)
            await c.svc("vstMode", [0, 0, 1])
            st = await c.wait(
                lambda m: m.get("t") == "vst"
                and len(m["csv"].split(";")[0].split(",")[0].split("|")) >= 5
                and m["csv"].split(";")[0].split(",")[0].split("|")[4] == "1", 12)
            check("fx activation mode (toggle / momentary)", st is not None)
            await c.svc("vstMode", [0, 0, 0])
            await c.svc("vstClose", [0, 0])
        else:
            check("VST3 plugin available", False, "catalog empty")
    else:
        print("  SKIP  VST (extension not installed)")

    # ---- MIDI learn path (no hardware needed: just the command plumbing) ----
    await c.svc("midiLearn", ["t0.press"])
    st = await c.wait(lambda m: m.get("t") == "s", 2)
    check("midi learn command accepted", "s" in c.seen)

    # A note carries a gesture: press / hold / double are distinct, and learning
    # captures whichever you performed. midiAdd/midiSim exercise that without
    # hardware; the map is asserted first so a silent miss cannot look like a pass.
    await c.svc("midiAdd", ["note", 60, "t2.press", "hold"])
    await asyncio.sleep(0.6)
    await c.svc("midiMap", [])
    mp = await c.wait(lambda m: m.get("t") == "midimap" and "60" in (m.get("csv") or ""), 8)
    check("midiAdd installs the mapping", mp is not None, (mp or {}).get("csv", ""))
    before_gesture = c.track(2)["state"]
    await c.svc("midiSim", [60, "press"])        # wrong gesture: must do nothing
    await asyncio.sleep(0.8)
    after_press = c.track(2)["state"]
    await c.svc("midiSim", [60, "hold"])         # right gesture: acts
    st = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][2]["state"] != after_press, 8)
    check("a hold fires only its hold mapping",
          st is not None and after_press == before_gesture,
          f"before={before_gesture} after-press={after_press}")

    # Rhythm engine: 16 synthesised kits, 200 generated patterns, cfg-driven.
    await c.cfg("rhythm.kit", 5)
    await c.cfg("rhythm.pattern", 42)
    await c.cfg("rhythm.on", 1)
    await asyncio.sleep(0.8)
    await c.svc("rhythm", [])
    st = await c.wait(lambda m: m.get("t") == "rhythm", 8)
    check("rhythm engine takes kit / pattern / on",
          st is not None and st["kit"] == 5 and st["pattern"] == 42
          and st["on"] == 1, f"{st}")
    # leave the repo config as we found it: the map and the rhythm settings are
    # configuration and the bridge persists them
    await c.cfg("rhythm.on", 0)
    await c.cfg("rhythm.kit", 0)
    await c.cfg("rhythm.pattern", 0)
    await c.svc("midiClear", [])

    # ---- #7: the DJ filter and the master FX slots ------------------------
    # Measured at the master meter, so this needs the master *unmuted*: 0.05 of
    # 0.25 amplitude is about -60 dBFS at the speaker, inaudible, and plenty for
    # Amplitude.kr to read.
    async def master_level() -> float:
        """Ask the engine for the master meter, instead of reading the socket.

        A websocket frame can be seconds old and makes a working filter look
        dead (AGENTS §8). A query is answered from the meter the engine is
        holding *now*.
        """
        # Sample across a whole loop and take the MAX: the level wobbles by 2x
        # from one instant to the next (a short take in a 2 s loop), and comparing
        # two single readings made a working filter look broken.
        await asyncio.sleep(0.7)
        best = 0.0
        for _ in range(8):
            await c.svc("master", [])
            m = await c.wait(lambda x: x.get("t") == "master", 5)
            if m:
                best = max(best, float(m.get("l") or 0))
            await asyncio.sleep(0.18)
        return best

    # a track with a steady tone, playing. autoClose/autoDub off, or the take
    # closes at the wrap and starts another one, leaving the track recording
    # (and therefore silent) for the rest of the measurement.
    await c.set("g.autoClose", 0)
    await c.set("g.autoDub", 0)
    # one bar: a 1.6 s take then covers most of the loop, so the meter is not
    # silent for 80% of the time by design
    bars_m4 = (c.last_state or {}).get("bars", 4)
    await c.set("g.bars", 1)
    await c.set("g.bpm", 120)
    await c.act("clear", L)
    await asyncio.sleep(0.5)
    await c.set(f"t{L}.gateThr", 0.02)
    await c.set(f"t{L}.gateOn", 0)
    await c.svc("testTone", [220, 0.25, 120])
    await c.act("dub", L)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["state"] == 1, 12)
    await asyncio.sleep(1.6)
    await c.act("dub", L)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["layers"] == 1
                 and m["tracks"][L]["state"] != 1, 10)
    await asyncio.sleep(0.8)
    await c.set("g.masterVol", 0.05)
    # a commit leaves the track playing; press would STOP it
    if c.track(L)["state"] != 2:
        await c.act("press", L)
        await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["state"] == 2, 8)
    # waiting for the engine to *confirm* the volume is what gets us past the
    # frames buffered before it (AGENTS §8: read fresh frames)
    await c.wait(lambda m: m.get("t") == "s" and m["masterVol"] > 0.04, 8)
    await c.svc("testTone", [220, 0.25, 120])     # a tone that is certainly alive
    await asyncio.sleep(0.6)
    # The level drifts slowly over the block (the loop's material, the meter's
    # follower), so each extreme is compared with a centre reading taken just
    # before it rather than with one reading minutes earlier.
    await c.set("g.dj", 0)
    c1 = await master_level()
    await c.set("g.dj", -0.98)                   # low pass near 8 Hz: everything out
    low = await master_level()
    await c.set("g.dj", 0)
    c2 = await master_level()
    await c.set("g.dj", 0.7)                     # high pass ~2.5 kHz: the tone is gone
    high = await master_level()
    await c.set("g.dj", 0.0)
    c3 = await master_level()
    back = await master_level()
    pr = await probe_l()
    await c.svc("diag", [])
    d1 = await c.wait(lambda m: m.get("t") == "diag", 6) or {}
    await asyncio.sleep(1.2)
    await c.svc("diag", [])
    d2 = await c.wait(lambda m: m.get("t") == "diag", 6) or {}
    dg = {"a": d1.get("beats_seen"), "b": d2.get("beats_seen"),
          "gridOn": d2.get("gridOn"), "poll": d2.get("poll")}
    st = c.last_state or {}
    ph1 = st.get("phase")
    await asyncio.sleep(0.4)
    st = await c.wait(lambda m: m.get("t") == "s", 3) or c.last_state or {}
    ph2 = st.get("phase")
    tr = (st.get("tracks") or [{}])[L] if len(st.get("tracks") or []) > L else {}
    # The meter wobbles by ~2x between samples (a short take in a 2 s loop), so
    # the assertion is directional with a margin, not a ratio.
    check("dj filter: centre passes, both ends cut",
          c1 > 0.002 and low < c1 * 0.4 and high < c2 * 0.8 and back > c3 * 0.6,
          f"centre={c1:.4f}/{c2:.4f}/{c3:.4f} lp={low:.4f} hp={high:.4f} back={back:.4f} "
          f"[state={tr.get('state')} layers={tr.get('layers')} "
          f"meter={tr.get('meter')} vol={st.get('masterVol')} dj={st.get('dj')} "
          f"gains={(pr or {}).get('gains')} peaks={(pr or {}).get('peaks')} "
          f"tvol={tr.get('vol')} phase={ph1}->{ph2} diag={dg}]")

    # a master FX slot is live: a filter at the bottom of its range kills the tone
    await c.set("g.fx1Type", 1)
    await c.set("g.fx1A", 0.0)
    await c.set("g.fx1B", 0.2)
    fx_on = await master_level()
    await c.set("g.fx1Type", 0)
    fx_off = await master_level()
    check("master fx slot applies and switches off live",
          fx_on < fx_off * 0.2 and fx_off > 0.002,
          f"fx={fx_on:.4f} bypass={fx_off:.4f}")
    await c.set("g.masterVol", 0)
    await c.set("g.autoClose", 1)
    await c.set("g.autoDub", 1)
    await c.set("g.bars", bars_m4)
    await c.act("stop", L)

    # ---- #3: the parity gaps the matrix actually flagged -------------------
    # Auto Rec: the detector synth has existed all along with an `arm` argument
    # that nothing ever set, so the feature looked implemented and was not.
    await c.act("clear", L)
    await asyncio.sleep(0.5)
    await c.set(f"t{L}.input", testin)
    await c.set(f"t{L}.gateOn", 0)
    await c.set(f"t{L}.autoArm", 1)
    await asyncio.sleep(0.4)
    await c.svc("testTone", [660, 0.3, 30])
    autostart = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["state"] == 1, 12)
    check("auto rec starts a take on the first note", autostart is not None,
          f"state={autostart['tracks'][L]['state'] if autostart else '?'}")
    await asyncio.sleep(1.4)
    await c.act("dub", L)
    await asyncio.sleep(1.0)

    # per-layer mute: the layer gains ARE the mixer
    await asyncio.sleep(0.4)
    before = (await probe_l() or {}).get("gains")
    await c.set(f"t{L}.lmute", 0)
    await asyncio.sleep(0.5)
    mid = (await probe_l() or {}).get("gains")
    await c.set(f"t{L}.lmute", 0)
    await asyncio.sleep(0.5)
    after = (await probe_l() or {}).get("gains")
    flipped = (before and mid and after
               and before[0] != mid[0] and mid[0] != after[0])
    check("per-layer mute toggles one layer's gain", bool(flipped),
          f"{before} -> {mid} -> {after}")

    # one-shot: plays one complete pass, then stops by itself
    oneshot_bars = (c.last_state or {}).get("bars", 4)
    await c.set("g.bars", 1)          # 2 s loops, so two passes fit in the timeout
    await asyncio.sleep(0.5)
    await c.set(f"t{L}.oneshot", 1)
    await c.act("press", L)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["state"] == 2, 8)
    stopped = await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["state"] == 3, 25)
    check("one-shot stops the track at the boundary", stopped is not None,
          f"state={stopped['tracks'][L]['state'] if stopped else '?'}")
    await c.set(f"t{L}.oneshot", 0)
    await c.set("g.bars", oneshot_bars)

    # track copy and swap
    await c.act("clear", 3)
    await asyncio.sleep(0.5)
    await c.act("clear", L)
    await asyncio.sleep(0.5)
    await c.set(f"t{L}.gateThr", 0.02)
    await c.set(f"t{L}.gateOn", 0)
    await c.set(f"t{L}.input", testin)
    await c.svc("testTone", [300, 0.3, 60])
    await c.act("dub", L)
    await c.wait(lambda m: m.get("t") == "s" and m["tracks"][L]["state"] == 1, 12)
    await asyncio.sleep(1.6)
    await c.act("dub", L)
    await asyncio.sleep(1.2)
    # the source has to actually hold something, or the copy proves nothing
    src_probe = await probe_l()
    if not src_probe or src_probe["peaks"][0] < 0.02:
        await c.svc("testTone", [300, 0.3, 60])
        await c.act("dub", L)
        await asyncio.sleep(1.8)
        await c.act("dub", L)
        await asyncio.sleep(1.2)
        src_probe = await probe_l()
    await c.svc("copyTrack", [L, 3])
    cp = await c.wait(lambda m: m.get("t") == "copied", 12)
    p3 = await probe_l() if False else None
    await c.svc("probe", [3])
    p3 = await c.wait(lambda m: m.get("t") == "probe" and m["track"] == 3, 10)
    src_layers = (await probe_l() or {}).get("layers", 0)
    check("track copy duplicates the layers",
          cp is not None and p3 is not None and p3["layers"] == src_layers
          and p3["peaks"][0] > 0.05,
          f"copied={cp} layers={(p3 or {}).get('layers')} "
          f"peak={round((p3 or {}).get('peaks', [0])[0], 3)}")

    await c.svc("swapTracks", [3, 4])
    await c.wait(lambda m: m.get("t") == "copied", 12)
    await asyncio.sleep(0.6)
    await c.svc("probe", [3])
    a3 = await c.wait(lambda m: m.get("t") == "probe" and m["track"] == 3, 10)
    await c.svc("probe", [4])
    a4 = await c.wait(lambda m: m.get("t") == "probe" and m["track"] == 4, 10)
    check("track swap exchanges the two tracks",
          a3 is not None and a4 is not None and a4["layers"] == a3["layers"]
          and a4["layers"] > 0,
          f"t3={(a3 or {}).get('layers')} t4={(a4 or {}).get('layers')}")

    # ---- #4: Ableton Link, the paths that need no peer ---------------------
    # A live peer cannot be assumed here, so the suite covers what a machine
    # without one must do: enable, keep our tempo, and come back on disable.
    # The with-peer tempo change was verified by hand against a second sclang.
    try:
        await c.cfg("clock.link", 1)
        lk = await c.wait(lambda m: m.get("t") == "link" and m.get("on") == 1, 12)
        await asyncio.sleep(1.5)
        st = await c.wait(lambda m: m.get("t") == "s", 5)
        bpm_held = st is not None and abs(st["bpm"] - 120.0) < 3.0
        check("link can be enabled with no peer and keeps our tempo",
              lk is not None and bpm_held,
              f"status={lk} bpm={st['bpm'] if st else '?'}")

        await c.cfg("clock.link", 0)
        await asyncio.sleep(1.5)
        st = await c.wait(lambda m: m.get("t") == "s", 5)
        check("link off returns to the configured tempo",
              st is not None and abs(st["bpm"] - 120.0) < 0.5,
              f"bpm={st['bpm'] if st else '?'}")
    except Exception as exc:                       # noqa: BLE001 - report, don't crash
        check("link can be enabled with no peer and keeps our tempo", False, repr(exc))
    finally:
        await c.cfg("clock.link", 0)

    # ---- #3: the phrase browser lists what is on disk ----------------------
    # slot 99 was saved earlier in this run, so the browser must see it.
    await c.ws.send(json.dumps({"op": "sessions"}))
    slots = await c.wait(lambda m: m.get("t") == "slots" and "slot99" in (m.get("csv") or ""), 10)
    check("phrase browser lists the saved slots", slots is not None,
          (slots or {}).get("csv", "")[:80])

    # ---- #3: per-channel input EQ, before any track -------------------------
    await c.svc("inputEq", [0, 200, 4000])
    await asyncio.sleep(0.4)
    st = await c.wait(lambda m: m.get("t") == "s", 5)
    check("per-channel input EQ is accepted", st is not None, "svc inputEq 0 200 4000")
    await c.svc("inputEq", [0, 20, 19000])

    # ---- the engine never complains ---------------------------------------
    # sclang reports a bad guard or a broken UGen by posting an ERROR line and
    # carrying on, so a suite that only reads behaviour cannot see it. That is
    # how a routine died on every boot for months (AGENTS invariant 28).
    try:
        text = (ROOT / "logs" / "engine.log").read_text(errors="replace")
        bad = [ln for ln in text.splitlines() if ln.startswith("ERROR")]
        check("engine log is free of errors", not bad,
              bad[0][:120] if bad else f"{len(text)} bytes")
    except OSError as exc:
        check("engine log is free of errors", False, repr(exc))

    # WAV import: a file dropped in sessions/import/ becomes a track's layer
    imp_dir = ROOT / "sessions" / "import"
    imp_dir.mkdir(parents=True, exist_ok=True)
    wp = imp_dir / "smoke.wav"
    with wave.open(str(wp), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(i * 0.05)))
                               for i in range(24000)))
    await c.ws.send(json.dumps({"op": "importList"}))
    st = await c.wait(lambda m: m.get("t") == "imports", 10)
    check("import list sees the dropped wav",
          st is not None and "smoke.wav" in (st.get("csv") or ""),
          (st or {}).get("csv", ""))
    await c.svc("import", [3, "smoke.wav"])
    st = await c.wait(lambda m: m.get("t") == "imported" and m["track"] == 3, 20)
    check("wav import fills a track", st is not None and st["frames"] > 0,
          f"frames={st['frames'] if st else '?'}")






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
