# Looper OSS protocol

Three hops. Each one has exactly one contract.

```text
browser ──ws JSON──▶ bridge ──OSC──▶ sclang (engine) ──OSC──▶ scsynth
        ◀──────────         ◀──────                 ◀──────
```

- engine OSC in: **:9000** (bridge → sclang)
- engine state out: **:9001** (sclang → bridge)
- bridge websocket: **:8182**
- static UI: **:8181** (`python -m http.server --directory ui`)

All ports live in `ui/config.json` under `osc` and `ui`.

## The one gotcha that cost a day

SuperCollider's `SendReply` puts a **reply id before the values**:

```text
[cmdName, nodeID, replyID, v1, v2, ...]      # replyID defaults to -1
```

So the first value of `/ldmeter` is `msg[4]`, not `msg[3]`. Messages with no
values (`/ldwrap`) still carry the reply id. Verify any new reply with a
two-line test before wiring it into the UI.

## engine → bridge (OSC, UDP 9001)

| message | values | notes |
| --- | --- | --- |
| `/ldstate` | 23 global + 24 per track, see below | 20 Hz |
| `/ldbeat` | `beat in bar` | on every beat |
| `/ldwrap` | — | loop boundary |

Four replies are **internal to sclang** and deliberately *not* forwarded to the
bridge: `/ldmeter` (per-track meters), `/ldin` (hardware input), `/ldmaster`
(master out) and the transport phase. They update engine state, and the values
travel inside `/ldstate`. That removes a second, fragile forwarding path — and it
means the UI has exactly one place to read meters and the playhead from.

Meters themselves do not use `SendReply` either (it proved unreliable from the big
SynthDefs): `\ld_meter` writes `BufWr.kr` into a 64-frame buffer, sclang sends
`/b_getn` at 10 Hz, and one persistent `/b_setn` responder stores the values that
`/ldstate` then carries. Slots 0–59 are per track (4 each: L, R, sum); slots
60–63 are master L/R and hardware input L/R.
| `/ldstats` | `cpu, sr, blockSize, hwBuffer, latency, running, vst, vstCount` | on request |
| `/ldvst` | `csv` | `slot\|name\|bypass\|window` per slot, tracks split by `;` |
| `/ldvstcatalog` | `csv` | plugin keys separated by `;` |
| `/ldmidimap` | `csv` | `type\|num\|target` entries split by `;` |
| `/ldmidi` | `what, num, target` | `learned` |
| `/ldauto` | `track` | threshold recorder fired |
| `/ldtap` | `bpm, taps` | result of a tap-tempo measurement |
| `/ldsaved` | `tag, layers, dir` | tag < 0 is the autosave |
| `/ldloaded` | `tag, layers, dir` | |
| `/ldloaderr` | `tag, message` | |
| `/ldprobe` | `track, layers, contentLen, recOn, playing, meterL, meterR, g0..g3, peak0..peak3` | diagnostic: what is actually in the layer buffers |
| `/ldcapturing` | `seconds` | export started |
| `/ldexported` | `path` | export finished |
| `/ldready` | `tracks` | engine finished booting |

### `/ldstate` field order

Global (26):

```text
bpm bars beatsPerBar metro countIn masterVol clickVol sel cpu
revMix room xfadeMs recordOffset dubDecay autoClose masterHp masterLp version
inL inR masterL masterR phase
```

`inL/inR` are the hardware input peaks, `masterL/masterR` the master output,
`phase` the loop position 0..1. `phase` is counted from the beat replies and
reset on `/ldwrap`, so it covers the whole loop; the UI dead-reckons between
20 Hz frames for a smooth playhead.

Per track (32), repeated for every track:

```text
state vol pan muted meter input layers
gateOn gateThr hp lp
delayOn delayBeats delayDecay delayMix
revSend gain recGain reverse fadeIn fadeOut loopRatio speed layerPeak undoDepth
```

`state`: `0` empty · `1` recording · `2` playing · `3` stopped.
`input`: `0` stereo sum · `1..n` one channel each · `n+1` the internal test bus.
`loopRatio`: `1` full loop · `2` half · `4` quarter — a grid-locked subdivision.
`speed`: `0.25..4`, default `1`. Varispeed: at `1` the read position comes from
the master phase (grid-locked, tracks cannot drift); anything else free-runs the
track's own phasor and re-pitches, like the hardware's SPEED knob. It is **not**
time-stretch — pitch moves with speed.

The bridge converts this to JSON with the same names; `ui/app.js` reads those.

## bridge → engine (OSC, UDP 9000)

Generic setters, so a new knob needs no new OSC path:

| message | values | example |
| --- | --- | --- |
| `/ld/set` | `path, value` | `/ld/set "t2.hp" 300` → `synth.set(\hp, 300)` |
| `/ld/set` | `path, value` | `/ld/set "g.bpm" 128` → global (see below) |
| `/ld/act` | `action, track` | `press stop clear clearAll undo redo mark markBack bounce stopAll save load export reboot` |
| `/ld/svc` | `what, ...args` | service calls, see below |

