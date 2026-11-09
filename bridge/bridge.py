#!/usr/bin/env python3
"""Looper OSS bridge: engine (OSC) <-> browser (WebSocket JSON).

The engine sends flat positional OSC; this file is the only place that knows the
field order, and the only place that speaks OSC. Above it is plain JSON.

Also owns ui/config.json: settings changed in the UI are written back here and
the generated engine config is refreshed, so a restart picks them up.
"""
import asyncio
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import zipfile
from typing import Any, cast

try:
    import websockets  # pyright: ignore[reportMissingImports]
    from pythonosc.dispatcher import Dispatcher  # pyright: ignore[reportMissingImports]
    from pythonosc.osc_server import AsyncIOOSCUDPServer  # pyright: ignore[reportMissingImports]
    from pythonosc.udp_client import SimpleUDPClient  # pyright: ignore[reportMissingImports]
except ImportError as exc:  # pragma: no cover - startup dependency check
    raise SystemExit(
        f"bridge: missing python package ({exc})\n"
        f"  reinstall Looper OSS, or run ./install.sh once from a source check-out"
    ) from exc

from write_config import write_from  # noqa: E402  (same directory)

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_JSON = ROOT / "ui" / "config.json"
ENGINE_LOG = ROOT / "logs" / "engine.log"
CONFIG_SCD = ROOT / "engine" / "_config.scd"

try:
    CFG: dict[str, Any] = json.loads(CONFIG_JSON.read_text())
except (OSError, ValueError) as exc:
    raise SystemExit(f"bridge: cannot read ui/config.json ({exc})") from exc

N = int(CFG.get("tracks", 5))
ENGINE_PORT = int(CFG["osc"]["enginePort"])
STATE_PORT = int(CFG["osc"]["statePort"])
WS_PORT = int(CFG["ui"].get("wsPort", 8182))

# field order must match ~sendState in engine/looper.scd
GLOBAL_FIELDS = [
    "bpm", "bars", "beatsPerBar", "metro", "countIn", "masterVol", "clickVol",
    "sel", "cpu", "revMix", "room", "xfadeMs", "recordOffset", "dubDecay",
    "autoClose", "masterHp", "masterLp", "version",
    "inL", "inR", "masterL", "masterR", "phase",
    "busA", "busB", "busC",
]
TRACK_FIELDS = [
    "state", "vol", "pan", "muted", "meter", "input", "layers",
    "gateOn", "gateThr", "hp", "lp",
    "compOn", "compThr", "compRatio", "route", "cueOn", "cueSend", "seqOn",
    "delayOn", "delayBeats", "delayDecay", "delayMix",
    "revSend", "gain", "recGain", "reverse", "fadeIn", "fadeOut",
    "loopRatio", "speed", "layerPeak", "undoDepth",
]

# config.json keys that the running engine can adopt without a restart
CFG_TO_ENGINE = {
    "bpm": "bpm",
    "bars": "bars",
    "beatsPerBar": "beatsPerBar",
    "loop.crossfadeMs": "xfadeMs",
    "loop.recordOffset": "recordOffset",
    "loop.dubDecay": "dubDecay",
    "loop.autoClose": "autoClose",
    "loop.quantizeStart": "quantizeStart",
    "loop.autoDub": "autoDub",
    "loop.overdubMode": "overdubMode",
    "rhythm.on": "rhythmOn",
    "rhythm.kit": "rhythmKit",
    "rhythm.pattern": "rhythmPattern",
    "loop.autosave": "autosave",
    "loop.revMix": "revMix",
    "loop.room": "room",
    "clock.clickVol": "clickVol",
    "clock.countInBars": "countIn",
    "loop.monitor": "monitor",
}

# config.json keys that need the engine to restart
CFG_NEEDS_RESTART = {
    "tracks", "maxLayers", "loop.maxSeconds", "audio.blockSize",
    "audio.hardwareBufferSize", "audio.device", "audio.in", "audio.out",
    "audio.sampleRate", "audio.driver", "audio.quantum",
}

engine = SimpleUDPClient("127.0.0.1", ENGINE_PORT)
quit_task: Any = None
clients: set = set()
last_state: dict[str, Any] | None = None
# readiness is state, not an event: a UI that connects after the engine booted
# must still learn that it booted, or it shows the boot screen for ever.
engine_ready: dict[str, Any] | None = None
engine_failed: str | None = None


