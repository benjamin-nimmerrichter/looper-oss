#!/usr/bin/env python3
"""Inspect the WAVs a session slot wrote — offline, no audio device, no sound.

    ./scripts/wavcheck.py                 # newest slot in sessions/
    ./scripts/wavcheck.py sessions/slot2
    ./scripts/wavcheck.py path/to/take.wav

For the loop point it reports the one number that matters: how far the last
sample jumps away from the one before it. A take should end where it began, not
step to zero — that step is the click/buzz you hear on every pass.
"""
from __future__ import annotations

import array
import pathlib
import struct
import sys


def load_float_wav(path: pathlib.Path) -> tuple[list[float], int]:
    """SuperCollider writes 32-bit float WAV, which the stdlib wave module
    refuses (format 3), so parse the RIFF chunks by hand."""
    raw = path.read_bytes()
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise SystemExit(f"{path}: not a RIFF/WAVE file")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        size = struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise SystemExit(f"{path}: no fmt/data chunk")
    tag, channels, rate, _, _, bits = fmt
    if tag != 3 or bits != 32:
        raise SystemExit(f"{path}: expected 32-bit float ({tag}/{bits})")
    samples = array.array("f")
    samples.frombytes(data[: len(data) // 4 * 4])
    if channels > 1:
        samples = samples[0::channels]
    return list(samples), rate


def rms(seg: list[float]) -> float:
    return (sum(v * v for v in seg) / max(1, len(seg))) ** 0.5


def slew(seg: list[float]) -> float:
    return max((abs(seg[i] - seg[i - 1]) for i in range(1, len(seg))), default=0.0)


def report(path: pathlib.Path) -> bool:
    """Level-independent checks. A buzz that is as loud as the take disappears in
    RMS/peak, so everything below is a *ratio* against the body of the take.

    Two things can go wrong at a loop point:
      - a hole: the final frame was never written and reads zero  -> internal step
      - a burst: high-frequency energy the body does not have     -> HF ratio
    A plain jump from the last sample to the first is normal for real material and
    is what the seam window exists to smooth, so it is reported, not failed.
    """
    d, sr = load_float_wav(path)
    n = len(d) - 1
    if n < 2000:
        print(f"{path.name}: {len(d)} frames — too short to judge")
        return True

    body = d[len(d) // 3: len(d) // 3 + 20000]
    ms5 = int(sr * 0.005)
    tail = d[-ms5:]
    head = d[:ms5]

    def slew(seg):
        return max((abs(seg[i] - seg[i - 1]) for i in range(1, len(seg))), default=0.0)

    def hf(seg):
        return max((abs(seg[i] - 2 * seg[i - 1] + seg[i - 2])
                    for i in range(2, len(seg))), default=0.0)

    b_slew, b_hf = slew(body), hf(body)
    t_slew, t_hf = slew(tail), hf(tail)
    h_hf = hf(head)
    hole = abs(d[n] - d[n - 1])
    wrap = abs(d[0] - d[n])

    guard = max(b_slew, 1e-6)
    hole_ratio = hole / guard
    tail_ratio = t_hf / max(b_hf, 1e-9)
    head_ratio = h_hf / max(b_hf, 1e-9)

    # Where does the audio actually stop? A take whose signal dies before the loop
    # end leaves a step at the stop point -- audible as a click even though the
    # final frames are (innocently) zero, which is what a ratio check misses.
    peak = max(abs(v) for v in d)
    floor_ = max(peak * 0.01, 1e-5)
    last_audio = n
    for i in range(n, -1, -1):
        if abs(d[i]) > floor_:
            last_audio = i
            break
    ends_early_ms = (n - last_audio) / sr * 1000.0

    # A punch-out legitimately ends early and quietly; what matters is how big a
    # step the audio makes on its way out. >2% of peak is an audible tick.
    end_step = abs(d[last_audio])
    end_pct = end_step / max(peak, 1e-9) * 100.0

    quiet = peak < 1e-4
    ok = quiet or (hole_ratio <= 3.0 and tail_ratio <= 3.0
                   and head_ratio <= 3.0 and end_pct <= 2.0)

    print(f"{path.name}  ({len(d)} frames, {len(d) / sr:.2f} s, peak {max(abs(v) for v in d):.3f})")
    if quiet:
        print("  silent take — nothing to judge")
        return True
    print(f"  body      |dx|={b_slew:.5f}  |d2x|={b_hf:.5f}   (reference)")
    print(f"  last 5 ms |dx|={t_slew:.5f}  |d2x|={t_hf:.5f}"
          f"   -> slew x{t_slew / guard:.1f}, HF x{tail_ratio:.1f}")
    print(f"  first 5ms                |d2x|={h_hf:.5f}   -> HF x{head_ratio:.1f}")
    print(f"  hole at the final frame  |d[n]-d[n-1]| = {hole:.5f}   x{hole_ratio:.1f} the body")
    print(f"  wrap |d[0]-d[n]| = {wrap:.5f}   (normal for real material; the seam window smooths it)")
    print(f"  audio stops {ends_early_ms:.1f} ms before the loop end, "
          f"with a {end_pct:.1f}% step"
          f"{'   <-- CLICK AT THE STOP POINT' if end_pct > 2.0 else '   (faded)'}")
    print("  " + ("ok" if ok else "BUZZ AT THE LOOP POINT"))
    return ok


def main(argv: list[str]) -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    targets: list[pathlib.Path] = []
    if len(argv) > 1:
        for a in argv[1:]:
            p = pathlib.Path(a)
            targets.extend(sorted(p.glob("*.wav")) if p.is_dir() else [p])
    else:
        slots = sorted((root / "sessions").glob("slot*/**/*.wav"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not slots:
            print("no session WAVs — record something and save a slot first")
            return 1
        targets = [slots[0]]

    bad = 0
    for t in targets:
        if not t.exists():
            print(f"{t}: missing")
            bad += 1
            continue
        if not report(t):
            bad += 1
    print(f"\n{len(targets) - bad}/{len(targets)} clean")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
