import {
  Button, Fader, Knob, Meter, Pad, Segmented, Select, Stepper, auditTapTargets,
} from "./controls.js";

/* ------------------------------------------------------------------ state -- */
const STATE_TEXT = {
  0: ["EMPTY", "tap to record"],
  1: ["RECORDING", "tap to close"],
  2: ["PLAYING", "tap to stop"],
  3: ["STOPPED", "tap to play"],
};
const STATE_CLASS = { 0: "", 1: "rec", 2: "play", 3: "stopped" };
/**
 * The record sources a track can pick from: the stereo sum, then one mono entry
 * per input channel. On a two-input interface that is L and R -- a mic on one,
 * a guitar on the other, each on its own track, from the same box. Wider
 * interfaces simply list more channels; nothing in the engine is fixed to two.
 */
function channelLabel(ch, n) {
  if (n !== 2) return `ch ${ch}`;
  return ch === 1 ? "L" : "R";
}

function inputOptions() {
  const n = Math.max(1, Math.min(16, Number(CFG.audio?.in ?? 2)));
  const opts = [{ label: n > 1 ? "stereo" : "mono", value: 0 }];
  for (let ch = 1; ch <= n; ch++) opts.push({ label: channelLabel(ch, n), value: ch });
  return opts;
}

/** Compact badge for the track header: which source this track records. */
function inputTag(v) {
  const n = Math.max(1, Math.min(16, Number(CFG.audio?.in ?? 2)));
  if (!Number(v)) return n > 1 ? "IN ST" : "IN 1";
  if (n !== 2) return `IN ${v}`;
  return `IN ${Number(v) === 1 ? "L" : "R"}`;
}
const BARS = [1, 2, 4, 8];
/** Per-track playback speed presets; the knob below does everything between. */
const SPEEDS = [
  { label: "½×", value: 0.5 },
  { label: "1×", value: 1 },
  { label: "2×", value: 2 },
  { label: "4×", value: 4 },
];
const RATIOS = [
  { label: "1", value: 1 },
  { label: "1/2", value: 2 },
  { label: "1/4", value: 4 },
];

let CFG = { tracks: 5, colours: ["#4a9de0"], ui: {} };
let S = null;
let STATS = { cpu: 0, sr: 0, blockSize: 0, hwBuffer: 0, latency: 0, vst: 0, vstCount: 0 };
let VST = [];
let VSTCATALOG = [];
let MIDIMAP = [];
let MIDIDEVS = [];
let selected = 0;
// the track you are merging FROM: tap its merge, then the one it goes into
let mergeFrom = null;
const mergeBtns = [];
let IMPORTS = [];
let SEQ = [];       // per-track step-sequence patterns (eight values 0..1)   // *.wav sitting in sessions/import/
// The bar eases toward the engine's loop points and stops when they stop moving.
// (It used to add elapsed time itself, so it kept moving even with the engine off.)
let playPos = 0;
let settingsTab = "audio";
let fxTab = "input";
let midiLearnScope = "t0";
let importTrack = 0;

const ui = { transport: {}, tracks: [], master: {} };

/* ------------------------------------------------------------------- wire -- */
let ws = null;
// ws:// not wss://: this socket only ever goes to the bridge on loopback
// (127.0.0.1), there is no certificate to trust and the app is offline by design.
let wsUrl = `ws://${location.hostname}:8182`;
let reconnectTimer = null;

const send = (o) => ws && ws.readyState === 1 && ws.send(JSON.stringify(o));
const set = (path, v) => send({ op: "set", path, v });
const act = (a, t = 0) => send({ op: "act", a, t });
const svc = (what, args = []) => send({ op: "svc", what, args });
const cfgSet = (path, v) => send({ op: "cfg", path, v });

// Master FX slots (#7): the effect types the engine can build, and what each
// one's two knobs mean -- a label that names the parameter is worth more than a
// dead pair of knobs that says "A" and "B" for every type.
const FX_TYPES = ["slot 1", "slot 2"];
const FX_KNOBS = {
  0: ["a", "b"],
  1: ["cutoff", "resonance"],
  2: ["time", "feedback"],
  3: ["drive", "tone"],
  4: ["rate", "depth"],
};

function connect() {
  ws = new WebSocket(wsUrl);
  ws.onopen = () => {
    setBoot("engine starting…");
    toast("connected");
    svc("stats");
  };
  ws.onerror = () => {
    // Say it on the boot screen: a blocked localhost socket is invisible
    // otherwise, and the user sees only "waiting for the engine" for ever.
    setBoot(`cannot reach the engine at ${wsUrl} — is localhost blocked?`, false, true);
  };
  ws.onclose = () => {
    setBoot("waiting for the engine…", false, true);
    toast("engine offline — retrying");
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 1200);
  };
  ws.onmessage = (e) => {
    let m;
    try {
      m = JSON.parse(e.data);
    } catch {
      return;
    }
    handle(m);
  };
}

// "starting engine" screen: hidden once the engine reports ready
function setBoot(text, done = false, failed = false) {
  const el0 = document.getElementById("boot");
  if (!el0) return;
  if (done) {
    el0.classList.add("done");
    setTimeout(() => { el0.style.display = "none"; }, 260);
    return;
  }
  el0.style.display = "grid";
  el0.classList.remove("done");
  el0.classList.toggle("failed", failed);
  const msg = el0.querySelector(".boot-msg");
  if (msg) msg.textContent = text;
}

function handle(m) {
  switch (m.t) {
    case "hello":
      // which platform we are on decides the driver list and how a device is
      // named: jack/alsa on Linux, WASAPI/ASIO/MME on Windows
      ui.platform = m.platform || "unix";
      if (Array.isArray(m.drivers) && m.drivers.length) ui.drivers = m.drivers;
      if (m.config) CFG = m.config;
      applyUiConfig();
      buildTracks(m.tracks);
      break;
    case "s":
      onState(m);
      break;
    // meters and the playhead arrive inside the state frame (t === "s")
    case "stats":
      STATS = m;
      renderStats();
      break;
    case "devices":
      renderDevices(m);
      break;
    case "vst":
      VST = m.csv;
      if (document.getElementById("fx").classList.contains("open")) openFx(selected);
      break;
    case "vstcatalog":
      VSTCATALOG = m.csv ? m.csv.split(";").filter(Boolean) : [];
      if (document.getElementById("fx").classList.contains("open")) openFx(selected);
      break;
    case "mididevs":
      MIDIDEVS = (m.csv || "").split(";").filter(Boolean).map((e) => {
        const [i, name] = e.split("|");
        return { idx: Number(i), name };
      });
      if (settingsTab === "midi") renderSettings();
      break;
    case "midimap":
      MIDIMAP = m.csv ? m.csv.split(";").filter(Boolean) : [];
      if (settingsTab === "midi") renderSettings();
      break;
    case "midi":
      toast(`learned ${m.what} ${m.num} → ${m.target}`);
      break;
    case "auto":
      toast(`track ${m.i + 1}: threshold reached`);
      break;
    case "silent":
      // a take recorded nothing: it was dropped instead of muting the track
      toast(`track ${m.track + 1}: nothing recorded, take dropped`);
      break;
    case "merged":
      // 1 = done, 0 = nothing to merge, 2 = no free layer slot on the destination
      if (m.ok === 1) toast(`track ${m.track + 1}: merged`);
      else if (m.ok === 2) toast(`track ${m.track + 1}: no free layer — flatten one of them first`);
      else toast(`track ${m.track + 1}: nothing to merge`);
      break;
    case "saved":
      // slot < 0 is the autosave: silent
      if (m.slot >= 0) toast(`saved slot ${m.slot} (${m.layers} layers)`);
      break;
    case "loaded":
      toast(`loaded slot ${m.slot} (${m.layers} layers)`);
      break;
    case "capturing":
      toast(`exporting ${m.seconds}s of the master…`);
      break;
    case "exported":
      toast(`exported ${m.path.split("/").pop()}`);
      break;
    case "imports": {
      // renderSettings() asks for the list again, so only re-render when it
      // actually changed -- otherwise the drawer rebuilds forever
      const next = (m.csv || "").split(";").filter(Boolean);
      if (next.join("|") !== IMPORTS.join("|")) {
        IMPORTS = next;
        if (settingsTab === "session") renderSettings();
      }
      break;
    }
    case "slots": {
      const list = document.getElementById("slotlist");
      if (list) {
        list.replaceChildren();
        const names = (m.csv || "").split(";").filter(Boolean);
        if (!names.length) list.append(el("span", "dim", "none yet"));
        names.forEach((name) => {
          const n = Number(name.replace("slot", ""));
          const b = Button({ label: String(n), cls: "tiny" });
          b.addEventListener("pointerup", () => svc("load", [n]));
          list.append(b);
        });
      }
      break;
    }
    case "imported":
      toast(m.frames > 0 ? `imported ${m.name} (${m.frames} frames)`
        : `import failed: ${m.name}`);
      break;
    case "backup":
      toast(m.ok ? `backup → ${String(m.path).split("/").pop()}`
        : `backup failed: ${m.msg}`);
      break;
    case "restored":
      toast(m.ok ? `restored ${m.name}` : `restore failed: ${m.msg}`);
      break;
    case "error":
      toast(m.msg);
      break;
    case "cfg":
      if (m.needsRestart) toast("restart the engine to apply");
      break;
    case "ready":
      setBoot("", true);
      toast("engine ready");
      break;
    case "tap":
      toast(`tap → ${Math.round(m.bpm)} bpm (${m.taps} taps)`);
      break;
    case "probe":
      toast(`t${m.track + 1}: ${m.layers}L · gains ${m.gains.join(",")} · peaks ${m.peaks.map((x) => x.toFixed(3)).join(",")}`);
      console.log("probe", m);
      break;
    case "bootfail":
      setBoot(`engine failed to start: ${m.msg}`, false, true);
      console.error("engine boot failed:", m.msg);
      break;
    case "link": {
      const el2 = document.getElementById("linkstatus");
      if (el2) {
        el2.textContent = !m.on ? "off"
          : m.peers > 0 ? `${m.peers} peer${m.peers === 1 ? "" : "s"} · ${Math.round(m.bpm)} bpm`
          : "on, no peer";
      }
      break;
    }
    case "audio":
      // the engine opened a different device than the one asked for, or fell
      // back to the default: the meters can move while nothing is heard
      toast(`audio: ${m.warn}`);
      console.error("audio:", m.warn);
      break;
    default:
      break;
  }
}