# --------------------------------------------------------------------- utils --
def log(*args: Any) -> None:
    print("bridge:", *args, flush=True)


async def broadcast(payload: dict) -> None:
    if not clients:
        return
    msg = json.dumps(payload, separators=(",", ":"))
    await asyncio.gather(*[ws.send(msg) for ws in list(clients)], return_exceptions=True)


def push(payload: dict) -> None:
    asyncio.ensure_future(broadcast(payload))


def inum(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def fnum(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def get_path(cfg: dict, dotted: str) -> Any:
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_path(cfg: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = cfg
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


# ------------------------------------------------------------------- devices --
def read_clock() -> dict:
    """PipeWire's current period: under JACK this is the real buffer size, and
    scsynth follows it — SC's own blockSize option is ignored."""
    out = {"quantum": 0, "rate": 0}
    if os.name == "nt":
        return out       # no PipeWire clock to read
    try:
        res = subprocess.run(["pw-metadata", "-n", "settings", "0"],
                             capture_output=True, text=True, timeout=4, check=False)
        for line in res.stdout.splitlines():
            for key in ("clock.quantum", "clock.rate"):
                if f"key:'{key}'" in line and "value:'" in line:
                    out["quantum" if key.endswith("quantum") else "rate"] = \
                        inum(line.split("value:'")[1].split("'")[0])
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"pw-metadata unavailable ({exc})")
    return out


def apply_quantum(frames: int) -> None:
    frames = int(frames)
    if frames <= 0 or os.name == "nt":
        # There is no PipeWire on Windows: the period belongs to the driver, and
        # calling pw-metadata there only produced "cannot set the quantum" on
        # every start -- a failure message about something that does not exist.
        return
    try:
        res = subprocess.run(["pw-metadata", "-n", "settings", "0",
                              "clock.force-quantum", str(frames)],
                             capture_output=True, timeout=4, check=False)
        if res.returncode == 0:
            log(f"pipewire quantum forced to {frames} frames")
        else:
            log(f"pw-metadata could not force the quantum (exit {res.returncode})")
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"cannot set the quantum ({exc})")


def list_audio_devices() -> dict:
    """What can be chosen, and what the engine is already connected to.

    `audio.device` is an ALSA hardware name (scsynth's -H). On JACK/PipeWire there
    is nothing to choose: scsynth is just another node in the graph, so the useful
    information is which ports feed it and where its output goes.
    """
    out: dict[str, Any] = {"sinks": [], "sources": [], "alsa": [],
                           "links": {"inputs": [], "outputs": []}}

    try:
        dump = subprocess.run(["pw-dump"], capture_output=True, text=True,
                              timeout=4, check=False)
        if dump.returncode == 0 and dump.stdout.strip():
            for node in json.loads(dump.stdout):
                props = (node.get("info") or {}).get("props") or {}
                media = props.get("media.class", "")
                if media not in ("Audio/Sink", "Audio/Source"):
                    continue
                name = props.get("node.name") or props.get("node.description")
                if not name:
                    continue
                entry = {"id": name, "label": props.get("node.description") or name}
                out["sinks" if media == "Audio/Sink" else "sources"].append(entry)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        log(f"pw-dump unavailable ({exc})")

    try:
        cards = subprocess.run(["aplay", "-l"], capture_output=True, text=True,
                               timeout=4, check=False)
        if cards.returncode == 0:
            for line in cards.stdout.splitlines():
                line = line.strip()
                if not line.startswith("card "):
                    continue
                try:
                    card_no = line.split()[1].rstrip(":")
                    dev_no = line.split("device ")[1].split(":")[0].strip()
                    label = line.split(": ", 1)[1] if ": " in line else line
                except (IndexError, ValueError):
                    continue
                out["alsa"].append({"id": f"hw:{card_no},{dev_no}", "label": label})
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"aplay unavailable ({exc})")

    try:
        links = subprocess.run(["pw-link", "-l"], capture_output=True, text=True,
                               timeout=4, check=False)
        if links.returncode == 0:
            current = ""
            for line in links.stdout.splitlines():
                if not line.strip():
                    current = ""
                    continue
                if not line.startswith(" ") and ":" in line:
                    current = line.strip()
                    continue
                pair = line.strip().lstrip("|<-").lstrip("|->").strip()
                if not pair:
                    continue
                if line.strip().startswith("|->"):
                    src, dst = current, pair
                else:
                    src, dst = pair, current
                if src.startswith("SuperCollider:out"):
                    out["links"]["outputs"].append(dst)
                if dst.startswith("SuperCollider:in"):
                    out["links"]["inputs"].append(src)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"pw-link unavailable ({exc})")

    for key in ("inputs", "outputs"):
        out["links"][key] = sorted(set(out["links"][key]))

    # Windows (and any machine without PipeWire): there is no pw-dump to ask, so
    # report what the engine actually opened. scsynth prints the backend and the
    # endpoint names when it boots, and that is the only truthful answer --
    # enumerating devices we are not using would be a lie dressed as a list.
    if not (out["sinks"] or out["sources"] or out["alsa"]):
        opened = opened_from_log()
        if opened:
            out["links"]["inputs"] = [opened.get("in", "")]
            out["links"]["outputs"] = [opened.get("out", "")]
            if opened.get("out"):
                out["alsa"].append({"id": opened["out"], "label": opened["out"]})

    out.update(read_clock())
    return out