Global paths for `/ld/set`: `g.bpm g.bars g.beatsPerBar g.metro g.countIn
g.masterVol g.clickVol g.revMix g.room g.masterHp g.masterLp g.xfadeMs
g.recordOffset g.dubDecay g.autoClose g.quantizeStart g.overdubMode
g.rhythmOn g.rhythmKit g.rhythmPattern g.sel`.

Per track paths: `t<N>.<field>` for every per-track state field above.

`/ld/svc` actions, with their arguments:

| action | arguments |
| --- | --- |
| `stats` | — |
| `vstList` / `vstCatalog` | — |
| `vstOpen` | track, slot, plugin key |
| `vstClose` | track, slot |
| `vstBypass` | track, slot, 0 or 1 |
| `vstBankSave` / `vstBankLoad` | track, bank 0–3 |
| `vstMode` | track, slot, 0 = toggle / 1 = momentary |
| `vstEditor` | track, slot |
| `midiLearn` | target, e.g. `t0.press` |
| `midiMap` / `midiClear` | — |
| `save` / `load` | slot |
| `export` | seconds |
| `tap` | — |
| `probe` | track (diagnostic) |
| `rhythm` | — ; replies `/ldrhythm on, kit, pattern` |
| `import` | track, file name in `sessions/import/` ; replies `/ldimport track, name, frames` |
| `midiAdd` | type, num, target, gesture (test hook) |
| `midiSim` | num, gesture (test hook) |

Browser-only ops (the bridge does the work, no engine traffic):

| op | effect |
| --- | --- |
| `backup` | zips `sessions/` → `sessions/backups/looper-<stamp>.zip`, replies `{"t":"backup","ok":1,"path":…}` |
| `restore` | extracts the newest backup (or `name`) over `sessions/`, refusing members outside it; replies `{"t":"restored","ok":1,"name":…}` |
| `/ldvstbanks` (engine → bridge) | `track:bank:key>key>key>key;…` — the bridge persists it as `vst.banks` |
| `backups` | replies `{"t":"backups","csv":"a.zip;b.zip"}` |
| `importList` | replies `{"t":"imports","csv":"a.wav;b.wav"}` from `sessions/import/` |

## browser ↔ bridge (WebSocket JSON)

Client → bridge:

```json
{"op":"set","path":"t0.vol","v":0.7}
{"op":"act","a":"press","t":0}
{"op":"svc","what":"save","args":[1]}
{"op":"cfg","path":"loop.crossfadeMs","v":2.5}
{"op":"devices"}
{"op":"restart"}
```

Bridge → client: the state message (`t:"s"`), plus `t` = `pos beat wrap m in
master stats devices vst vstcatalog midimap midi auto saved loaded capturing
exported cfg error ready hello`.

`{"op":"cfg"}` writes the value into `ui/config.json`, regenerates
`engine/_config.scd`, and live-applies it when the engine can take it without a
restart:

```text
bpm bars beatsPerBar
loop.crossfadeMs → g.xfadeMs        loop.recordOffset → g.recordOffset
loop.dubDecay → g.dubDecay          loop.autoClose → g.autoClose
loop.quantizeStart → g.quantizeStart loop.revMix → g.revMix
loop.room → g.room                  clock.clickVol → g.clickVol
clock.countInBars → g.countIn
```

Everything else (audio device, block size, `tracks`, `maxLayers`,
`loop.maxSeconds`) needs `{"op":"restart"}`, and the UI is told so via
`{"t":"cfg","needsRestart":true}`.

## config.json

Single source of truth. `bridge/write_config.py` renders it into a
SuperCollider literal (`engine/_config.scd`) for sclang; the bridge does the same
whenever the UI saves a setting, and the app launcher does it at startup.

```jsonc
{
  "tracks": 5, "maxLayers": 4, "bpm": 120, "bars": 4, "beatsPerBar": 4,
  "audio": { "blockSize": 64, "hardwareBufferSize": 256, "in": 2, "out": 2,
             "device": null, "driver": "auto", "sampleRate": null },
  "loop":  { "maxSeconds": 16, "crossfadeMs": 1.3, "recordOffset": 0,
             "autoClose": 1, "dubDecay": 0.0, "revMix": 0.6, "room": 0.8,
             "quantizeStart": 0 },
  "clock": { "clickVol": 0.3, "countInBars": 1, "accentHz": 1580, "clickHz": 1050 },
  "midi":  { "map": [] },
  "osc":   { "enginePort": 9000, "statePort": 9001 },
  "ui":    { "port": 8181, "wsPort": 8182, "scale": 1.0, "holdMs": 800,
             "fullscreen": false, "showMaster": true }
}
```

A malformed config is survivable: the bridge refuses to start with a clear
message, and the engine falls back to built-in defaults for any missing key.