/* ------------------------------------------------------------------ toast -- */
let toastTimer = null;
function toast(msg) {
  const t = document.getElementById("toast");
  if (!t) return;
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 1800);
}

/* ------------------------------------------------------------------ utils -- */
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function getPath(obj, dotted) {
  return dotted.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function applyUiConfig() {
  const scale = Number(CFG.ui?.scale ?? 1);
  document.documentElement.style.setProperty("--scale", String(scale));
  document.body.classList.toggle("no-master", CFG.ui?.showMaster === false);
}

/* ----------------------------------------------------------------- keypad -- */
// Touch-friendly number entry: tap the BPM value instead of dragging a fader.
function openKeypad({ title, value, min, max, hint, onOk }) {
  const root = document.getElementById("keypad");
  const valEl = root.querySelector(".keypad-value");
  root.querySelector(".keypad-title").textContent = title;
  root.querySelector(".keypad-hint").textContent = hint || `${min}–${max}`;
  const grid = root.querySelector(".keypad-grid");
  grid.replaceChildren();
  let typed = "";
  const show = () => { valEl.textContent = typed === "" ? String(value) : typed; };
  const apply = () => {
    const v = typed === "" ? value : Number(typed);
    if (Number.isFinite(v)) onOk(Math.min(max, Math.max(min, v)));
    root.classList.remove("open");
  };
  ["1", "2", "3", "4", "5", "6", "7", "8", "9", "C", "0", "OK"].forEach((k) => {
    if (k === "C") {
      const b = Button({ label: "clear", cls: "tiny" });
      b.addEventListener("pointerup", () => { typed = ""; show(); });
      grid.append(b);
    } else if (k === "OK") {
      const b = Button({ label: "set", cls: "ok" });
      b.addEventListener("pointerup", apply);
      grid.append(b);
    } else {
      const b = Button({ label: k });
      b.addEventListener("pointerup", () => {
        if (typed.length < 4) { typed += k; show(); }
      });
      grid.append(b);
    }
  });
  show();
  root.classList.add("open");
}

/* -------------------------------------------------------------- transport -- */
/**
 * Two taps: arm, then confirm within 2.5 s.
 *
 * A press-and-hold is not an option here. On a touchscreen (Windows and Linux)
 * the OS claims the hold and turns it into a right-click / context menu, so the
 * hold never completes -- the same reason the per-track clear is two taps.
 */
/**
 * Two-tap track merge, the way a hardware looper does it: what you touch FIRST
 * is the track that goes away, what you touch SECOND is where it lands.
 *
 *   1. tap merge on track 2  -> 2 lights up ("from"), every other track's merge
 *                                lights as a target ("into")
 *   2. tap merge on track 1  -> 2 is merged into 1
 *
 * Tapping the source again cancels, so a mis-tap costs one tap, not a merge.
 */
function tapMerge(i) {
  if (mergeFrom === null) {
    mergeFrom = i;
    paintMerge();
    toast(`track ${i + 1}: now tap the track it should go into`);
    return;
  }
  if (mergeFrom === i) {
    mergeFrom = null;
    paintMerge();
    return;
  }
  const src = mergeFrom;
  mergeFrom = null;
  paintMerge();
  svc("mergeTracks", [i, i, src]);
  // the merge is a layer move, not a sum: nothing is re-rendered, so it is
  // instant and lossless -- but the destination now holds both as layers, and
  // "flatten" in its fx drawer turns them into one loop
  toast(`track ${src + 1} → track ${i + 1} (as layers; flatten to join)`);
}

function paintMerge() {
  mergeBtns.forEach((b, n) => {
    if (!b) return;
    b.classList.toggle("on", mergeFrom === n);
    b.classList.toggle("target", mergeFrom !== null && mergeFrom !== n);
    b.textContent = mergeFrom === n ? "from" : mergeFrom !== null ? "into" : "merge";
  });
}

function confirmButton(label, onConfirm, cls = "") {
  const b = Button({ label, cls });
  let armedAt = 0;
  const disarm = () => {
    armedAt = 0;
    b.classList.remove("on");
    b.textContent = label;
  };
  b.addEventListener("pointerup", () => {
    const now = performance.now();
    if (armedAt && now - armedAt < 2500) {
      disarm();
      onConfirm();
    } else {
      armedAt = now;
      b.classList.add("on");
      b.textContent = "sure?";
      setTimeout(() => {
        if (armedAt && performance.now() - armedAt >= 2400) disarm();
      }, 2600);
    }
  });
  return b;
}

function buildTransport() {
  const root = document.getElementById("transport");
  root.replaceChildren();

  const g0 = el("div", "group");
  g0.append(el("div", "caption", "looper-oss"));
  const stopAll = Button({ label: "stop all", cls: "danger" });
  stopAll.addEventListener("pointerup", () => act("stopAll", 0));
  const clearAll = confirmButton("clear all", () => {
    act("clearAll", 0);
    toast("all tracks cleared");
  }, "danger");
  const looperBtns = el("div", "row2");
  looperBtns.append(stopAll, clearAll);
  const settings = Button({ label: "setup" });
  settings.addEventListener("pointerup", () => openSettings());
  g0.append(looperBtns, settings);

  const g1 = el("div", "group grow");
  g1.append(el("div", "caption", "tempo"));
  const bpmVal = el("div", "bigvalue", "120");
  bpmVal.style.cursor = "pointer";
  bpmVal.title = "tap to type a tempo";
  bpmVal.addEventListener("pointerup", () => {
    openKeypad({
      title: "tempo",
      value: Math.round(S ? S.bpm : 120),
      min: 40,
      max: 240,
      hint: "40–240 bpm · or use TAP",
      onOk: (v) => { set("g.bpm", v); cfgSet("bpm", v); },
    });
  });
  const bpmRow = el("div");
  bpmRow.style.display = "flex";
  bpmRow.style.gap = "0.4rem";
  const bpmFader = Fader({
    min: 40, max: 240, value: 120, format: () => "",   // the number above is it
    onInput: (v) => set("g.bpm", Math.round(v)),
  });
  bpmFader.style.flex = "1";
  const tap = Button({ label: "tap", cls: "tiny" });
  tap.addEventListener("pointerup", () => svc("tap"));
  bpmRow.append(bpmFader, tap);
  // Where you are in the loop, in the units you count in: the engine already
  // ships `phase` plus bars/beatsPerBar, so this is arithmetic, not protocol.
  // It sits with the tempo because both are about the grid, and one group fewer
  // leaves more width for everything else.
  const posVal = el("div", "posval", "1·1");
  // under the fader, not beside the number: the number is the tempo, the readout
  // is where you are in the loop
  posVal.classList.add("tempoPos");   // bottom-right, opposite the bpm number
  g1.append(bpmVal, bpmRow, posVal);

  const g2 = el("div", "group");
  g2.append(el("div", "caption", "bars / loop"));
  const bars = Segmented({
    options: BARS.map((b) => ({ label: `${b}`, value: b })),
    value: 4,
    onPick: (v) => set("g.bars", v),
  });
  g2.append(bars);


  const g3 = el("div", "group");
  g3.append(el("div", "caption", "clock"));
  // These send the inverse of what the ENGINE has, not of the button's class:
  // a state frame lands between pointerdown and pointerup and used to reset the
  // class, so the toggle always sent 0.
  const metro = Button({ label: "metronome" });
  const countIn = Button({ label: "count-in" });
  metro.addEventListener("pointerup", () => set("g.metro", S && S.metro ? 0 : 1));
  countIn.addEventListener("pointerup", () => set("g.countIn", S && S.countIn > 0 ? 0 : 1));
  g3.append(metro, countIn);

  // The performance group: the DJ filter, and nothing else. `centre` centres the
  // knob in the group instead of letting it sit wherever the caption starts.
  const g4 = el("div", "group grow centre");
  g4.append(el("div", "caption", "performance"));
  // One bipolar knob: centre is a hard bypass, left sweeps a low pass down,
  // right a high pass up, and a double tap puts it back to centre.
  const dj = Knob({
    min: -1, max: 1, value: 0, center: 0, label: "dj filter",
    format: (v) => {
      if (Math.abs(v) < 0.01) return "off";
      if (v < 0) return `LP ${Math.round(20 * 20000 ** (1 + v))} Hz`;
      return `HP ${Math.round(20 * 1000 ** v)} Hz`;
    },
    onInput: (v) => set("g.dj", v),
  });
  g4.append(dj);

  // Rhythm engine: 16 synthesised kits, 200 patterns. Driven through the config
  // so it survives a restart, like every other setting.
  const gr = el("div", "group grow");
  gr.append(el("div", "caption", "rhythm"));
  const rOn = Button({ label: "drums", toggle: true, cls: "tiny" });
  if (Number(CFG.rhythm?.on)) rOn.classList.add("on");
  rOn.addEventListener("pointerup", () => {
    cfgSet("rhythm.on", rOn.classList.contains("on") ? 1 : 0);
  });
  const rKit = Stepper({
    min: 1, max: 16, step: 1, value: Number(CFG.rhythm?.kit ?? 0) + 1,
    format: (v) => `kit ${v}`,
    onInput: (v) => cfgSet("rhythm.kit", v - 1),
  });
  const rPat = Stepper({
    min: 1, max: 200, step: 1, value: Number(CFG.rhythm?.pattern ?? 0) + 1,
    format: (v) => `pat ${v}`,
    onInput: (v) => cfgSet("rhythm.pattern", v - 1),
  });
  gr.append(rOn, rKit, rPat);

  root.append(g0, g1, g2, g3, gr, g4);
  ui.transport = { bpmVal, bpmFader, bars, metro, countIn, posVal, dj };
}

/* ----------------------------------------------------------------- tracks -- */
function buildTracks(n) {
  const root = document.getElementById("tracks");
  root.replaceChildren();

  const playhead = el("div", "playhead");
  // bar and beat marks along the line, so it reads as a ruler and not only as a
  // dot travelling across the screen
  const playheadTicks = el("div", "ticks");
  const playheadFill = el("i");
  playhead.append(playheadTicks, playheadFill);
  root.append(playhead);

  ui.tracks = [];
  for (let i = 0; i < n; i++) {
    const col = el("div", "track");
    col.style.setProperty("--c", (CFG.colours || ["#4a9de0"])[i % (CFG.colours || [1]).length]);

    const name = el("div", "tname");
    name.append(el("b", null, `${i + 1}`));
    const layers = el("span", "layers", "0 layers");
    // which input this track records, visible without opening anything
    const insrc = el("span", "insrc", inputTag(0));
    name.append(layers, insrc);
    // REPLACE mode silences everything below every new take, so it must be
    // visible on the track itself, not only in a settings drawer.
    const replace = el("span", "replace", "REPLACE");
    replace.title = "overdub mode: a new take mutes the layers below it";
    name.append(replace);
    ui.replaceBadges = ui.replaceBadges || [];
    ui.replaceBadges[i] = replace;

    const pad = Pad({
      label: "EMPTY",
      sub: "tap to record",
      cls: "main",
      onTap: () => {
        selected = i;
        act("press", i);
      },
    });

    const rowA = el("div", "row2");
    const stop = Button({ label: "stop" });
    stop.addEventListener("pointerup", () => act("stop", i));
    const dub = Button({ label: "dub" });
    dub.addEventListener("pointerup", () => act("dub", i));
    const fx = Button({ label: "fx" });
    fx.addEventListener("pointerup", () => openFx(i));
    rowA.append(stop, dub, fx);

    const vol = Fader({
      min: 0, max: 1.4, value: 0.8, format: (v) => `${Math.round(v * 100)}`,
      onInput: (v) => set(`t${i}.vol`, v),
    });

    const rowB = el("div", "row2");
    const undo = Button({ label: "undo", cls: "tiny" });
    undo.addEventListener("pointerup", () => act("undo", i));
    const redo = Button({ label: "redo", cls: "tiny" });
    redo.addEventListener("pointerup", () => act("redo", i));
    // MERGE: tap it on the track that should RECEIVE, then on the track that
    // should be joined into it -- the first one you touched is the destination.
    // Tapping the armed track again cancels. Flattening a track's own layers
    // lives in the fx drawer, next to the layer mutes.
    const merge = Button({ label: "merge", cls: "tiny" });
    mergeBtns[i] = merge;
    merge.addEventListener("pointerup", () => tapMerge(i));
    const clear = confirmButton("clear", () => {
      act("clear", i);
      toast(`track ${i + 1} cleared`);
    }, "tiny danger");
    rowB.append(undo, redo, merge, clear);

    const meter = Meter();
    col.append(name, pad, rowA, vol, rowB, meter);
    root.append(col);
    ui.tracks.push({ col, pad, vol, meter, layers, insrc, clear, stateKey: -1 });
  }
  ui.playheadFill = playheadFill;
  ui.playheadTicks = playheadTicks;
}

/* ----------------------------------------------------------------- master -- */
function buildMaster() {
  const root = document.getElementById("master");
  root.replaceChildren();

  const inWrap = el("div", "inmeters");
  inWrap.append(el("div", "caption", "input"));
  const inL = Meter();
  const inR = Meter();
  inWrap.append(inL, inR);

  const vol = Fader({
    min: 0, max: 1.2, value: 0.8, format: (v) => `master ${Math.round(v * 100)}`,
    onInput: (v) => set("g.masterVol", v),
  });
  vol.classList.add("masterfader");

  const out = Meter();
  out.style.width = "6rem";

  const session = Button({ label: "save", cls: "tiny" });
  session.addEventListener("pointerup", () => svc("save", [1]));
  const exportBtn = Button({ label: "export", cls: "tiny" });
  exportBtn.addEventListener("pointerup", () => svc("export", [30]));

  // output mixer: an independent level per stereo bus
  const busRow = el("div");
  busRow.style.display = "flex";
  busRow.style.gap = "0.4rem";
  const busKnobs = [0, 1, 2].map((idx) => {
    const name = "ABC"[idx];
    const k = Knob({
      label: `bus ${name}`, min: 0, max: 1.2, value: 1,
      format: (v) => `${Math.round(v * 100)}`,
      onInput: (v) => set(`g.bus${name}`, v),
    });
    busRow.append(k);
    return k;
  });

  root.append(inWrap, vol, out, busRow, session, exportBtn);
  ui.buses = busKnobs;
  ui.master = { inL, inR, out, vol };
}

/* -------------------------------------------------------------- fx drawer -- */
const FX_TABS = [
  { id: "input", label: "input & out" },
  { id: "loop", label: "loop & layers" },
  { id: "fx", label: "filter & fx" },
  { id: "plugins", label: "plugins" },
];

function openFx(i) {
  selected = i;
  const drawer = document.getElementById("fx");
  const colours = CFG.colours || ["#4a9de0"];
  const trackColor = colours[i % colours.length];
  drawer.style.setProperty("--c", trackColor);
  drawer.querySelector("h2").textContent = `track ${i + 1}`;

  // Track switcher pills in header so you can move between tracks without closing
  const pills = drawer.querySelector(".track-pills");
  if (pills) {
    pills.replaceChildren();
    const nTracks = S ? S.tracks.length : (CFG.tracks || 5);
    for (let k = 0; k < nTracks; k++) {
      const p = el("button", `track-pill${k === i ? " on" : ""}`, `T${k + 1}`);
      p.type = "button";
      p.style.setProperty("--tc", colours[k % colours.length]);
      p.addEventListener("pointerup", () => openFx(k));
      pills.append(p);
    }
  }

  // Signal-flow tabs: Input & Out | Loop & Layers | Filter & FX | Plugins
  const tabs = drawer.querySelector(".tabs");
  if (tabs) {
    tabs.replaceChildren();
    FX_TABS.forEach((tab) => {
      const b = Button({ label: tab.label, cls: `tiny${tab.id === fxTab ? " on" : ""}` });
      b.dataset.tab = tab.id;
      b.addEventListener("pointerup", () => {
        fxTab = tab.id;
        openFx(i);
      });
      tabs.append(b);
    });
  }

  const body = drawer.querySelector(".body");
  body.replaceChildren();
  body.classList.remove("single");
  const t = S ? S.tracks[i] : null;

  const makeCard = (parent, heading) => {
    const c = el("div", "fxcard");
    const h = el("div", "head");
    h.append(el("div", "caption", heading));
    const acts = el("div", "head-acts");
    h.append(acts);
    const inner = el("div", "card-body");
    c.append(h, inner);
    parent.append(c);
    return [inner, acts];
  };
  const knobs = (parent) => {
    const k = el("div", "knobs");
    parent.append(k);
    return k;
  };
  const toggle = (head, on, label, path, onOffLabels = false) => {
    const b = Button({ label: onOffLabels ? (on ? "on" : "off") : label, toggle: true, cls: "tiny" });
    if (on) b.classList.add("on");
    b.addEventListener("pointerup", () => {
      const v = b.classList.contains("on") ? 1 : 0;
      if (onOffLabels) b.textContent = v ? "on" : "off";
      set(path, v);
    });
    head.append(b);
    return b;
  };

  if (fxTab === "input") {
    const left = el("div", "drawer-col");
    const right = el("div", "drawer-col");
    body.append(left, right);

    // ---- input source, gain & compressor ----
    {
      const [c, h] = makeCard(left, "input & compressor");
      toggle(h, t && t.compOn, "compressor", `t${i}.compOn`);
      if (Number(CFG.loop?.monitor ?? 0) === 0) {
        c.append(el("div", "note", "software monitor off — you hear your interface directly"));
      }
      const sources = inputOptions();
      const pickSource = (v) => set(`t${i}.input`, v);
      c.append(sources.length <= 4
        ? Segmented({ options: sources, value: t ? t.input : 0, onPick: pickSource })
        : Select({ options: sources, value: t ? t.input : 0, onPick: pickSource }));
      const k = knobs(c);
      k.append(Knob({
        label: "gain", min: 0, max: 2, value: t ? t.gain : 1,
        format: (v) => `${Math.round(v * 100)}%`, onInput: (v) => set(`t${i}.gain`, v),
      }));
      k.append(Knob({
        label: "rec level", min: 0, max: 1.5, value: t ? t.recGain : 1,
        format: (v) => `${Math.round(v * 100)}%`, onInput: (v) => set(`t${i}.recGain`, v),
      }));
      k.append(Knob({
        label: "comp thr", min: 0.05, max: 0.95, value: t ? t.compThr : 0.6,
        format: (v) => `${Math.round(v * 100)}%`, onInput: (v) => set(`t${i}.compThr`, v),
      }));
      k.append(Knob({
        label: "comp ratio", min: 0.05, max: 1, value: t ? t.compRatio : 0.4,
        format: (v) => `${(1 / v).toFixed(1)}:1`, onInput: (v) => set(`t${i}.compRatio`, v),
      }));
    }

    // ---- noise gate & auto-rec ----
    {
      const [c, h] = makeCard(right, "noise gate & auto-rec");
      toggle(h, t && t.gateOn, "on", `t${i}.gateOn`, true);
      toggle(h, Number(getPath(CFG, `t${i}.autoArm`) ?? 0) === 1, "auto rec", `t${i}.autoArm`);
      knobs(c).append(Knob({
        label: "threshold", min: 0.002, max: 0.4, value: t ? t.gateThr : 0.02, curve: 2,
        format: (v) => `${(20 * Math.log10(v)).toFixed(0)} dB`,
        onInput: (v) => set(`t${i}.gateThr`, v),
      }));
      c.append(el("div", "note",
        "auto rec arms this track so the first sound above the gate threshold starts recording"));
    }

    // ---- output bus & cue ----
    {
      const [c, h] = makeCard(right, "output routing & cue");
      toggle(h, t && t.cueOn, "cue", `t${i}.cueOn`);
      c.append(Segmented({
        options: [{ label: "bus A (main)", value: 0 }, { label: "bus B", value: 1 }, { label: "bus C", value: 2 }],
        value: t ? t.route : 0,
        onPick: (v) => set(`t${i}.route`, v),
      }));
      knobs(c).append(Knob({
        label: "cue send", min: 0, max: 1.2, value: t ? t.cueSend : 0.6,
        format: (v) => `${Math.round(v * 100)}%`,
        onInput: (v) => set(`t${i}.cueSend`, v),
      }));
    }
  }

  if (fxTab === "loop") {
    const left = el("div", "drawer-col");
    const right = el("div", "drawer-col");
    body.append(left, right);

    // ---- layers ----
    {
      const [c, h] = makeCard(left, "layers & playback");
      toggle(h, t && t.oneshot, "one-shot", `t${i}.oneshot`);
      const flatten = confirmButton("flatten", () => act("mergeLayers", i), "tiny");
      h.append(flatten);
      const row = el("div", "layerbank");
      const n = t ? t.layers : 0;
      for (let k = 0; k < 4; k++) {
        const b = Button({ label: `layer ${k + 1}`, cls: "tiny", toggle: true });
        if (k >= n) b.classList.add("off");
        b.addEventListener("pointerup", () => set(`t${i}.lmute`, k));
        row.append(b);
      }
      c.append(row);
      c.append(el("div", "subhead", "mark back"));
      const markRow = el("div", "row2");
      const mark = Button({ label: "set mark", cls: "tiny" });
      mark.addEventListener("pointerup", () => {
        act("mark", i);
        toast(`mark set at ${t ? t.layers : 0} layers`);
      });
      const back = Button({ label: "mark back", cls: "tiny" });
      back.addEventListener("pointerup", () => {
        act("markBack", i);
        toast("back to the mark");
      });
      markRow.append(mark, back);
      c.append(markRow);
      c.append(el("div", "note",
        "tap a layer to mute/unmute · flatten sums layers into L1 · mark back jumps to a bookmarked layer count"));
    }

    // ---- time, speed & fades ----
    {
      const [c, h] = makeCard(right, "time, varispeed & fades");
      toggle(h, t && t.reverse, "reverse", `t${i}.reverse`);
      const fadeReset = Button({ label: "fades 0", cls: "tiny" });
      fadeReset.addEventListener("pointerup", () => {
        set(`t${i}.fadeIn`, 0);
        set(`t${i}.fadeOut`, 0);
      });
      h.append(fadeReset);
      c.append(el("div", "subhead", "loop length subdivision"));
      c.append(Segmented({
        options: RATIOS, value: t ? t.loopRatio : 1,
        onPick: (v) => set(`t${i}.loopRatio`, v),
      }));
      c.append(el("div", "subhead", "varispeed presets"));
      c.append(Segmented({
        options: SPEEDS, value: t ? t.speed : 1,
        onPick: (v) => set(`t${i}.speed`, v),
      }));
      const k = knobs(c);
      k.append(Knob({
        label: "speed", min: 0.25, max: 4, value: t ? t.speed : 1, curve: 2,
        format: (v) => `${Number(v).toFixed(2)}×`,
        onInput: (v) => set(`t${i}.speed`, v),
      }));
      k.append(Knob({
        label: "fade in", min: 0, max: 5, value: t ? t.fadeIn : 0, curve: 2,
        format: (v) => `${v.toFixed(2)} s`, onInput: (v) => set(`t${i}.fadeIn`, v),
      }));
      k.append(Knob({
        label: "fade out", min: 0, max: 5, value: t ? t.fadeOut : 0, curve: 2,
        format: (v) => `${v.toFixed(2)} s`, onInput: (v) => set(`t${i}.fadeOut`, v),
      }));
    }
  }

  if (fxTab === "fx") {
    const left = el("div", "drawer-col");
    const right = el("div", "drawer-col");
    body.append(left, right);

    // ---- filter & step sequencer ----
    {
      const [c, h] = makeCard(left, "filter & step sequencer");
      toggle(h, t && t.seqOn, "seq", `t${i}.seqOn`);
      const k = knobs(c);
      k.append(Knob({
        label: "high pass", min: 20, max: 2000, value: t ? t.hp : 40, curve: 2.4,
        format: (v) => `${Math.round(v)} Hz`, onInput: (v) => set(`t${i}.hp`, Math.round(v)),
      }));
      k.append(Knob({
        label: "low pass", min: 400, max: 18000, value: t ? t.lp : 16000, curve: 2.4,
        format: (v) => (v >= 1000 ? `${(v / 1000).toFixed(1)}k` : `${Math.round(v)}`),
        onInput: (v) => set(`t${i}.lp`, Math.round(v)),
      }));
      c.append(el("div", "subhead", "8-step low-pass sequence"));
      const seqRow = el("div", "steprow");
      const steps = SEQ[i] || (SEQ[i] = [0, 0.25, 0.5, 0.75, 1, 0.75, 0.5, 0.25]);
      for (let st = 0; st < 8; st++) {
        const cell = el("button", "stepcell");
        cell.type = "button";
        cell.style.setProperty("--h", String(steps[st]));
        if (steps[st] > 0) cell.classList.add("on");
        cell.addEventListener("pointerup", () => {
          steps[st] = steps[st] >= 1 ? 0 : Math.round((steps[st] + 0.25) * 100) / 100;
          cell.style.setProperty("--h", String(steps[st]));
          cell.classList.toggle("on", steps[st] > 0);
          svc("seq", [i, steps.join(",")]);
        });
        seqRow.append(cell);
      }
      c.append(seqRow);
      c.append(el("div", "note",
        "tap any step to cycle its cutoff lift (0–100%), enable seq above to animate the filter"));
    }

    // ---- delay & reverb send ----
    {
      const [c, h] = makeCard(right, "delay & reverb send");
      toggle(h, t && t.delayOn, "on", `t${i}.delayOn`, true);
      const k = knobs(c);
      k.append(Knob({
        label: "delay time", min: 0.06, max: 2, value: t ? t.delayBeats : 0.75, curve: 1.8,
        format: (v) => `${v.toFixed(2)} bt`, onInput: (v) => set(`t${i}.delayBeats`, v),
      }));
      k.append(Knob({
        label: "decay", min: 0.05, max: 8, value: t ? t.delayDecay : 0.4, curve: 2,
        format: (v) => `${v.toFixed(2)} s`, onInput: (v) => set(`t${i}.delayDecay`, v),
      }));
      k.append(Knob({
        label: "delay mix", min: 0, max: 1, value: t ? t.delayMix : 0.25,
        format: (v) => `${Math.round(v * 100)}%`, onInput: (v) => set(`t${i}.delayMix`, v),
      }));
      k.append(Knob({
        label: "reverb send", min: 0, max: 1, value: t ? t.revSend : 0,
        format: (v) => `${Math.round(v * 100)}%`, onInput: (v) => set(`t${i}.revSend`, v),
      }));
      c.append(el("div", "note",
        "reverb sends into the shared master FreeVerb2 (adjust room size & mix in Setup → Loop)"));
    }
  }

  if (fxTab === "plugins") {
    const left = el("div", "drawer-col");
    const right = el("div", "drawer-col");
    body.append(left, right);

    // ---- plugin insert slots ----
    {
      const [c] = makeCard(left, "vst insert chain");
      const list = el("div", "slots");
      const csv = (VST || "").split(";")[i] || "";
      const slots = csv.split(",").map((x) => x.split("|"));
      for (let s = 0; s < (STATS.slots || 4); s++) {
        const row = el("div", "slot");
        const name = slots[s] && slots[s][1] ? slots[s][1] : "";
        const bypassed = slots[s] && String(slots[s][2]) === "1";
        if (name) row.classList.add("filled");
        const numBadge = el("span", "slot-num", String(s + 1));
        const nm = el("span", "nm", name || `empty slot ${s + 1}`);
        const acts = el("div", "acts");
        if (name) {
          const ed = Button({ label: "ui", cls: "tiny" });
          ed.addEventListener("pointerup", () => svc("vstEditor", [i, s]));
          const by = Button({ label: "byp", cls: "tiny", toggle: true });
          if (bypassed) by.classList.add("on");
          const momentary = String((slots[s] && slots[s][4]) || "0") === "1";
          if (momentary) {
            by.addEventListener("pointerdown", () => svc("vstBypass", [i, s, 0]));
            by.addEventListener("pointerup", () => svc("vstBypass", [i, s, 1]));
          } else {
            by.addEventListener("pointerup", () =>
              svc("vstBypass", [i, s, by.classList.contains("on") ? 1 : 0]));
          }
          const md = Button({ label: "mom", cls: "tiny", toggle: true });
          if (momentary) md.classList.add("on");
          md.addEventListener("pointerup", () =>
            svc("vstMode", [i, s, md.classList.contains("on") ? 1 : 0]));
          const rm = Button({ label: "×", cls: "tiny danger" });
          rm.addEventListener("pointerup", () => svc("vstClose", [i, s]));
          acts.append(ed, by, md, rm);
        } else {
          const pick = Select({
            options: [{ label: "load plugin…", value: "" }].concat(
              (VSTCATALOG.length ? VSTCATALOG : ["(no plugins found)"]).map((n) => ({ label: n, value: n })),
            ),
            value: "",
            onPick: (v) => {
              if (v && v !== "(no plugins found)") svc("vstOpen", [i, s, v]);
            },
          });
          acts.append(pick);
        }
        row.append(numBadge, nm, acts);
        list.append(row);
      }
      c.append(list);
      if (STATS.vst !== 1) {
        c.append(el("div", "note", "VSTPlugin extension is not loaded on this SuperCollider installation"));
      } else if (!VSTCATALOG.length) {
        c.append(el("div", "note", `scanning ${STATS.vstCount} plugins…`));
      }
    }

    // ---- FX banks ----
    {
      const [c] = makeCard(right, "plugin banks");
      c.append(el("div", "note",
        "store the 4 insert slots into a bank or recall a saved chain for this track"));
      for (let b = 0; b < 4; b++) {
        const sv = Button({ label: "store", cls: "tiny" });
        sv.addEventListener("pointerup", () => {
          svc("vstBankSave", [i, b]);
          toast(`track ${i + 1}: stored bank ${b + 1}`);
        });
        const ld = Button({ label: "recall", cls: "tiny" });
        ld.addEventListener("pointerup", () => {
          svc("vstBankLoad", [i, b]);
          toast(`track ${i + 1}: recalled bank ${b + 1}`);
        });
        const ctl = el("div", "ctl");
        ctl.append(sv, ld);
        c.append(settingsRow(`bank ${b + 1}`, ctl));
      }
    }
  }

  drawer.classList.add("open");
}

/* -------------------------------------------------------------- settings -- */
function openSettings() {
  const drawer = document.getElementById("settings");
  drawer.classList.add("open");
  svc("stats");
  svc("midiMap");
  svc("vstList");
  svc("vstCatalog");
  send({ op: "devices" });
  renderSettings();
}

function settingsRow(label, ctl, hint) {
  const r = el("div", "cfgrow");
  const left = el("div", "lblcol");
  left.append(el("span", "lbl", label));
  if (hint) left.append(el("span", "hint", hint));
  const wrap = ctl.classList?.contains("ctl") ? ctl : el("div", "ctl");
  if (wrap !== ctl) wrap.append(ctl);
  r.append(left, wrap);
  return r;
}

function rowStepper(label, path, { min, max, step, format, src = "cfg", hint, fallback }) {
  const raw = src === "set"
    ? (S && S[path] != null ? S[path] : fallback)
    : getPath(CFG, path);
  const value = Number(raw ?? fallback ?? min);
  const s = Stepper({
    min, max, step, value, format: format || ((v) => String(v)),
    onInput: (v) => (src === "cfg" ? cfgSet(path, v) : set(`g.${path}`, v)),
  });
  return settingsRow(label, s, hint);
}

function rowToggle(label, path, { src = "cfg", hint } = {}) {
  const on = Boolean(Number(getPath(CFG, path) ?? 0));
  const b = Button({ label: on ? "on" : "off", toggle: true, cls: "tiny" });
  if (on) b.classList.add("on");
  b.addEventListener("pointerup", () => {
    const v = b.classList.contains("on") ? 1 : 0;
    b.textContent = v ? "on" : "off";
    if (src === "cfg") cfgSet(path, v);
    else set(`g.${path}`, v);
  });
  return settingsRow(label, b, hint);
}

function rowSelect(label, path, options, { src = "cfg", hint, onChange } = {}) {
  const value = getPath(CFG, path) ?? "";
  const s = Select({
    options, value: String(value ?? ""),
    onPick: (v) => {
      if (src === "cfg") cfgSet(path, v);
      else set(`g.${path}`, v);
      if (onChange) onChange(v);
    },
  });
  return settingsRow(label, s, hint);
}

function rowButton(label, text, fn, hint) {
  const b = Button({ label: text, cls: "tiny" });
  b.addEventListener("pointerup", fn);
  return settingsRow(label, b, hint);
}

function card(parent, heading) {
  const c = el("div", "fxcard");
  const h = el("div", "head");
  h.append(el("div", "caption", heading));
  const inner = el("div", "card-body");
  c.append(h, inner);
  parent.append(c);
  return inner;
}

const SETUP_TABS = [
  { id: "audio", label: "audio" },
  { id: "loop", label: "loop & master" },
  { id: "clock", label: "clock" },
  { id: "midi", label: "midi" },
  { id: "session", label: "session" },
  { id: "ui", label: "ui" },
  { id: "505", label: "505 guide" },
];

const MIDI_ACTION_LABELS = {
  press: "rec / play / stop",
  stop: "stop track",
  undo: "undo layer",
  clear: "clear track",
  merge: "flatten layers",
  auto: "arm auto-rec",
  tap: "tap tempo",
  stopAll: "stop all tracks",
  metro: "toggle metronome",
};

function renderSettings() {
  const drawer = document.getElementById("settings");
  drawer.querySelector("h2").textContent = "setup";
  const tabs = drawer.querySelector(".tabs");
  tabs.replaceChildren();
  SETUP_TABS.forEach((tab) => {
    const b = Button({ label: tab.label, cls: `tiny${tab.id === settingsTab ? " on" : ""}` });
    b.dataset.tab = tab.id;
    b.addEventListener("pointerup", () => {
      settingsTab = tab.id;
      if (tab.id === "audio") send({ op: "devices" });
      if (tab.id === "midi") { svc("midiMap"); svc("midiDevs"); }
      renderSettings();
    });
    tabs.append(b);
  });

  const body = drawer.querySelector(".body");
  body.replaceChildren();
  body.classList.toggle("single", settingsTab === "ui");

  if (settingsTab === "audio") {
    const left = el("div", "drawer-col");
    const right = el("div", "drawer-col");
    body.append(left, right);

    const drv = String(CFG.audio?.driver ?? "auto");
    const dev = CFG.audio?.device ?? null;

    const c = card(left, "driver & latency");
    const drivers = ui.drivers || [
      { label: "auto (jack if present)", value: "auto" },
      { label: "jack / pipewire", value: "jack" },
      { label: "alsa (direct)", value: "alsa" },
    ];
    c.append(rowSelect("driver", "audio.driver", drivers, { hint: "requires restart" }));
    c.append(el("div", "note",
      ui.platform === "windows"
        ? (drv === "asio"
            ? "ASIO takes the device exclusively for lowest latency; nothing else can play meanwhile"
            : "WASAPI is the normal Windows path; choose the endpoint below")
        : (drv === "alsa"
            ? "ALSA opens the hardware directly; nothing else can use it while running"
            : "scsynth joins the PipeWire graph as a node — route ports in qpwgraph")));

    if (ui.platform === "windows" ? drv !== "auto" : drv === "alsa") {
      const win = ui.platform === "windows";
      const pool = win
        ? [...(ui.devices?.sinks || []), ...(ui.devices?.sources || [])]
        : (ui.devices?.alsa || []);
      const opts = [{ label: "(system default)", value: "" }].concat(
        pool.map((d) => ({ label: `${d.id} — ${d.label}`, value: d.id })),
      );
      c.append(rowSelect("device", "audio.device", opts,
        { hint: win ? "endpoint · restart" : "hw:N,M · restart" }));
    } else if (dev) {
      c.append(settingsRow("device", el("span", "num", String(dev))));
      c.append(rowButton("stale device", "clear", () => {
        cfgSet("audio.device", null);
        CFG.audio.device = null;
        toast("device cleared — restart to apply");
        renderSettings();
      }, "only ALSA uses this"));
    }

    if (ui.platform !== "windows") {
      const qsr = Number(CFG.audio?.sampleRate ?? 48000) || 48000;
      const qNow = Number(CFG.audio?.quantum ?? 0) || 0;
      const qOpts = [{ label: "leave alone", value: 0 }].concat(
        [64, 128, 256, 512, 1024].map((n) => ({
          label: `${n} frames · ${((n / qsr) * 1000).toFixed(1)} ms`, value: n,
        })),
      );
      c.append(rowSelect("pipewire quantum", "audio.quantum", qOpts, { hint: "restart to apply" }));
      c.append(el("div", "note", qNow
        ? `period ${((qNow / qsr) * 1000).toFixed(1)} ms · round trip ≈ ${(((2 * qNow) / qsr) * 1000).toFixed(1)} ms · raise to 256 if you hear dropouts with many tracks`
        : "unforced — PipeWire decides (usually 1024 ≈ 21 ms)"));
    }

    const io = card(left, "engine i/o");
    io.append(
      rowStepper("sample rate", "audio.sampleRate", {
        min: 0, max: 192000, step: 1000,
        format: (v) => (v ? `${Math.round(v / 1000)}k` : "default"),
      }),
      rowStepper("block size", "audio.blockSize", { min: 16, max: 2048, step: 16, hint: "restart" }),
      rowStepper("hw buffer", "audio.hardwareBufferSize", { min: 32, max: 4096, step: 32, hint: "restart" }),
      rowStepper("inputs", "audio.in", { min: 1, max: 16, step: 1, hint: "restart" }),
      rowStepper("outputs", "audio.out", { min: 1, max: 16, step: 1, hint: "restart" }),
    );

    const s = card(right, "engine status");
    const info = el("div", "stat-strip", "");
    info.id = "statsline";
    s.append(info);
    renderStats();
    s.append(
      rowButton("engine", "restart", () => {
        toast("restarting engine…");
        send({ op: "restart" });
      }, "re-reads audio & track config"),
      rowButton("stats", "refresh", () => svc("stats"), "cpu & latency estimate"),
    );

    if (drv !== "alsa") {
      const g = card(right, "live connections (pipewire)");
      const ins = ui.devices?.links?.inputs || [];
      const outs = ui.devices?.links?.outputs || [];
      const inBox = el("div", "link-item");
      inBox.append(el("div", "caption", "inputs feeding SuperCollider"));
      inBox.append(el("div", "link-val", ins.length ? ins.join("  ·  ") : "nothing connected"));
      const outBox = el("div", "link-item");
      outBox.append(el("div", "caption", "outputs from SuperCollider"));
      outBox.append(el("div", "link-val", outs.length ? outs.join("  ·  ") : "nothing connected"));
      g.append(inBox, outBox);
      g.append(rowButton("graph", "refresh links", () => send({ op: "devices" }),
        "reroute ports in qpwgraph, then refresh"));
    }
  }

  if (settingsTab === "loop") {
    const left = el("div", "drawer-col");
    const right = el("div", "drawer-col");
    body.append(left, right);

    const c = card(left, "looping & recording");
    c.append(
      rowStepper("tracks", "tracks", { min: 1, max: 16, step: 1, hint: "restart" }),
      rowStepper("layers / track", "maxLayers", { min: 1, max: 4, step: 1, hint: "restart" }),
      rowStepper("max loop", "loop.maxSeconds", {
        min: 4, max: 120, step: 4, format: (v) => `${v}s`, hint: "restart",
      }),
      rowStepper("seam crossfade", "loop.crossfadeMs", {
        min: 0.1, max: 50, step: 0.1, format: (v) => `${v.toFixed(1)} ms`,
      }),
      rowStepper("record offset", "loop.recordOffset", {
        min: -4096, max: 4096, step: 8, format: (v) => `${v} smp`,
        hint: "compensate interface latency",
      }),
      rowStepper("input monitor", "loop.monitor", {
        min: 0, max: 1.5, step: 0.1,
        format: (v) => (v === 0 ? "off" : `${Math.round(v * 100)}%`),
        hint: "0 = loop only (use direct interface monitoring)",
      }),
      rowToggle("auto-close 1st layer", "loop.autoClose", {
        hint: "close layer 1 automatically at the loop boundary",
      }),
      rowToggle("continue into overdub", "loop.autoDub", {
        hint: "roll straight into layer 2 after layer 1 closes",
      }),
      rowToggle("overdub replaces", "loop.overdubMode", {
        hint: "new take mutes layers below; undo restores them",
      }),
      rowToggle("quantize start", "loop.quantizeStart", {
        hint: "wait for bar 1 before recording starts",
      }),
      rowStepper("dub decay", "loop.dubDecay", {
        min: 0, max: 0.95, step: 0.05, format: (v) => `${Math.round(v * 100)}%`,
        hint: "fade playing layers each pass",
      }),
    );

    const m = card(right, "master chain");
    m.append(
      rowStepper("high pass", "masterHp", {
        min: 0, max: 2000, step: 10, src: "set", fallback: 0, format: (v) => `${v} Hz`,
      }),
      rowStepper("low pass", "masterLp", {
        min: 1000, max: 19000, step: 250, src: "set", fallback: 18000, format: (v) => `${v} Hz`,
      }),
      rowStepper("reverb mix", "loop.revMix", {
        min: 0, max: 1, step: 0.05, format: (v) => `${Math.round(v * 100)}%`,
      }),
      rowStepper("room size", "loop.room", {
        min: 0, max: 1, step: 0.05, format: (v) => `${Math.round(v * 100)}%`,
      }),
      rowStepper("dj resonance", "master.djRes", {
        min: 0, max: 1, step: 0.05, format: (v) => `${Math.round(v * 100)}%`,
      }),
    );

    const fxc = card(right, "master fx slots");
    FX_TYPES.forEach((name, slot) => {
      const slotType = Math.round(Number(getPath(CFG, `master.fx${slot + 1}Type`) ?? 0));
      const box = el("div", "mfx-slot");
      const top = el("div", "mfx-top");
      top.append(el("span", "lbl", name));
      const opts = [
        { label: "off", value: 0 },
        { label: "filter", value: 1 },
        { label: "delay", value: 2 },
        { label: "drive", value: 3 },
        { label: "tremolo", value: 4 },
      ];
      const sel = Select({
        options: opts,
        value: slotType,
        onPick: (v) => {
          const nv = Number(v);
          CFG.master = { ...(CFG.master || {}), [`fx${slot + 1}Type`]: nv };
          cfgSet(`master.fx${slot + 1}Type`, nv);
          renderSettings();
        },
      });
      top.append(sel);
      box.append(top);
      const labels = FX_KNOBS[slotType] || FX_KNOBS[0];
      const krow = el("div", "knobs");
      ["A", "B"].forEach((k, idx) => {
        krow.append(Knob({
          min: 0, max: 1, value: Number(getPath(CFG, `master.fx${slot + 1}${k}`) ?? 0.5),
          label: labels[idx],
          format: (v) => `${Math.round(v * 100)}%`,
          onInput: (v) => {
            set(`g.fx${slot + 1}${k}`, v);
            cfgSet(`master.fx${slot + 1}${k}`, v);
          },
        }));
      });
      box.append(krow);
      fxc.append(box);
    });
  }

  if (settingsTab === "clock") {
    const left = el("div", "drawer-col");
    const right = el("div", "drawer-col");
    body.append(left, right);

    const c = card(left, "metronome");
    c.append(
      rowStepper("click level", "clock.clickVol", {
        min: 0, max: 1, step: 0.05, format: (v) => `${Math.round(v * 100)}%`,
      }),
      rowStepper("count-in bars", "clock.countInBars", { min: 0, max: 8, step: 1 }),
      rowStepper("accent pitch", "clock.accentHz", {
        min: 200, max: 4000, step: 20, format: (v) => `${v} Hz`, hint: "restart",
      }),
      rowStepper("click pitch", "clock.clickHz", {
        min: 200, max: 4000, step: 20, format: (v) => `${v} Hz`, hint: "restart",
      }),
    );

    const lk = card(right, "ableton link");
    lk.append(rowToggle("follow link", "clock.link", {
      hint: "sync tempo and bar phase with Link peers on your local network",
    }));
    const status = el("div", "stat-strip", "no peer");
    status.id = "linkstatus";
    lk.append(settingsRow("status", status));
  }

  if (settingsTab === "ui") {
    const c = card(body, "display & touch");
    const scale = el("div", "ctl");
    const sf = Fader({
      min: 0.75, max: 1.75, value: Number(CFG.ui?.scale ?? 1),
      format: (v) => `${Math.round(v * 100)}%`,
      onInput: (v) => {
        CFG.ui.scale = Number(v.toFixed(2));
        applyUiConfig();
        cfgSet("ui.scale", Number(v.toFixed(2)));
      },
    });
    scale.append(sf);
    c.append(
      settingsRow("scale", scale, "scales every control proportionally"),
      rowToggle("show master bar", "ui.showMaster", { hint: "hide footer for extra track height" }),
      rowButton("display", "fullscreen", () => {
        if (document.fullscreenElement) document.exitFullscreen();
        else document.documentElement.requestFullscreen?.();
      }),
      rowButton("touch targets", "check 48px", () => {
        const r = auditTapTargets(48);
        toast(r.bad.length ? `${r.bad.length} too small` : `all ${r.total} targets ok`);
      }, "toggle dev overlay with key d"),
    );
  }

  if (settingsTab === "midi") {
    const left = el("div", "drawer-col");
    const right = el("div", "drawer-col");
    body.append(left, right);

    const d = card(left, "midi input");
    const devSel = Select({
      options: [{ label: "all sources", value: -1 }].concat(
        MIDIDEVS.map((x) => ({ label: x.name, value: x.idx })),
      ),
      value: String(Number(CFG.midi?.device ?? -1)),
      onPick: (v) => {
        const idx = Number(v);
        CFG.midi = { ...(CFG.midi || {}), device: idx };
        svc("midiDevice", [idx]);
        cfgSet("midi.device", idx);
      },
    });
    d.append(settingsRow("device", devSel, `${MIDIDEVS.length} sources`));
    d.append(rowButton("preset map", "load default", () => svc("midiDefault", []),
      "CC 20–27 press · 30–37 undo · 40–47 stop · 50–57 clear · 60 tap"));

    const m = card(left, `active mappings (${MIDIMAP.length})`);
    m.append(rowButton("learned map", "clear all", () => svc("midiClear", [])));
    if (!MIDIMAP.length) {
      m.append(el("div", "note", "no mappings yet — tap Learn on the right, then press a pedal or key"));
    } else {
      MIDIMAP.forEach((mapping) => {
        const [type, num, tgt, gest] = mapping.split("|");
        const suffix = gest && gest !== "-" ? ` · ${gest}` : "";
        m.append(settingsRow(tgt, el("span", "num", `${type} ${num}${suffix}`)));
      });
    }

    const c = card(right, "midi learn");
    const nTracks = S ? S.tracks.length : (CFG.tracks || 5);
    const scopeTabs = el("div", "midi-tabs");
    const scopes = [];
    for (let k = 0; k < nTracks; k++) scopes.push({ id: `t${k}`, label: `T${k + 1}` });
    scopes.push({ id: "g", label: "global" });
    if (!scopes.some((sc) => sc.id === midiLearnScope)) midiLearnScope = "t0";
    scopes.forEach((sc) => {
      const b = Button({ label: sc.label, cls: `tiny${sc.id === midiLearnScope ? " on" : ""}` });
      b.addEventListener("pointerup", () => {
        midiLearnScope = sc.id;
        renderSettings();
      });
      scopeTabs.append(b);
    });
    c.append(scopeTabs);
    c.append(el("div", "note",
      "notes capture press, hold (>300 ms) or double-tap automatically while learning"));

    const verbs = midiLearnScope === "g"
      ? ["tap", "stopAll", "metro"]
      : ["press", "stop", "undo", "clear", "merge", "auto"];
    verbs.forEach((v) => {
      const tgt = `${midiLearnScope}.${v}`;
      const known = MIDIMAP.find((entry) => entry.split("|")[2] === tgt);
      const label = known ? known.split("|").slice(0, 2).join(" ") : "learn";
      const b = Button({ label, cls: `tiny${known ? " on" : ""}` });
      b.addEventListener("pointerup", () => {
        svc("midiLearn", [tgt]);
        toast(`move a MIDI control for ${tgt}`);
      });
      c.append(settingsRow(MIDI_ACTION_LABELS[v] || tgt, b, tgt));
    });
  }

  if (settingsTab === "505") {
    const left = el("div", "drawer-col");
    const right = el("div", "drawer-col");
    body.append(left, right);

    const MAP = [
      ["TRACK 1–5 pad", "the big pad on each column"],
      ["REC (start recording)", "tap the pad when empty"],
      ["REC again (close loop)", "tap the pad while recording / overdubbing"],
      ["OVERDUB", "press DUB under the pad (or roll over via auto-dub)"],
      ["PLAY / STOP", "tap the pad while stopped / playing, or press STOP"],
      ["CLEAR", "tap CLEAR twice within 2.5 s"],
      ["UNDO / REDO", "UNDO / REDO (up to 4 layers)"],
      ["MEASURE (loop length)", "BARS / LOOP in the top bar: 1 · 2 · 4 · 8"],
      ["TEMPO", "tap the BPM number, drag the fader, or press TAP"],
      ["RHYTHM", "DRUMS + kit (1–16) & pattern (1–200) in the top bar"],
      ["INPUT FX", "fx → input & out (source, gain, comp, gate, auto-rec)"],
      ["TRACK FX", "fx → filter & fx (HP/LP, step seq, delay, reverb)"],
      ["MASTER FX", "DJ filter in top bar; master FX slots in setup → loop"],
      ["SPEED CHANGE", "change BPM or fx → loop → speed (varispeed)"],
      ["MEMORY (phrase save)", "setup → session → save / load slot"],
      ["LOOP SYNC subdivision", "fx → loop & layers → 1 · 1/2 · 1/4"],
    ];
    const c = card(left, "RC-505 → Looper OSS");
    MAP.forEach(([a, b]) => {
      const row = el("div", "cfgrow map");
      row.append(el("span", "lbl", a), el("span", "desc", b));
      c.append(row);
    });

    const d = card(right, "design notes");
    [
      "Loop length comes from BARS + BPM rather than auto-detecting from the first take.",
      "Each track holds up to 4 independent layers for instant non-destructive undo/redo and per-layer mute.",
      "Overdubbing adds onto existing audio across loop wraps so punching out late never erases your downbeat.",
      "Built-in rhythm engine synthesises 16 kits × 200 patterns with stock UGens (no external samples required).",
      "Track effects combine stock SuperCollider UGens with up to 4 VST3/VST2 plugin insert slots per track.",
    ].forEach((text) => d.append(el("div", "note", text)));
  }

  if (settingsTab === "session") {
    const left = el("div", "drawer-col");
    const right = el("div", "drawer-col");
    body.append(left, right);

    const c = card(left, "phrase memory & export");
    const slotStepper = Stepper({
      min: 1, max: 99, step: 1, value: Number(CFG.session?.slot ?? 1),
      onInput: (v) => {
        CFG.session = { ...(CFG.session || {}), slot: v };
        cfgSet("session.slot", v);
      },
    });
    c.append(settingsRow("active slot", slotStepper));
    const browsed = el("div", "slotlist");
    browsed.id = "slotlist";
    c.append(settingsRow("saved memories", browsed, "tap a slot number to load it"));
    send({ op: "sessions" });
    c.append(
      rowButton("save", "save slot", () => svc("save", [Number(CFG.session?.slot ?? 1)])),
      rowButton("load", "load slot", () => svc("load", [Number(CFG.session?.slot ?? 1)])),
      rowButton("export mixdown", "bounce 30 s", () => svc("export", [30]),
        "saves stereo WAV to sessions/exports/"),
      rowButton("bounce all", "sum into track 1", () => {
        act("bounce", 0);
        toast("bouncing every track into track 1…");
      }, "one loop pass; source tracks are cleared"),
    );
    c.append(el("div", "note", "slots save to sessions/slotN/ (WAV per layer + session.json + VST state)"));

    const auto = card(right, "autosave & backups");
    auto.append(
      rowToggle("autosave", "loop.autosave", { hint: "recovers the last session stopped on start" }),
      rowButton("autosave data", "clear autosave", () => {
        svc("clearAutosave", []);
        toast("autosave cleared");
      }, "automatic when all tracks are cleared"),
      rowButton("backup", "zip sessions", () => send({ op: "backup" }),
        "packs all slots into one zip archive"),
      rowButton("restore", "restore newest", () => send({ op: "restore" }),
        "overwrites sessions/ from the newest zip"),
    );

    const imp = card(right, "import wav to track");
    const nTracks = S ? S.tracks.length : (CFG.tracks || 5);
    if (importTrack >= nTracks) importTrack = selected < nTracks ? selected : 0;
    const trkOpts = Array.from({ length: nTracks }, (_, k) => ({ label: `track ${k + 1}`, value: k }));
    const trkSel = Select({
      options: trkOpts,
      value: importTrack,
      onPick: (v) => { importTrack = Number(v); },
    });
    const impSel = Select({
      options: (IMPORTS.length ? IMPORTS : ["(none)"]).map((n) => ({ label: n, value: n })),
      value: IMPORTS[0] ?? "(none)",
      onPick: () => {},
    });
    const impBtn = Button({ label: "import", cls: "tiny" });
    impBtn.addEventListener("pointerup", () => {
      const name = impSel.value;
      if (name && name !== "(none)") {
        svc("import", [importTrack, name]);
        toast(`importing ${name} into track ${importTrack + 1}`);
      }
    });
    imp.append(
      settingsRow("target track", trkSel),
      settingsRow("wav file", impSel),
      settingsRow("load into layer 1", impBtn),
      el("div", "note", "drop WAV files into sessions/import/ and reopen this tab to refresh"),
    );
    send({ op: "importList" });
  }
}

function renderStats() {
  const line = document.getElementById("statsline");
  if (!line) return;
  const lat = STATS.latency ? `${(STATS.latency * 1000).toFixed(1)} ms` : "—";
  line.textContent =
    `cpu ${(STATS.cpu ?? 0).toFixed(1)}%  ·  ${(STATS.sr / 1000 || 0).toFixed(1)} kHz  ·  ` +
    `block ${STATS.blockSize || 0} / buffer ${STATS.hwBuffer || 0}  ·  ~${lat}  ·  ` +
    `vst ${STATS.vst === 1 ? `${STATS.vstCount} found` : "off"}`;
}

function renderDevices(m) {
  ui.devices = {
    sinks: m.sinks || [], sources: m.sources || [], alsa: m.alsa || [],
    links: m.links || { inputs: [], outputs: [] },
  };
  if (settingsTab === "audio") renderSettings();
}

/* --------------------------------------------------------------- keyboard -- */
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    document.getElementById("fx").classList.remove("open");
    document.getElementById("settings").classList.remove("open");
  }
  if (e.key === "d") {
    const layer = document.getElementById("devoverlay");
    layer.classList.toggle("on");
    if (layer.classList.contains("on")) auditTapTargets(48);
    else document.getElementById("devbadge").classList.remove("on");
  }
  const n = parseInt(e.key, 10);
  if (!Number.isNaN(n) && n >= 1 && n <= ui.tracks.length) act("press", n - 1);
  if (e.key === "s") act("stop", selected);
  if (e.key === "u") act("undo", selected);
  if (e.key === " ") {
    e.preventDefault();
    act("stopAll", 0);
  }
});