def opened_from_log() -> dict[str, str]:
    """The device scsynth opened, from its own boot log.

    `Booting with:` is followed by `  In: <backend> : <endpoint>` and
    `  Out: ...`. On Linux the PipeWire graph answers this question properly;
    on Windows there is nothing to interrogate, so the log is the source.
    """
    found: dict[str, str] = {}
    if not ENGINE_LOG.exists():
        return found
    try:
        text = ENGINE_LOG.read_text(errors="replace")
    except OSError:
        return found
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("In:") and "in" not in found:
            found["in"] = stripped[3:].strip()
        elif stripped.startswith("Out:") and "out" not in found:
            found["out"] = stripped[4:].strip()
    return found



# ------------------------------------------------------------------- ui files --
# The page and its WebSocket are served from the SAME origin and port. Chromium
# (Chrome 142 / Edge 143 onward) asks permission before a page may talk to a
# local network destination, and a socket on a second port can be blocked with
# nothing in the log to show for it: the page loads, the socket never opens, the
# boot screen waits for ever. One origin removes the question entirely.
UI_ROOT: pathlib.Path | None = None
_ui_seen: set[str] = set()
_ws_seen = False


# Explicit, never from the registry: Python's mimetypes reads Windows' file
# associations, where a .js file is commonly registered as "text/plain" -- and a
# browser refuses to run an ES module served as text/plain ("Strict MIME type
# checking is enforced for module scripts"). That one line cost several rounds of
# debugging on a real Windows machine while Linux served text/javascript happily.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}


def _content_type(path: Any) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _ui_response(request: Any) -> Any:
    from http import HTTPStatus

    from websockets.datastructures import Headers
    from websockets.http11 import Response

    if UI_ROOT is None:
        return None
    path = request.path.split("?", 1)[0]
    if path in ("/", ""):
        path = "/index.html"
    target = (UI_ROOT / path.lstrip("/")).resolve()
    try:
        target.relative_to(UI_ROOT.resolve())
    except ValueError:
        return Response(HTTPStatus.FORBIDDEN, "Forbidden", Headers(), b"")
    if not target.is_file():
        return Response(HTTPStatus.NOT_FOUND, "Not found", Headers(), b"")
    body = target.read_bytes()
    headers = Headers()
    headers["Content-Type"] = _content_type(target)
    headers["Content-Length"] = str(len(body))
    headers["Cache-Control"] = "no-store"      # a rebuilt UI must apply on reload
    return Response(HTTPStatus.OK, "OK", headers, body)


def process_request(connection: Any, request: Any) -> Any:
    """WebSocket handshakes pass through; plain GETs are answered with the UI.

    Both are logged once: "nothing connects" has three very different causes and
    this line tells them apart -- page never loaded, handshake refused, handler
    failing.
    """
    if request.headers.get("Upgrade"):
        global _ws_seen
        if not _ws_seen:
            _ws_seen = True
            log(f"ws handshake from {request.path}")
        return None
    seen = _ui_seen
    if request.path not in seen:
        seen.add(request.path)
        log(f"served {request.path}")
    return _ui_response(request)


