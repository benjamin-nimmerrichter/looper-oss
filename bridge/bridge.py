#!/usr/bin/env python3
"""Looper OSS bridge: engine (OSC) <-> browser (WebSocket JSON).

The engine sends flat positional OSC; this file is the only place that knows the
field order, and the only place that speaks OSC. Above it is plain JSON.

Also owns ui/config.json: settings changed in the UI are written back here and
the generated engine config is refreshed, so a restart picks them up.
"""
import asyncio
import json
import os
import pathlib
import shutil
import subprocess
import sys
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
]
TRACK_FIELDS = [
    "state", "vol", "pan", "muted", "meter", "input", "layers",
    "gateOn", "gateThr", "hp", "lp",
    "delayOn", "delayBeats", "delayDecay", "delayMix",
    "revSend", "gain", "recGain", "reverse", "fadeIn", "fadeOut",
    "loopRatio", "layerPeak", "undoDepth",
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
    if frames <= 0:
        return
    try:
        subprocess.run(["pw-metadata", "-n", "settings", "0",
                        "clock.force-quantum", str(frames)],
                       capture_output=True, timeout=4, check=False)
        log(f"pipewire quantum forced to {frames} frames")
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
    out.update(read_clock())
    return out


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
            CONFIG_JSON.write_text(json.dumps(CFG, indent=2) + "\n")
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
    push({"t": "bootfail", "msg": why})


def on_ready(_addr: str, *args: Any) -> None:
    tracks = inum(args[0]) if args else N
    log(f"engine ready ({tracks} tracks)")
    push({"t": "ready", "tracks": tracks})
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
            out.write_text(json.dumps({"state": last_state, "slot": slot}, indent=2))
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
    elif op == "devices":
        asyncio.ensure_future(send_devices())
    elif op == "quit":
        schedule_quit(1.0)
    elif op == "restart":
        engine.send_message("/ld/act", ["reboot", 0])


def apply_config(dotted: str, value: Any) -> None:
    """Persist a setting, regenerate the engine config, live-apply if possible."""
    global CFG
    set_path(CFG, dotted, value)
    CFG_JSON_BACKUP = CONFIG_JSON.with_suffix(".json.bak")
    shutil.copyfile(CONFIG_JSON, CFG_JSON_BACKUP)
    CONFIG_JSON.write_text(json.dumps(CFG, indent=2) + "\n")
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
        ("/ldmidi", on_midi_event), ("/ldmididevs", on_midi_devices),
        ("/ldauto", on_auto), ("/ldready", on_ready), ("/ldbootfail", on_boot_fail), ("/ldprobe", on_probe), ("/ldtap", on_tap),
        ("/ldsaved", on_saved), ("/ldloaded", on_loaded), ("/ldloaderr", on_loaderr),
        ("/ldcapturing", on_capturing), ("/ldexported", on_exported),
    ):
        dispatcher.map(addr, fn)

    loop = cast("asyncio.BaseEventLoop", asyncio.get_running_loop())
    osc_server = AsyncIOOSCUDPServer(("127.0.0.1", STATE_PORT), dispatcher, loop)
    await osc_server.create_serve_endpoint()

    async with websockets.serve(ws_handler, "127.0.0.1", WS_PORT, max_size=None):
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