/* ----------------------------------------------------------------- render -- */
function onState(s) {
  const first = !S;
  if (first) setBoot("", true);          // first state frame = engine is up
  S = s;
  if (first || ui.tracks.length !== s.tracks.length) buildTracks(s.tracks.length);

  ui.master.inL?.set(s.inL ?? 0);
  ui.master.inR?.set(s.inR ?? 0);
  ui.master.out?.set(Math.max(s.masterL ?? 0, s.masterR ?? 0));

  ui.transport.bpmVal.textContent = Math.round(s.bpm);
  if (!ui.transport.bpmFader.classList.contains("active")) ui.transport.bpmFader.setValue(s.bpm);
  ui.transport.bars.setValue(s.bars);
  ui.transport.metro.classList.toggle("on", !!s.metro);
  ui.transport.countIn.classList.toggle("on", !!s.countIn);
  {
    if (ui.buses) {
      [s.busA, s.busB, s.busC].forEach((v, k) => {
        if (!ui.buses[k].classList.contains("active")) ui.buses[k].setValue(v);
      });
    }
    ui.master.vol.setValue(s.masterVol);
    // the DJ knob follows the engine: a MIDI pedal can move it, and after a
    // restart it comes back centred, which the knob must show
    if (ui.transport?.dj && !ui.transport.dj.classList.contains("active")) {
      ui.transport.dj.setValue(s.dj ?? 0);
    }
  }

  s.tracks.forEach((t, i) => {
    const u = ui.tracks[i];
    if (!u) return;
    const isOdub = t.state === 1 && t.layers > 0;
    const stateKey = isOdub ? "odub" : t.state;
    if (u.stateKey !== stateKey) {
      u.stateKey = stateKey;
      const [label, sub] = isOdub
        ? ["OVERDUB", "tap to finish"]
        : (STATE_TEXT[t.state] || STATE_TEXT[0]);
      u.pad.setLabel(label, sub);
      // classList, not className: the pad carries a stable "main" class too
      u.pad.classList.remove("rec", "play", "odub", "stopped");
      const cls = isOdub ? "odub" : STATE_CLASS[t.state];
      if (cls) u.pad.classList.add(cls);
    }
    const marks = t.undoDepth > t.layers ? ` · ${t.undoDepth - t.layers} undo` : "";
    // layer peak: tells you a HOT layer even while the track is stopped
    const hot = t.layerPeak > 0.6 ? " · HOT" : "";
    u.layers.textContent = `${t.layers}L${marks}${hot}`;
    const tag = inputTag(t.input);
    if (u.insrc.textContent !== tag) u.insrc.textContent = tag;
    // REPLACE is a global mode, but its effect is per track: show it where the
    // player is looking, not only in the settings drawer.
    const rep = ui.replaceBadges?.[i];
    if (rep) rep.classList.toggle("on", Number(CFG.loop?.overdubMode ?? 0) === 1);
    if (!u.vol.classList.contains("active")) u.vol.setValue(t.vol);
    u.meter.set(t.meter);
    u.col.classList.toggle("selected", i === selected);
  });
}