# --------------------------------------------------------------------- state --
def on_state(_addr: str, *args: Any) -> None:
    global last_state
    if len(args) < len(GLOBAL_FIELDS) + len(TRACK_FIELDS) * N:
        return
    state = dict(zip(GLOBAL_FIELDS, args[: len(GLOBAL_FIELDS)], strict=True))
    trackvals = args[len(GLOBAL_FIELDS):]
    state["t"] = "s"
    state["tracks"] = [
        dict(
            zip(
                TRACK_FIELDS,
                trackvals[i * len(TRACK_FIELDS): (i + 1) * len(TRACK_FIELDS)],
                strict=True,
            )
        )
        for i in range(N)
    ]
    last_state = state
    push(state)


def on_pos(_addr: str, *args: Any) -> None:
    if args:
        push({"t": "pos", "ph": fnum(args[0])})


def on_beat(_addr: str, *args: Any) -> None:
    if args:
        push({"t": "beat", "beat": inum(args[0])})


def on_wrap(_addr: str, *args: Any) -> None:
    push({"t": "wrap"})


def on_meter(_addr: str, *args: Any) -> None:
    if len(args) >= 3:
        push(
            {
                "t": "m",
                "i": inum(args[0]),
                "l": fnum(args[1]),
                "r": fnum(args[2]),
                "peak": fnum(args[3]) if len(args) > 3 else 0.0,
            }
        )


def on_master(_addr: str, *args: Any) -> None:
    if len(args) >= 2:
        push({"t": "master", "l": fnum(args[0]), "r": fnum(args[1])})


def on_input(_addr: str, *args: Any) -> None:
    if len(args) >= 2:
        push({"t": "in", "l": fnum(args[0]), "r": fnum(args[1])})


def on_stats(_addr: str, *args: Any) -> None:
    if len(args) >= 8:
        push(
            {
                "t": "stats",
                "cpu": fnum(args[0]),
                "sr": fnum(args[1]),
                "blockSize": inum(args[2]),
                "hwBuffer": inum(args[3]),
                "latency": fnum(args[4]),
                "running": inum(args[5]),
                "vst": inum(args[6]),
                "vstCount": inum(args[7]),
            }
        )


def on_vst(_addr: str, *args: Any) -> None:
    push({"t": "vst", "csv": str(args[0]) if args else ""})


def on_vst_catalog(_addr: str, *args: Any) -> None:
    push({"t": "vstcatalog", "csv": str(args[0]) if args else ""})


def on_midi_map(_addr: str, *args: Any) -> None:
    csv = str(args[0]) if args else ""
    entries = [e for e in csv.split(";") if e]
    # the learned map *is* configuration: remember it across restarts
    if CFG["midi"].get("map") != entries:
        set_path(CFG, "midi.map", entries)
        try:
            CONFIG_JSON.write_text(json.dumps(CFG, indent=2) + "\n", newline="\n")
            write_from(str(CONFIG_JSON), str(CONFIG_SCD))
        except OSError as exc:
            log(f"cannot persist midi map ({exc})")
    push({"t": "midimap", "csv": csv})


def on_tap(_addr: str, *args: Any) -> None:
    push({
        "t": "tap",
        "bpm": fnum(args[0]) if args else 0,
        "taps": inum(args[1]) if len(args) > 1 else 0,
    })


def on_probe(_addr: str, *args: Any) -> None:
    # track, layers, contentLen, recOn, playing, gains[maxLayers], peaks[maxLayers]
    push({
        "t": "probe",
        "track": inum(args[0]) if args else 0,
        "layers": inum(args[1]) if len(args) > 1 else 0,
        "contentLen": inum(args[2]) if len(args) > 2 else 0,
        "recOn": inum(args[3]) if len(args) > 3 else 0,
        "playing": inum(args[4]) if len(args) > 4 else 0,
        "meterL": fnum(args[5]) if len(args) > 5 else 0,
        "meterR": fnum(args[6]) if len(args) > 6 else 0,
        "gains": [fnum(a) for a in args[7:11]],
        "peaks": [fnum(a) for a in args[11:15]],
    })


def on_inputs(_addr: str, *args: Any) -> None:
    push({"t": "inputs", "csv": str(args[0]) if args else ""})


