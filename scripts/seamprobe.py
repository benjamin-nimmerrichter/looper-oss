#!/usr/bin/env python3
"""M0 — measure the loop seam instead of describing it.

Records a steady 220 Hz tone as take 1, lets `autoClose` + `autoDub` roll straight
into the overdub (the exact moment the metallic buzz was reported), stops, plays
and exports, then reports **band energy across the take** so a burst can be
located in time rather than guessed at.

    ./scripts/seamprobe.py                # drive a running stack, then measure
    ./scripts/seamprobe.py --measure WAV  # measure a file you already have
    ./scripts/seamprobe.py --start        # start the stack first, kill it after

Why a band and not RMS: a buzz as loud as the take disappears in RMS. The report
is a *ratio* between the 5-15 kHz energy in a window and the same energy in the
body of the take, which is level-independent and locates the artefact.

No numpy: a Goertzel per band frequency keeps it dependency-free, like the rest
of `scripts/`.
"""
from __future__ import annotations

import asyncio
import json
import math
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wavcheck import load_float_wav  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
WS = "ws://127.0.0.1:8182"
BAND_LO, BAND_HI = 5000.0, 15000.0
BAND_STEPS = 24
WIN_MS = 100.0          # the artefact is ~100 ms long: use it as the window
HOP_MS = 50.0


def goertzel(seg: list[float], sr: int, freq: float) -> float:
    """Power at one frequency. Cheap, no FFT, good enough to compare windows."""
    w = 2.0 * math.pi * freq / sr
    coeff = 2.0 * math.cos(w)
    s0 = s1 = s2 = 0.0
    for v in seg:
        s0 = v + coeff * s1 - s2
        s2, s1 = s1, s0
    return (s1 * s1 + s2 * s2 - coeff * s1 * s2) / max(1, len(seg)) ** 2


def band_power(seg: list[float], sr: int) -> float:
    if len(seg) < 32:
        return 0.0
    freqs = [BAND_LO + (BAND_HI - BAND_LO) * i / (BAND_STEPS - 1) for i in range(BAND_STEPS)]
    return sum(goertzel(seg, sr, f) for f in freqs) / len(freqs)