/* ------------------------------------------------------------------- loop -- */
// The play head and the bar·beat readout are driven differently on purpose.
//
//   * the NUMBERS are the engine's beat, snapped. They change on the beat and on
//     nothing else, so what you read is what you are hearing -- the old code
//     eased them from beat to beat, which made them roll and made you miss one.
//   * the PLAY HEAD steps with it, on the same beat. Discrete on purpose: it
//     jumps to the next beat instead of sliding, so it shows where the loop is
//     rather than approximating it.
function tick() {
  if (S && ui.playheadFill) {
    const per = S.beatsPerBar || 4;
    const total = Math.max(1, (S.bars || 4) * per);
    const engine = Number.isFinite(S.phase) ? S.phase : 0;

    // The readout and the head follow the engine's own phase from the state
    // frame: up to ~50 ms behind, which is invisible for a bar·beat display and,
    // unlike deriving it from the beat events, cannot miscount at the loop point.
    playPos = engine;
    if (ui.transport?.posVal) {
      const idx = Math.round(engine * total) % total;
      const text = `${Math.floor(idx / per) + 1}·${(idx % per) + 1}`;
      if (ui.transport.posVal.textContent !== text) ui.transport.posVal.textContent = text;
    }

    ui.playheadFill.style.width = `${(playPos * 100).toFixed(2)}%`;
    if (ui.playheadTicks) {
      ui.playheadTicks.style.setProperty("--bars", String(Math.max(1, S.bars || 4)));
      ui.playheadTicks.style.setProperty("--beats", String(total));
    }
  }
  requestAnimationFrame(tick);
}