def on_vst_banks(_addr: str, *args: Any) -> None:
    """Four FX banks per track: track:bank:key>key>key>key, tracks by ";" ."""
    csv = str(args[0]) if args else ""
    tracks = []
    for chunk in [x for x in csv.split(";") if x]:
        banks = []
        for bank in chunk.split(":"):
            banks.append([k if k else None for k in bank.split(">")])
        tracks.append(banks)
    if CFG.setdefault("vst", {}).get("banks") != tracks:
        CFG["vst"]["banks"] = tracks
        try:
            CONFIG_JSON.write_text(json.dumps(CFG, indent=2) + "\n", newline="\n")
            write_from(str(CONFIG_JSON), str(CONFIG_SCD))
        except OSError as exc:
            log(f"cannot persist vst banks ({exc})")
    push({"t": "vstbanks", "csv": csv})


def on_import(_addr: str, *args: Any) -> None:
    push({"t": "imported",
          "track": inum(args[0]) if args else 0,
          "name": str(args[1]) if len(args) > 1 else "",
          "frames": inum(args[2]) if len(args) > 2 else 0})


def on_rhythm(_addr: str, *args: Any) -> None:
    push({"t": "rhythm",
          "on": inum(args[0]) if args else 0,
          "kit": inum(args[1]) if len(args) > 1 else 0,
          "pattern": inum(args[2]) if len(args) > 2 else 0})


def on_midi_devices(_addr: str, *args: Any) -> None:
    push({"t": "mididevs", "csv": str(args[0]) if args else ""})


def on_midi_event(_addr: str, *args: Any) -> None:
    push({"t": "midi", "what": str(args[0]), "num": args[1] if len(args) > 1 else 0,
          "target": args[2] if len(args) > 2 else ""})


def on_auto(_addr: str, *args: Any) -> None:
    if args:
        push({"t": "auto", "i": inum(args[0])})


def on_boot_fail(_addr: str, *args: Any) -> None:
    why = str(args[0]) if args else "unknown"
    log(f"ENGINE BOOT FAILED: {why}")
    global engine_failed, engine_ready
    engine_failed = why
    engine_ready = None
    push({"t": "bootfail", "msg": why})


def on_ready(_addr: str, *args: Any) -> None:
    global engine_ready, engine_failed
    tracks = inum(args[0]) if args else N
    engine_ready = {"t": "ready", "tracks": tracks}
    engine_failed = None
    log(f"engine ready ({tracks} tracks)")
    push(engine_ready)
    engine.send_message("/ld/svc", ["stats"])


def on_saved(_addr: str, *args: Any) -> None:
    slot = inum(args[0]) if args else 0
    written = inum(args[1]) if len(args) > 1 else 0
    directory = str(args[2]) if len(args) > 2 else ""
    if last_state and directory:
        # the engine works in relative paths ("sessions/slotN")
        raw = pathlib.Path(directory)
        sess = (raw if raw.is_absolute() else ROOT / raw).resolve()
        if not sess.is_relative_to(ROOT.resolve()):
            sess = ROOT / "sessions"
        sess.mkdir(parents=True, exist_ok=True)
        out = (sess / "session.json").resolve()
        if out.is_relative_to(ROOT.resolve()):
            out.write_text(json.dumps({"state": last_state, "slot": slot}, indent=2),
                       newline="\n")
    push({"t": "saved", "slot": slot, "layers": written, "dir": directory})


def on_loaded(_addr: str, *args: Any) -> None:
    slot = inum(args[0]) if args else 0
    layers = inum(args[1]) if len(args) > 1 else 0
    push({"t": "loaded", "slot": slot, "layers": layers})


def on_loaderr(_addr: str, *args: Any) -> None:
    push({"t": "error", "msg": f"session {args[0] if args else '?'}: "
                              f"{args[1] if len(args) > 1 else 'not found'}"})


def on_capturing(_addr: str, *args: Any) -> None:
    push({"t": "capturing", "seconds": fnum(args[0]) if args else 30.0})


def on_exported(_addr: str, *args: Any) -> None:
    push({"t": "exported", "path": str(args[0]) if args else ""})


