#!/usr/bin/env python3
"""Kill leftovers from a previous Looper OSS run.

A crashed run can leave the bridge holding its ports, or an orphaned scsynth
answering on :57110 with a completely different SynthDef set. Both make the next
start fail in confusing ways, so run.sh sweeps first.

Deliberately not `pkill -f pattern`: that pattern text also appears in the
calling shell's own command line, so pkill ends up killing the caller.
"""
from __future__ import annotations

import contextlib
import os
import signal
import sys


def is_scsynth(argv: list[str]) -> bool:
    return argv[0].rsplit("/", 1)[-1] == "scsynth"


def is_sclang(argv: list[str]) -> bool:
    return argv[0].rsplit("/", 1)[-1] == "sclang"


def is_bridge(argv: list[str]) -> bool:
    return (
        len(argv) >= 2
        and argv[0].rsplit("/", 1)[-1].startswith("python")
        and argv[1].endswith("bridge/bridge.py")
    )


def is_ui_server(port: str):
    def match(argv: list[str]) -> bool:
        return (
            len(argv) >= 3
            and argv[0].rsplit("/", 1)[-1].startswith("python")
            and "http.server" in argv
            and port in argv
        )
    return match


TARGETS = (("scsynth", is_scsynth), ("sclang", is_sclang), ("bridge", is_bridge))


def read_argv(pid: str) -> list[str]:
    path = f"/proc/{pid}/cmdline"
    try:
        with open(path, "rb") as fh:
            return [a.decode(errors="ignore") for a in fh.read().split(b"\0") if a]
    except OSError:
        return []  # process vanished between listdir and read



def sweep(verbose: bool = True, ui_port: str | None = None) -> int:
    targets = TARGETS + ((("ui server", is_ui_server(ui_port)),) if ui_port else ())
    try:
        pids = os.listdir("/proc")
    except OSError as exc:
        print(f"sweep: cannot scan /proc ({exc})")
        return 0

    me = os.getpid()
    killed = 0
    for name in pids:
        if not name.isdigit():
            continue
        try:
            pid = int(name)
        except ValueError:
            continue
        if pid == me:
            continue
        argv = read_argv(name)
        if not argv:
            continue
        for label, match in targets:
            if match(argv):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGTERM)
                    killed += 1
                    if verbose:
                        print(f"sweep: stopped stale {label} (pid {pid})")
                break
    return killed


if __name__ == "__main__":
    port = next((a for a in sys.argv[1:] if a.isdigit()), None)
    sweep("-q" not in sys.argv, port)
    sys.exit(0)