/* ------------------------------------------------------------------- boot -- */
async function boot() {
  try {
    const res = await fetch("config.json", { cache: "no-store" });
    if (res.ok) CFG = await res.json();
  } catch {
    /* defaults are fine */
  }
  if (CFG.osc && CFG.ui?.wsPort) wsUrl = `ws://${location.hostname}:${CFG.ui.wsPort}`;
  applyUiConfig();

  buildTransport();
  buildMaster();
  buildTracks(CFG.tracks || 5);

  document.getElementById("fx-close").addEventListener("pointerup", () => {
    document.getElementById("fx").classList.remove("open");
  });
  document.getElementById("settings-close").addEventListener("pointerup", () => {
    document.getElementById("settings").classList.remove("open");
    document.getElementById("devbadge").classList.remove("on");
    document.getElementById("devoverlay").classList.remove("on");
  });

  // small hook for the UI verification harness (scripts/uicheck.mjs)
  window.__ld = {
    audit: (min = 48) => auditTapTargets(min),
    state: () => S,
    config: () => CFG,
    cfg: (path, v) => cfgSet(path, v),
    set: (path, v) => set(path, v),
    openSettings,
    openFx,
  };

  // Closing the app window stops the engine (the bridge waits a grace period, so
  // a reload does not). Ctrl-C on the console stops this window via run.sh.
  window.addEventListener("pagehide", () => send({ op: "quit" }));
  window.addEventListener("beforeunload", () => send({ op: "quit" }));

  document.addEventListener("contextmenu", (e) => e.preventDefault());
  document.addEventListener("selectstart", (e) => e.preventDefault());

  connect();
  requestAnimationFrame(tick);
}

boot();