# ------------------------------------------------------------------ commands --
def handle_command(raw: str) -> None:
    try:
        cmd = json.loads(raw)
    except ValueError:
        return
    op = cmd.get("op")

    if op == "set":
        engine.send_message("/ld/set", [str(cmd["path"]), fnum(cmd.get("v"))])
    elif op == "act":
        engine.send_message("/ld/act", [str(cmd.get("a", "")), inum(cmd.get("t"))])
    elif op == "svc":
        what = str(cmd.get("what", ""))
        if isinstance(cmd.get("args"), list):
            args: list[Any] = [what, *cmd["args"]]
        else:
            args = [what]
            for key in ("i", "slot", "seconds", "name"):
                if key in cmd:
                    args.append(cmd[key])
        engine.send_message("/ld/svc", args)
    elif op == "cfg":
        apply_config(str(cmd["path"]), cmd.get("v"))
    elif op == "importList":
        folder = ROOT / "sessions" / "import"
        names = sorted(p.name for p in folder.glob("*.wav")) if folder.is_dir() else []
        push({"t": "imports", "csv": ";".join(names)})
    elif op == "backup":
        try:
            path = session_backup()
            push({"t": "backup", "ok": 1, "path": str(path)})
            log(f"backup -> {path.name} ({path.stat().st_size // 1024} kB)")
        except OSError as exc:
            push({"t": "backup", "ok": 0, "msg": str(exc)})
    elif op == "restore":
        try:
            name = session_restore(str(cmd.get("name", "")))
            push({"t": "restored", "ok": 1, "name": name})
            log(f"restored {name}")
        except (OSError, ValueError) as exc:
            push({"t": "restored", "ok": 0, "msg": str(exc)})
    elif op == "backups":
        push({"t": "backups", "csv": ";".join(b.name for b in session_backups())})
    elif op == "devices":
        asyncio.ensure_future(send_devices())
    elif op == "quit":
        schedule_quit(1.0)
    elif op == "restart":
        engine.send_message("/ld/act", ["reboot", 0])


def session_backups() -> list[pathlib.Path]:
    """Newest first. One zip holds sessions/, session.json and the VST state."""
    folder = ROOT / "sessions" / "backups"
    if not folder.is_dir():
        return []
    return sorted(folder.glob("looper-*.zip"), key=lambda p: p.name, reverse=True)


def session_backup() -> pathlib.Path:
    """Zip sessions/ so one file carries everything to another machine."""
    root = ROOT / "sessions"
    out_dir = root / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"looper-{stamp}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file() and out_dir not in path.parents:
                zf.write(path, path.relative_to(root))
    return out


def session_restore(name: str = "") -> str:
    """Extract the named backup (or the newest) back over sessions/."""
    backups = session_backups()
    if not backups:
        raise OSError("no backups yet")
    chosen = backups[0]
    if name:
        match = [b for b in backups if b.name == name]
        if not match:
            raise OSError(f"no such backup: {name}")
        chosen = match[0]
    root = (ROOT / "sessions").resolve()
    with zipfile.ZipFile(chosen) as zf:
        for member in zf.namelist():
            target = (root / member).resolve()
            # a zip that writes outside sessions/ is not a backup, it is an attack
            if root not in target.parents and target != root:
                raise ValueError(f"refusing {member}: outside sessions/")
        zf.extractall(root)
    return chosen.name


def apply_config(dotted: str, value: Any) -> None:
    """Persist a setting, regenerate the engine config, live-apply if possible."""
    global CFG
    set_path(CFG, dotted, value)
    CFG_JSON_BACKUP = CONFIG_JSON.with_suffix(".json.bak")
    shutil.copyfile(CONFIG_JSON, CFG_JSON_BACKUP)
    CONFIG_JSON.write_text(json.dumps(CFG, indent=2) + "\n", newline="\n")
    write_from(str(CONFIG_JSON), str(CONFIG_SCD))

    if dotted == "audio.quantum":
        apply_quantum(inum(value))

    live = CFG_TO_ENGINE.get(dotted)
    if live is not None:
        engine.send_message("/ld/set", [f"g.{live}", float(value)])
    push(
        {
            "t": "cfg",
            "path": dotted,
            "v": value,
            "needsRestart": dotted in CFG_NEEDS_RESTART,
        }
    )


