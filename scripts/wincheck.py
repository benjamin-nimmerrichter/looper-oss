#!/usr/bin/env python3
"""M2 — does the protocol actually work on this platform?

The smoke suite is tuned for a native machine: it assumes OSC round trips and
buffer polls land in tens of milliseconds. Under Wine they can take hundreds,
and every timing assertion fails while the feature itself works. This asks the
*functional* questions with generous timeouts instead:

    ./scripts/wincheck.py            # against a running stack (Wine or not)
    ./scripts/wincheck.py --start    # start a stack, run, stop it

Checks: the state contract, a clock that advances, press starts recording, the
take closes into an overdub, stop/play, an export that measures above zero, a
session save and a backup zip. Same protocol as Linux, judged by outcome.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wavcheck import load_float_wav  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = "ws://127.0.0.1:8182"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return ok


def sessions_dir() -> pathlib.Path:
    """Where this run actually keeps its sessions.

    From source it is <repo>/sessions; a packaged build stages itself into a
    per-version home and writes there, which made three working features report
    as failures when this looked only at the repository. The directory holding
    the newest export wins, so it does not matter which one is running.
    """
    candidates = [ROOT / "sessions"]
    for pattern in (
        str(pathlib.Path.home() / ".wine-*/drive_c/users/*/AppData/Local/looper-oss/app/*/sessions"),
        str(pathlib.Path.home() / ".local/share/looper-oss/app/*/sessions"),
    ):
        candidates += [p for p in pathlib.Path("/").glob(pattern.lstrip("/"))]

    def newest(path: pathlib.Path) -> float:
        files = list((path / "exports").glob("*.wav"))
        return max((f.stat().st_mtime for f in files), default=0.0)

    return max(candidates, key=newest)


async def run() -> None:
    import websockets

    async with websockets.connect(WS, open_timeout=30, ping_interval=None) as ws:
        async def send(**kw):
            await ws.send(json.dumps(kw))

        async def wait(pred, timeout):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 1.0))
                except (TimeoutError, ValueError):
                    continue
                if pred(m):
                    return m
            return None

        st = await wait(lambda m: m.get("t") == "s", 120)
        ok = check("engine reaches the bridge", st is not None)
        if st is None or not ok:
            return
        check("state shape (26 globals / 32 track fields)",
              len(st) - 2 == 26 and len(st["tracks"][0]) == 32,
              f"{len(st) - 2}/{len(st['tracks'][0])}/{len(st['tracks'])}")

        # a clock that moves. Wine's audio server processes samples slower than
        # real time, so the phase crawls; the assertion is that it advances at
        # all, and the rate is printed so the slowdown is visible instead of
        # silently accepted.
        phases = set()
        first_phase = None
        t0 = time.monotonic()
        end = t0 + 20
        while time.monotonic() < end:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 1.0))
            except (TimeoutError, ValueError):
                continue
            if m.get("t") == "s":
                if first_phase is None:
                    first_phase = m["phase"]
                phases.add(round(m["phase"], 3))
        rate = (len(phases) / max(1e-9, time.monotonic() - t0))
        check("the clock advances", len(phases) > 1,
              f"{len(phases)} distinct phases, ~{rate:.2f} beat-steps/s")

        # feed a known tone into the track, or a silent take exports silence and
        # the export check would be measuring nothing
        await send(op="svc", what="silence", args=[0])
        await send(op="set", path="g.masterVol", v=0.4)
        await send(op="set", path="t0.input", v=3)
        await send(op="set", path="t0.gateOn", v=0)
        await send(op="set", path="t0.hp", v=20)
        await send(op="set", path="t0.lp", v=19000)
        await send(op="svc", what="testTone", args=[220, 0.3, 240])
        await send(op="set", path="g.bars", v=1)
        await send(op="set", path="g.autoClose", v=1)
        await send(op="act", a="clear", t=0)
        await asyncio.sleep(1.5)
        base_frame = await wait(lambda m: m.get("t") == "s", 20) or st
        base = base_frame["tracks"][0]

        await send(op="act", a="press", t=0)
        rec = await wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 1, 60)
        check("press starts recording", rec is not None,
              f"state={rec['tracks'][0]['state'] if rec else None}")

        # at one bar the take must close by itself and carry on as an overdub
        grew = await wait(
            lambda m: m.get("t") == "s" and m["tracks"][0]["layers"] > base["layers"], 60)
        check("first take closes into an overdub", grew is not None,
              f"layers {base['layers']} -> {grew['tracks'][0]['layers'] if grew else '?'}")

        await send(op="act", a="stop", t=0)
        stopped = await wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 3, 30)
        check("stop stops the track", stopped is not None)
        await send(op="act", a="press", t=0)
        playing = await wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 2, 30)
        check("press resumes playback", playing is not None)

        # export: the file has to contain something, measured, not assumed
        sessions = sessions_dir()
        exports = sessions / "exports"
        before = set(exports.glob("*.wav")) if exports.exists() else set()
        await send(op="svc", what="export", args=[2])
        got = await wait(lambda m: m.get("t") == "exported", 300)
        after = set(exports.glob("*.wav")) if exports.exists() else set()
        fresh = after - before
        check("mixdown export writes a WAV", got is not None and len(fresh) == 1,
              next(iter(fresh)).name if fresh else "none")
        if fresh:
            data, sr = load_float_wav(sorted(fresh)[-1])
            peak = max(abs(v) for v in data) if data else 0.0
            rms = (sum(v * v for v in data) / max(1, len(data))) ** 0.5
            check("the exported mixdown is not silence", peak > 1e-3,
                  f"{len(data)} frames @ {sr}, peak {peak:.4f}, rms {rms:.4f}")

        # files: a session save and a backup zip
        await send(op="svc", what="save", args=[90])
        saved = await wait(lambda m: m.get("t") == "saved" and m["slot"] == 90, 90)
        check("a session saves", saved is not None)
        check("the session wrote its manifest",
              (sessions / "slot90" / "session.json").exists(),
              str(sessions))
        await ws.send(json.dumps({"op": "backup"}))
        backed = await wait(lambda m: m.get("t") == "backup", 180)
        zips = sorted((sessions / "backups").glob("looper-*.zip"))
        check("backup writes one zip", bool(backed and backed.get("ok")) and bool(zips),
              zips[-1].name if zips else "no zip")


def main(argv: list[str]) -> int:
    started = None
    if "--start" in argv:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "sweep.py")], check=False)
        started = subprocess.Popen(
            [sys.executable, "-m", "app.main", "--window", "none"], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**__import__("os").environ, "LOOPER_OSS_KEEP_ALIVE": "1"})
        import urllib.request
        for _ in range(180):
            time.sleep(1)
            try:
                urllib.request.urlopen("http://127.0.0.1:8181/", timeout=3)
                break
            except OSError:
                pass
    try:
        asyncio.run(run())
    finally:
        if started is not None:
            started.terminate()
            subprocess.run([sys.executable, str(ROOT / "scripts" / "sweep.py")], check=False)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  - {name} {detail}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