def analyse(path: pathlib.Path) -> dict:
    data, sr = load_float_wav(path)
    if len(data) < sr // 10:
        raise SystemExit(f"{path.name}: {len(data)} frames — nothing to measure")
    win = int(sr * WIN_MS / 1000.0)
    hop = int(sr * HOP_MS / 1000.0)
    windows = []
    for start in range(0, max(1, len(data) - win), hop):
        seg = data[start:start + win]
        windows.append({
            "t": start / sr,
            "band": band_power(seg, sr),
            "rms": (sum(v * v for v in seg) / len(seg)) ** 0.5,
            "peak": max(abs(v) for v in seg),
        })
    body = sorted(w["band"] for w in windows)
    median = body[len(body) // 2] if body else 0.0
    guard = max(median, 1e-18)
    for w in windows:
        w["ratio"] = w["band"] / guard
    worst = sorted(windows, key=lambda w: -w["ratio"])[:5]
    # the loop point: where the take wraps. Find it by peak level, not by assuming
    # a length -- the tone fills the whole take, so the file is the loop repeated.
    steps = [abs(data[i] - data[i - 1]) for i in range(1, len(data))]
    body_step = sorted(steps)[int(len(steps) * 0.5)] if steps else 0.0
    max_step = max(steps) if steps else 0.0
    max_at = steps.index(max_step) / sr if steps else 0.0
    return {
        "name": path.name, "frames": len(data), "sr": sr,
        "seconds": len(data) / sr, "peak": max(abs(v) for v in data),
        "median_band": median, "worst": worst,
        "body_step": body_step, "max_step": max_step, "max_step_at": max_at,
        "rms": (sum(v * v for v in data) / len(data)) ** 0.5,
    }


def report(r: dict) -> bool:
    print(f"{r['name']}  {r['seconds']:.2f} s, {r['frames']} frames @ {r['sr']} Hz, "
          f"peak {r['peak']:.3f}, rms {r['rms']:.4f}")
    print("  worst 5-15 kHz windows (ratio against the take's median):")
    for w in r["worst"]:
        print(f"    t={w['t']:6.2f}s  band x{w['ratio']:6.1f}   rms {w['rms']:.4f}   peak {w['peak']:.3f}")
    top = r["worst"][0]["ratio"] if r["worst"] else 0.0
    print(f"  biggest sample step {r['max_step']:.5f} at t={r['max_step_at']:.2f}s "
          f"(median step {r['body_step']:.5f}, x{r['max_step'] / max(r['body_step'], 1e-9):.1f})")
    if r["peak"] < 1e-4:
        print("  silent take -- nothing to judge")
        return False
    ok = top < 4.0
    print(f"  verdict: {'no burst found' if ok else 'BURST — 5-15 kHz energy '
                                      f'{top:.1f}x the body'}")
    return ok


async def drive() -> pathlib.Path:
    import websockets

    exports = ROOT / "sessions" / "exports"
    before = set(exports.glob("*.wav")) if exports.exists() else set()

    async with websockets.connect(WS) as ws:
        async def send(**kw):
            await ws.send(json.dumps(kw))

        async def wait(pred, timeout=20):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), 0.5))
                except (TimeoutError, ValueError):
                    continue
                if pred(m):
                    return m
            return None

        # s.mute also silences the captured bus, so the take is made *quiet*
        # rather than muted: the export has to measure something.
        # commands are no-ops until the synthdefs are up (invariant 21), so wait
        # for the first state frame before poking anything
        ready = await wait(lambda m: m.get("t") == "s", 90)
        if ready is None:
            raise SystemExit("engine never sent a state frame")
        print("engine ready")

        await send(op="svc", what="silence", args=[1])
        await send(op="set", path="g.masterVol", v=0.8)
        await send(op="set", path="g.bars", v=1)
        await send(op="set", path="g.busA", v=1)
        await send(op="set", path="g.busB", v=0)
        await send(op="set", path="g.busC", v=0)
        await send(op="set", path="t0.route", v=0)
        await send(op="set", path="t0.speed", v=1)
        await send(op="set", path="t0.input", v=3)
        await send(op="set", path="t0.gateOn", v=0)
        await send(op="set", path="t0.hp", v=20)
        await send(op="set", path="t0.lp", v=19000)
        await send(op="svc", what="testTone", args=[220, 0.25, 90])
        await send(op="act", a="clear", t=0)
        await asyncio.sleep(0.6)
        cl = await wait(lambda m: m.get("t") == "s", 4)
        if cl:
            print(f"after clear: state={cl['tracks'][0]['state']} "
                  f"layers={cl['tracks'][0].get('layers')}")

        await send(op="act", a="press", t=0)
        # log the transitions instead of guessing which one we caught
        seen, end = [], time.monotonic() + 6.0
        while time.monotonic() < end:
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 0.5))
            except (TimeoutError, ValueError):
                continue
            if m.get("t") == "s":
                t0 = m["tracks"][0]
                key = (t0["state"], t0.get("layers"))
                if not seen or seen[-1] != key:
                    seen.append(key)
                    print(f"  t={6.0 - (end - time.monotonic()):4.1f}s state={key[0]} layers={key[1]}")
        print(f"recorded states: {seen}")
        await send(op="svc", what="probe", args=[0])
        pr = await wait(lambda m: m.get("t") == "probe", 8)
        print(f"layer peaks: {pr.get('peaks') if pr else None}")
        # two passes: the first take closes at the bar and the overdub starts by
        # itself, which is exactly the hand-off that was reported as buzzing
        print("rolling through the hand-off ...")
        await asyncio.sleep(4.6)
        st = await wait(lambda m: m.get("t") == "s", 3)
        if st:
            t0 = st["tracks"][0]
            print(f"after the hand-off: state={t0['state']} layers={t0.get('layers')}")
        # commit the overdub, stop, then PLAY: pressing once more while autoDub is
        # on only commits and starts ANOTHER take (state stays 1), and exporting
        # while a take is open captures a half-open loop.
        await send(op="act", a="press", t=0)
        await asyncio.sleep(0.6)
        await send(op="act", a="stop", t=0)
        await asyncio.sleep(0.6)
        await send(op="act", a="press", t=0)          # play
        await asyncio.sleep(0.8)
        st = await wait(lambda m: m.get("t") == "s" and m["tracks"][0]["state"] == 2, 6)
        t0 = st["tracks"][0] if st else None
        print(f"before export: state={t0['state'] if t0 else None} "
              f"layers={t0.get('layers') if t0 else None}")
        if t0:
            interesting = {k: v for k, v in t0.items()
                           if k in ("gains", "undo", "meter", "peak", "vol",
                                    "playing", "recOn", "speed", "route")}
            print(f"  track: {interesting}")
        gl = await wait(lambda m: m.get("t") == "s" and "g" in m, 3)
        if gl:
            print(f"  globals: { {k: gl['g'][k] for k in gl['g'] if k in ('busA','busB','busC','playing','gridOn','phase')} }")
        await send(op="svc", what="export", args=[4])
        await wait(lambda m: m.get("t") == "exported", 40)
        await send(op="svc", what="silence", args=[1])
        await send(op="set", path="g.masterVol", v=1)
        await send(op="act", a="stopAll", t=0)

    after = sorted(exports.glob("*.wav"))
    fresh = [p for p in after if p not in before]
    if not fresh:
        raise SystemExit("no new export appeared in sessions/exports")
    return fresh[-1]


def main(argv: list[str]) -> int:
    started = None
    if "--start" in argv:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "sweep.py")], check=False,
                       stdout=subprocess.DEVNULL)
        started = subprocess.Popen(
            [sys.executable, "-m", "app.main", "--window", "none"], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**__import__("os").environ, "LOOPER_OSS_KEEP_ALIVE": "1"})
        import urllib.request
        for attempt in range(120):
            time.sleep(1)
            try:
                urllib.request.urlopen("http://127.0.0.1:8181/", timeout=2)
                print("stack up", flush=True)
                break
            except OSError as exc:
                # one line per 10 s: silence here would look like a hang
                if attempt % 10 == 9:
                    print(f"  waiting for :8181 ({exc})", file=sys.stderr, flush=True)
    try:
        if "--measure" in argv:
            path = pathlib.Path(argv[argv.index("--measure") + 1])
        else:
            path = asyncio.run(drive())
        ok = report(analyse(path))
        return 0 if ok else 1
    finally:
        if started is not None:
            started.terminate()
            subprocess.run([sys.executable, str(ROOT / "scripts" / "sweep.py")],
                           check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