def leave(code: int = 0) -> None:
    """End the bridge.

    Standalone, that means the process. Inside the desktop app the bridge is a
    thread, and `os._exit` there would take the window down with it — the user
    closes a window, they do not expect the app to vanish mid-session.
    """
    if os.environ.get("LOOPER_OSS_IN_PROCESS") == "1":
        raise SystemExit(code)
    os._exit(code)


def schedule_quit(delay: float = 4.0) -> None:
    """No UI for `delay` seconds -> stop the engine and leave.

    Closing the app window must stop the console too; a reload reconnects well
    inside the grace period and cancels this.

    `LOOPER_OSS_KEEP_ALIVE=1` disables it: a headless run (tests, a plugin host,
    a pedal with its own front end) has no UI client by design and must survive.
    """
    global quit_task
    if os.environ.get("LOOPER_OSS_KEEP_ALIVE") == "1":
        return
    if quit_task is not None and not quit_task.done():
        return

    async def _quit() -> None:
        await asyncio.sleep(delay)
        if clients:
            return
        log(f"no UI for {delay}s — stopping the engine")
        engine.send_message("/ld/act", ["shutdown", 0])
        await asyncio.sleep(0.4)
        log("bridge exiting")
        leave(0)

    quit_task = asyncio.ensure_future(_quit())


async def send_devices() -> None:
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(None, list_audio_devices)
    await broadcast({"t": "devices", **data})


async def ws_handler(ws: Any) -> None:
    clients.add(ws)
    log(f"ui connected ({len(clients)})")
    # a UI that connects late still needs the catalog and the meters
    for what in ("stats", "vstList", "vstCatalog", "midiMap", "midiDevs"):
        engine.send_message("/ld/svc", [what])
    try:
        await ws.send(
            json.dumps(
                {
                    "t": "hello",
                    "tracks": N,
                    "layers": CFG.get("maxLayers", 4),
                    "config": CFG,
                }
            )
        )
        # replay readiness for a UI that connected late (browser windows can
        # take many seconds to appear; the engine usually wins that race)
        if engine_ready is not None:
            await ws.send(json.dumps(engine_ready))
        elif engine_failed is not None:
            await ws.send(json.dumps({"t": "bootfail", "msg": engine_failed}))
        if last_state is not None:
            await ws.send(json.dumps(last_state))
        async for raw in ws:
            handle_command(raw)
    except websockets.ConnectionClosed as exc:
        log(f"ui closed connection ({exc})")
    finally:
        clients.discard(ws)
        log(f"ui disconnected ({len(clients)})")
        if not clients:
            schedule_quit()


async def main() -> None:
    if CFG["audio"].get("quantum"):
        apply_quantum(inum(CFG["audio"]["quantum"]))
    dispatcher = Dispatcher()
    for addr, fn in (
        ("/ldstate", on_state), ("/ldpos", on_pos), ("/ldbeat", on_beat),
        ("/ldwrap", on_wrap), ("/ldmeter", on_meter), ("/ldmaster", on_master),
        ("/ldin", on_input), ("/ldstats", on_stats), ("/ldvst", on_vst),
        ("/ldvstcatalog", on_vst_catalog), ("/ldmidimap", on_midi_map),
        ("/ldmidi", on_midi_event), ("/ldrhythm", on_rhythm), ("/ldvstbanks", on_vst_banks), ("/ldinputs", on_inputs), ("/ldimport", on_import), ("/ldmididevs", on_midi_devices),
        ("/ldauto", on_auto), ("/ldready", on_ready), ("/ldbootfail", on_boot_fail), ("/ldprobe", on_probe), ("/ldtap", on_tap),
        ("/ldsaved", on_saved), ("/ldloaded", on_loaded), ("/ldloaderr", on_loaderr),
        ("/ldcapturing", on_capturing), ("/ldexported", on_exported),
    ):
        dispatcher.map(addr, fn)

    loop = cast("asyncio.BaseEventLoop", asyncio.get_running_loop())
    osc_server = AsyncIOOSCUDPServer(("127.0.0.1", STATE_PORT), dispatcher, loop)
    await osc_server.create_serve_endpoint()

    async with websockets.serve(ws_handler, "127.0.0.1", WS_PORT, max_size=None,
                                process_request=process_request):
        log(
            f"ws://127.0.0.1:{WS_PORT}  osc in {STATE_PORT} -> engine {ENGINE_PORT}  "
            f"({N} tracks)"
        )
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
