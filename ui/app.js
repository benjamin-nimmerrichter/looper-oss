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
let phase = { value: 0, at: performance.now() };
let IMPORTS = [];
let SEQ = [];       // per-track step-sequence patterns (eight values 0..1)   // *.wav sitting in sessions/import/
// The bar eases toward the engine's loop points and stops when they stop moving.
// (It used to add elapsed time itself, so it kept moving even with the engine off.)
let playPos = 0;
let settingsTab = "audio";

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

function connect() {
  ws = new WebSocket(wsUrl);
  ws.onopen = () => {
    setBoot("engine starting…");
    toast("connected");
    svc("stats");
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
    min: 40, max: 240, value: 120, format: (v) => `${Math.round(v)}`,
    onInput: (v) => set("g.bpm", Math.round(v)),
  });
  bpmFader.style.flex = "1";
  const tap = Button({ label: "tap", cls: "tiny" });
  tap.addEventListener("pointerup", () => svc("tap"));
  bpmRow.append(bpmFader, tap);
  g1.append(bpmVal, bpmRow);

  const g2 = el("div", "group");
  g2.append(el("div", "caption", "bars / loop"));
  const bars = Segmented({
    options: BARS.map((b) => ({ label: `${b}`, value: b })),
    value: 4,
    onPick: (v) => set("g.bars", v),
  });
  g2.append(bars);

  // Where you are in the loop, in the units you count in. The engine already
  // ships `phase` (0..1 over the whole loop) plus bars/beatsPerBar, so this is
  // arithmetic, not protocol. It follows the same eased position as the
  // playhead, so the number and the blue line can never disagree.
  const gpos = el("div", "group");
  gpos.append(el("div", "caption", "position"));
  const posVal = el("div", "posval", "1·1");
  gpos.append(posVal);

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

  const g4 = el("div", "group grow");
  g4.append(el("div", "caption", "master"));
  const masterFader = Fader({
    min: 0, max: 1.2, value: 0.8, format: (v) => `${Math.round(v * 100)}`,
    onInput: (v) => set("g.masterVol", v),
  });
  g4.append(masterFader);

  // Rhythm engine: 16 synthesised kits, 200 patterns. Driven through the config
  // so it survives a restart, like every other setting.
  const gr = el("div", "group");
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

  root.append(g0, g1, g2, gpos, g3, gr, g4);
  ui.transport = { bpmVal, bpmFader, bars, metro, countIn, masterFader, posVal };
}

/* ----------------------------------------------------------------- tracks -- */
function buildTracks(n) {
  const root = document.getElementById("tracks");
  root.replaceChildren();

  const playhead = el("div", "playhead");
  const playheadFill = el("i");
  playhead.append(playheadFill);
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
    const clear = confirmButton("clear", () => {
      act("clear", i);
      toast(`track ${i + 1} cleared`);
    }, "tiny danger");
    rowB.append(undo, redo, clear);

    const meter = Meter();
    col.append(name, pad, rowA, vol, rowB, meter);
    root.append(col);
    ui.tracks.push({ col, pad, vol, meter, layers, insrc, clear, stateKey: -1 });
  }
  ui.playheadFill = playheadFill;
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
function openFx(i) {
  selected = i;
  const drawer = document.getElementById("fx");
  drawer.querySelector("h2").textContent = `track ${i + 1}`;
  const body = drawer.querySelector(".body");
  body.replaceChildren();
  const t = S ? S.tracks[i] : null;

  const card = (heading) => {
    const c = el("div", "fxcard");
    const h = el("div", "head");
    const cap = el("div", "caption", heading);
    h.append(cap);
    c.append(h);
    body.append(c);
    return [c, h];
  };
  const knobs = (parent) => {
    const k = el("div", "knobs");
    parent.append(k);
    return k;
  };
  const toggle = (head, on, label, path) => {
    const b = Button({ label, toggle: true, cls: "tiny" });
    if (on) b.classList.add("on");
    b.addEventListener("pointerup", () => set(path, b.classList.contains("on") ? 1 : 0));
    head.append(b);
    return b;
  };
  const knob = (parent, cfg) => {
    const k = Knob({ ...cfg, value: t ? t[cfg.key] : cfg.value });
    knobs(parent).append(k);
    return k;
  };

  // ---- input FX ----
  {
    const [c, h] = card("input FX");
    if (Number(CFG.loop?.monitor ?? 0) === 0) {
      c.append(el("div", "caption", "software monitor off — you hear your interface directly"));
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
      label: "comp thr", min: 0.05, max: 0.95, value: t ? t.compThr : 0.6,
      format: (v) => `${Math.round(v * 100)}%`,
      onInput: (v) => set(`t${i}.compThr`, v),
    }));
    k.append(Knob({
      label: "comp", min: 0.05, max: 1, value: t ? t.compRatio : 0.4,
      format: (v) => `${(1 / v).toFixed(1)}:1`,
      onInput: (v) => set(`t${i}.compRatio`, v),
    }));
    toggle(h, t && t.compOn, "compressor", `t${i}.compOn`);
    k.append(Knob({
      label: "rec level", min: 0, max: 1.5, value: t ? t.recGain : 1,
      format: (v) => `${Math.round(v * 100)}%`, onInput: (v) => set(`t${i}.recGain`, v),
    }));
  }

  // ---- output ----
  {
    const [c, h] = card("output");
    c.append(Segmented({
      options: [{ label: "A", value: 0 }, { label: "B", value: 1 }, { label: "C", value: 2 }],
      value: t ? t.route : 0,
      onPick: (v) => set(`t${i}.route`, v),
    }));
    toggle(h, t && t.cueOn, "cue", `t${i}.cueOn`);
    knobs(c).append(Knob({
      label: "cue send", min: 0, max: 1.2, value: t ? t.cueSend : 0.6,
      format: (v) => `${Math.round(v * 100)}%`,
      onInput: (v) => set(`t${i}.cueSend`, v),
    }));
  }

  // ---- gate ----
  {
    const [c, h] = card("noise gate");
    toggle(h, t && t.gateOn, t && t.gateOn ? "on" : "off", `t${i}.gateOn`);
    knob(c, {
      key: "gateThr", label: "threshold", min: 0.002, max: 0.4, value: 0.02, curve: 2,
      format: (v) => `${(20 * Math.log10(v)).toFixed(0)} dB`,
      onInput: (v) => set(`t${i}.gateThr`, v),
    });
    const auto = Button({ label: "arm auto-rec", toggle: true, cls: "tiny" });
    auto.addEventListener("pointerup", () =>
      set(`t${i}.autoArm`, auto.classList.contains("on") ? 1 : 0));
    c.append(auto);
  }

  // ---- filter ----
  {
    const [c, h] = card("filter");
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
    // step sequence: eight eighth-notes stepping the low pass upward. Tap a cell
    // to cycle it 0 -> 0.25 -> 0.5 -> 0.75 -> 1; the bar is its height.
    toggle(h, t && t.seqOn, "seq", `t${i}.seqOn`);
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
  }

  // ---- loop ----
  {
    const [c] = card("loop");
    c.append(Segmented({
      options: RATIOS, value: t ? t.loopRatio : 1,
      onPick: (v) => set(`t${i}.loopRatio`, v),
    }));
    // Speed is varispeed: 1 is grid-locked, anything else free-runs the track
    // at its own rate, so it re-pitches like the hardware's SPEED knob.
    c.append(Segmented({
      options: SPEEDS, value: t ? t.speed : 1,
      onPick: (v) => set(`t${i}.speed`, v),
    }));
    // MARK BACK drops straight to a bookmarked layer count instead of stepping
    // down one layer at a time; MARK sets the bookmark where you are now.
    const markRow = el("div", "row2");
    const mark = Button({ label: "mark", cls: "tiny" });
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
    const row = el("div", "row2");
    const rev = Button({ label: "reverse", toggle: true, cls: "tiny" });
    if (t && t.reverse) rev.classList.add("on");
    rev.addEventListener("pointerup", () =>
      set(`t${i}.reverse`, rev.classList.contains("on") ? 1 : 0));
    const fadeReset = Button({ label: "fades 0", cls: "tiny" });
    fadeReset.addEventListener("pointerup", () => {
      set(`t${i}.fadeIn`, 0);
      set(`t${i}.fadeOut`, 0);
    });
    row.append(rev, fadeReset);
    c.append(row);
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

  // ---- delay ----
  {
    const [c, h] = card("delay");
    toggle(h, t && t.delayOn, t && t.delayOn ? "on" : "off", `t${i}.delayOn`);
    const k = knobs(c);
    k.append(Knob({
      label: "time", min: 0.06, max: 2, value: t ? t.delayBeats : 0.75, curve: 1.8,
      format: (v) => `${v.toFixed(2)} bt`, onInput: (v) => set(`t${i}.delayBeats`, v),
    }));
    k.append(Knob({
      label: "decay", min: 0.05, max: 8, value: t ? t.delayDecay : 0.4, curve: 2,
      format: (v) => `${v.toFixed(2)} s`, onInput: (v) => set(`t${i}.delayDecay`, v),
    }));
    k.append(Knob({
      label: "mix", min: 0, max: 1, value: t ? t.delayMix : 0.25,
      format: (v) => `${Math.round(v * 100)}%`, onInput: (v) => set(`t${i}.delayMix`, v),
    }));
  }

  // ---- reverb send ----
  {
    const [c] = card("reverb");
    knob(c, {
      key: "revSend", label: "send", min: 0, max: 1, value: 0,
      format: (v) => `${Math.round(v * 100)}%`, onInput: (v) => set(`t${i}.revSend`, v),
    });
  }

  // ---- plugin slots ----
  {
    const [c] = card("plugin slots");
    const list = el("div", "slots");
    const csv = (VST || "").split(";")[i] || "";
    const slots = csv.split(",").map((x) => x.split("|"));
    for (let s = 0; s < (STATS.slots || 4); s++) {
      const row = el("div", "slot");
      const name = slots[s] && slots[s][1] ? slots[s][1] : "";
      const bypassed = slots[s] && String(slots[s][2]) === "1";
      if (name) row.classList.add("filled");
      const nm = el("span", "nm", name || `slot ${s + 1} — empty`);
      const acts = el("div", "acts");
      if (name) {
        const ed = Button({ label: "ui", cls: "tiny" });
        ed.addEventListener("pointerup", () => svc("vstEditor", [i, s]));
        const by = Button({ label: "byp", cls: "tiny", toggle: true });
        if (bypassed) by.classList.add("on");
        by.addEventListener("pointerup", () =>
          svc("vstBypass", [i, s, by.classList.contains("on") ? 1 : 0]));
        const rm = Button({ label: "x", cls: "tiny" });
        rm.addEventListener("pointerup", () => svc("vstClose", [i, s]));
        acts.append(ed, by, rm);
      } else {
        const pick = Select({
          options: [{ label: "load…", value: "" }].concat(
            (VSTCATALOG.length ? VSTCATALOG : ["(no plugins found)"]).map((n) => ({ label: n, value: n })),
          ),
          value: "",
          onPick: (v) => {
            if (v) svc("vstOpen", [i, s, v]);
          },
        });
        acts.append(pick);
      }
      row.append(nm, acts);
      list.append(row);
    }
    c.append(list);
    // Four FX banks: s1..s4 store the current slots, l1..l4 recall them.
    const bankRow = el("div", "row2");
    for (let b = 0; b < 4; b++) {
      const sv = Button({ label: `s${b + 1}`, cls: "tiny" });
      sv.addEventListener("pointerup", () => svc("vstBankSave", [i, b]));
      const ld = Button({ label: `l${b + 1}`, cls: "tiny" });
      ld.addEventListener("pointerup", () => svc("vstBankLoad", [i, b]));
      bankRow.append(sv, ld);
    }
    c.append(bankRow, el("div", "caption", "banks: s = store, l = recall"));
    if (STATS.vst !== 1) {
      c.append(el("div", "caption", "VSTPlugin extension not loaded"));
    } else if (!VSTCATALOG.length) {
      c.append(el("div", "caption", `scanning ${STATS.vstCount} plugins…`));
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
  // The hint belongs to the *label*, not to the control: if it trails the
  // control every row with a hint pushes its number left by the hint's width,
  // and the numbers stop forming a column.
  const left = el("div", "lblcol");
  left.append(el("span", "lbl", label));
  if (hint) left.append(el("span", "hint", hint));
  const wrap = el("div", "ctl");
  wrap.append(ctl);
  r.append(left, wrap);
  return r;
}

function rowStepper(label, path, { min, max, step, format, src = "cfg", hint }) {
  const value = Number(getPath(CFG, path) ?? min);
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

function rowSelect(label, path, options, { src = "cfg", hint } = {}) {
  const value = getPath(CFG, path) ?? "";
  const s = Select({
    options, value: String(value ?? ""),
    onPick: (v) => (src === "cfg" ? cfgSet(path, v) : set(`g.${path}`, v)),
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
  c.append(h);
  parent.append(c);
  return c;
}

function renderSettings() {
  const drawer = document.getElementById("settings");
  drawer.querySelector("h2").textContent = "setup";
  const tabs = drawer.querySelector(".tabs");
  tabs.replaceChildren();
  ["audio", "loop", "clock", "ui", "midi", "session", "505"].forEach((t) => {
    const b = Button({ label: t, cls: `tiny${t === settingsTab ? " on" : ""}` });
    b.dataset.tab = t;
    b.addEventListener("pointerup", () => {
      settingsTab = t;
      if (t === "audio") send({ op: "devices" });
      if (t === "midi") svc("midiMap");
      renderSettings();
    });
    tabs.append(b);
  });

  const body = drawer.querySelector(".body");
  body.replaceChildren();

  if (settingsTab === "audio") {
    const drv = String(CFG.audio?.driver ?? "auto");
    const dev = CFG.audio?.device ?? null;

    const c = card(body, "driver");
    c.append(rowSelect("driver", "audio.driver", [
      { label: "auto (jack if present)", value: "auto" },
      { label: "jack / pipewire", value: "jack" },
      { label: "alsa (direct)", value: "alsa" },
    ], { hint: "restart" }));
    c.append(el("div", "caption",
      drv === "alsa"
        ? "alsa talks to the hardware directly; nothing else can use it while running"
        : "scsynth joins the PipeWire graph as a node — there is no device to choose"));

    // A device is only meaningful for ALSA: it becomes scsynth's -H argument.
    if (drv === "alsa") {
      const opts = [{ label: "(system default)", value: "" }].concat(
        (ui.devices?.alsa || []).map((d) => ({
          label: `${d.id} — ${d.label}`, value: d.id,
        })),
      );
      c.append(rowSelect("alsa device", "audio.device", opts, { hint: "hw:N,M · restart" }));
    } else if (dev) {
      // never leave a PipeWire node name in audio.device: it would be handed to
      // ALSA on the next start and scsynth would die with "couldn't initialize audio"
      c.append(settingsRow("device", el("span", "num", String(dev))));
      c.append(rowButton("device", "clear", () => {
        cfgSet("audio.device", null);
        CFG.audio.device = null;
        toast("device cleared — restart to apply");
        renderSettings();
      }, "only ALSA uses this"));
    }

    // Under JACK/PipeWire scsynth follows the graph period; SC's own block size is
    // ignored. This is the real latency control.
    const qCard = card(body, "latency (pipewire)");
    const qsr = Number(CFG.audio?.sampleRate ?? 48000) || 48000;
    const qNow = Number(CFG.audio?.quantum ?? 0) || 0;
    const qOpts = [{ label: "leave alone", value: 0 }].concat(
      [64, 128, 256, 512, 1024].map((n) => ({
        label: `${n} frames · ${((n / qsr) * 1000).toFixed(1)} ms`, value: n,
      })),
    );
    qCard.append(rowSelect("quantum", "audio.quantum", qOpts, { hint: "restart the engine" }));
    qCard.append(el("div", "caption", qNow
      ? `period ${((qNow / qsr) * 1000).toFixed(1)} ms · round trip ≈ ${(((2 * qNow) / qsr) * 1000).toFixed(1)} ms`
      : "unforced — PipeWire decides (usually 1024 ≈ 21 ms)"));
    qCard.append(el("div", "caption",
      "one period of latency per buffer; lower it before blaming the looper"));

    const io = card(body, "engine io");
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

    // where the audio actually goes, read from the live graph (only meaningful
    // when scsynth is a JACK/PipeWire node)
    if (drv !== "alsa") {
    const g = card(body, "connections (pw-link)");
    const ins = ui.devices?.links?.inputs || [];
    const outs = ui.devices?.links?.outputs || [];
    g.append(el("div", "caption", `in  ← ${ins.length ? ins.join("  ·  ") : "nothing connected"}`));
    g.append(el("div", "caption", `out → ${outs.length ? outs.join("  ·  ") : "nothing connected"}`));
    g.append(el("div", "caption", "reroute in qpwgraph; then press refresh"));
    g.append(rowButton("graph", "refresh", () => send({ op: "devices" })));
    }

    const s = card(body, "engine");
    const info = el("div", "caption", "");
    info.id = "statsline";
    s.append(info);
    renderStats();
    s.append(
      rowButton("engine", "restart", () => {
        toast("restarting engine…");
        send({ op: "restart" });
      }, "re-reads config"),
      rowButton("latency", "measure", () => svc("stats"), "round trip estimate"),
    );
  }

  if (settingsTab === "loop") {
    const c = card(body, "looping");
    c.append(
      rowStepper("tracks", "tracks", { min: 1, max: 16, step: 1, hint: "restart" }),
      rowStepper("layers / track", "maxLayers", { min: 1, max: 4, step: 1, hint: "restart" }),
      rowStepper("max loop", "loop.maxSeconds", {
        min: 4, max: 120, step: 4, format: (v) => `${v}s`, hint: "restart",
      }),
      rowStepper("crossfade", "loop.crossfadeMs", {
        min: 0.1, max: 50, step: 0.1, format: (v) => `${v.toFixed(1)} ms`,
      }),
      rowStepper("record offset", "loop.recordOffset", {
        min: -4096, max: 4096, step: 8, format: (v) => `${v} smp`,
      }),
      // the readout must stay narrow enough for the value column, so the
      // explanation lives in the hint rather than in the number
      rowStepper("input monitor", "loop.monitor", {
        min: 0, max: 1.5, step: 0.1,
        format: (v) => (v === 0 ? "off" : `${Math.round(v * 100)}%`),
        hint: "0 = hear the loop only, never the dry input",
      }),
      rowToggle("auto-close 1st layer", "loop.autoClose"),
      rowToggle("continue into overdub", "loop.autoDub"),
      rowToggle("overdub replaces", "loop.overdubMode", {
        hint: "new take mutes what it covers; undo brings it back",
      }),
      rowToggle("quantize start", "loop.quantizeStart"),
      rowStepper("dub decay", "loop.dubDecay", {
        min: 0, max: 0.95, step: 0.05, format: (v) => `${Math.round(v * 100)}%`,
      }),
    );
    const m = card(body, "master chain");
    m.append(
      rowStepper("high pass", "masterHp", {
        min: 0, max: 2000, step: 10, src: "set", format: (v) => `${v} Hz`,
      }),
      rowStepper("low pass", "masterLp", {
        min: 1000, max: 19000, step: 250, src: "set", format: (v) => `${v} Hz`,
      }),
      rowStepper("reverb mix", "loop.revMix", {
        min: 0, max: 1, step: 0.05, format: (v) => `${Math.round(v * 100)}%`,
      }),
      rowStepper("room size", "loop.room", {
        min: 0, max: 1, step: 0.05, format: (v) => `${Math.round(v * 100)}%`,
      }),
    );
  }

  if (settingsTab === "clock") {
    const c = card(body, "metronome");
    c.append(
      rowStepper("click level", "clock.clickVol", {
        min: 0, max: 1, step: 0.05, format: (v) => `${Math.round(v * 100)}%`,
      }),
      rowStepper("count-in bars", "clock.countInBars", { min: 0, max: 8, step: 1 }),
      rowStepper("accent", "clock.accentHz", {
        min: 200, max: 4000, step: 20, format: (v) => `${v} Hz`, hint: "restart",
      }),
      rowStepper("click", "clock.clickHz", {
        min: 200, max: 4000, step: 20, format: (v) => `${v} Hz`, hint: "restart",
      }),
    );
  }

  if (settingsTab === "ui") {
    const c = card(body, "display");
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
    c.append(settingsRow("scale", scale), 
      rowToggle("show master bar", "ui.showMaster"),
      rowButton("display", "fullscreen", () => {
        if (document.fullscreenElement) document.exitFullscreen();
        else document.documentElement.requestFullscreen?.();
      }),
      rowButton("targets", "check 48px", () => {
        const r = auditTapTargets(48);
        toast(r.bad.length ? `${r.bad.length} too small` : `all ${r.total} targets ok`);
      }, "dev overlay (d)"),
    );
  }

  if (settingsTab === "midi") {
    const d = card(body, "input");
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
    d.append(rowButton("map", "default layout", () => svc("midiDefault", []),
      "20-27 press, 30-37 undo, 40-47 stop, 50-57 clear, 60 tap"));

    const c = card(body, "learn");
    const targets = [];
    for (let i = 0; i < (S ? S.tracks.length : CFG.tracks); i++) {
      ["press", "stop", "undo", "clear"].forEach((v) => targets.push(`t${i}.${v}`));
    }
    ["tap", "stopAll", "metro"].forEach((v) => targets.push(`g.${v}`));
    targets.forEach((tgt) => {
      const known = MIDIMAP.find((m) => m.endsWith(`|${tgt}`));
      const b = Button({ label: known ? known.split("|").slice(0, 2).join(" ") : "learn", cls: "tiny" });
      b.addEventListener("pointerup", () => {
        svc("midiLearn", [tgt]);
        toast(`move a control for ${tgt}`);
      });
      c.append(settingsRow(tgt, b));
    });
    const m = card(body, "map");
    m.append(
      rowButton("learned", "clear all", () => svc("midiClear", []), `${MIDIMAP.length} mapped`),
    );
    MIDIMAP.forEach((mapping) => {
      const [type, num, tgt] = mapping.split("|");
      m.append(settingsRow(tgt, el("span", "num", `${type} ${num}`)));
    });
  }

  if (settingsTab === "505") {
    // Muscle-memory map: Looper OSS is deliberately shaped like an RC-505 so the
    // gestures transfer. Everything here is already implemented; this is the index.
    const MAP = [
      ["TRACK 1-5 button", "the big pad on each column"],
      ["REC (start recording)", "tap the pad when empty"],
      ["REC again (close loop)", "tap the pad while recording"],
      ["DUB (overdub)", "tap the pad while playing"],
      ["PLAY", "tap the pad while stopped"],
      ["STOP", "STOP under the pad"],
      ["CLEAR", "hold CLEAR (0.8 s)"],
      ["UNDO / REDO", "UNDO / REDO (last layer only, depth = layers)"],
      ["MEASURE (loop length)", "BARS / LOOP in the top bar: 1 2 4 8"],
      ["TEMPO", "the tempo bar, or TAP"],
      ["RHYTHM level", "setup → clock → click level"],
      ["INPUT FX", "fx → input FX, gate, filter"],
      ["TRACK FX", "fx → delay, reverb send"],
      ["MASTER FX", "setup → loop → master chain"],
      ["SPEED CHANGE", "change BPM: layers re-pitch (varispeed)"],
      ["MEMORY (phrase save)", "setup → session → save / load slot"],
      ["LOOP SYNC off (per-track length)", "fx → loop → 1 / 1/2 / 1/4"],
    ];
    const c = card(body, "RC-505 → Looper OSS");
    // a reference table, not a settings row: two fixed columns so every
    // description starts at the same x, instead of a ragged right-aligned blob
    MAP.forEach(([a, b]) => {
      const row = el("div", "cfgrow map");
      row.append(el("span", "lbl", a), el("span", "desc", b));
      c.append(row);
    });
    const d = card(body, "deliberate differences");
    [
      "loop length comes from BARS + BPM, not from the first take (AUTO)",
      "up to 4 layers per track instead of one loop + undo",
      "no built-in drum rhythm patterns yet (metronome only)",
      "effects are stock UGens or your own VST3s, not the 49/53 Boss algorithms",
    ].forEach((t) => d.append(el("div", "caption", t)));
  }

  if (settingsTab === "session") {
    const c = card(body, "phrase memory");
    const slotStepper = Stepper({
      min: 1, max: 99, step: 1, value: Number(CFG.session?.slot ?? 1),
      onInput: (v) => {
        CFG.session = { ...(CFG.session || {}), slot: v };
        cfgSet("session.slot", v);
      },
    });
    c.append(settingsRow("slot", slotStepper));
    c.append(
      rowButton("save", "save slot", () => svc("save", [Number(CFG.session?.slot ?? 1)])),
      rowButton("load", "load slot", () => svc("load", [Number(CFG.session?.slot ?? 1)])),
      rowButton("export", "bounce 30 s", () => svc("export", [30])),
      rowButton("backup", "zip sessions", () => send({ op: "backup" }),
        "one file carries every slot"),
      rowButton("restore", "restore newest", () => send({ op: "restore" }),
        "overwrites sessions/ from the newest zip"),
      rowButton("bounce", "sum into track 1", () => {
        act("bounce", 0);
        toast("bouncing every track into track 1…");
      }, "one loop pass; the sources are cleared"),
    );
    const auto = card(body, "autosave");
    auto.append(
      rowToggle("autosave", "loop.autosave", { hint: "recovers the last session on start" }),
      rowButton("now", "clear autosave", () => {
        svc("clearAutosave", []);
        toast("autosave cleared");
      }, "after clearing tracks this is automatic"),
    );

    // WAV import: the browser cannot pick a server path, so files are dropped
    // into sessions/import/ and listed here.
    const imp = card(body, "import a wav");
    const impSel = Select({
      options: (IMPORTS.length ? IMPORTS : ["(none)"]).map((n) => ({ label: n, value: n })),
      value: IMPORTS[0] ?? "(none)",
      onPick: () => {},
    });
    const impBtn = Button({ label: "import", cls: "tiny" });
    impBtn.addEventListener("pointerup", () => {
      const name = impSel.value;
      if (name && name !== "(none)") {
        svc("import", [selected, name]);
        toast(`importing ${name} into track ${selected + 1}`);
      }
    });
    const impRow = el("div", "row2");
    impRow.append(impSel, impBtn);
    imp.append(impRow, el("div", "caption", "drop files in sessions/import/"));
    send({ op: "importList" });

    const info = card(body, "files");
    info.append(el("div", "caption", "saves to sessions/slotN (WAV per layer + session.json)"));
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

  // Engine phase is a beat-stepped target. We store it and let tick() ease
  // toward it: no dead reckoning, so with no new points the bar stands still.
  phase = { value: s.phase ?? 0, at: performance.now() };
  ui.master.inL?.set(s.inL ?? 0);
  ui.master.inR?.set(s.inR ?? 0);
  ui.master.out?.set(Math.max(s.masterL ?? 0, s.masterR ?? 0));

  ui.transport.bpmVal.textContent = Math.round(s.bpm);
  if (!ui.transport.bpmFader.classList.contains("active")) ui.transport.bpmFader.setValue(s.bpm);
  ui.transport.bars.setValue(s.bars);
  ui.transport.metro.classList.toggle("on", !!s.metro);
  ui.transport.countIn.classList.toggle("on", !!s.countIn);
  if (!ui.transport.masterFader.classList.contains("active")) {
    ui.transport.masterFader.setValue(s.masterVol);
    if (ui.buses) {
      [s.busA, s.busB, s.busC].forEach((v, k) => {
        if (!ui.buses[k].classList.contains("active")) ui.buses[k].setValue(v);
      });
    }
    ui.master.vol.setValue(s.masterVol);
  }

  s.tracks.forEach((t, i) => {
    const u = ui.tracks[i];
    if (!u) return;
    if (u.stateKey !== t.state) {
      u.stateKey = t.state;
      const [label, sub] = STATE_TEXT[t.state] || STATE_TEXT[0];
      u.pad.setLabel(label, sub);
      // classList, not className: the pad carries a stable "main" class too
      u.pad.classList.remove("rec", "play", "odub", "stopped");
      if (STATE_CLASS[t.state]) u.pad.classList.add(STATE_CLASS[t.state]);
    }
    const marks = t.undoDepth > t.layers ? ` · ${t.undoDepth - t.layers} undo` : "";
    // layer peak: tells you a HOT layer even while the track is stopped
    const hot = t.layerPeak > 0.6 ? " · HOT" : "";
    u.layers.textContent = `${t.layers}L${marks}${hot}`;
    const tag = inputTag(t.input);
    if (u.insrc.textContent !== tag) u.insrc.textContent = tag;
    if (!u.vol.classList.contains("active")) u.vol.setValue(t.vol);
    u.meter.set(t.meter);
    u.col.classList.toggle("selected", i === selected);
  });
}

/* ------------------------------------------------------------------- loop -- */
function tick() {
  if (S && ui.playheadFill) {
    // shortest way round the loop, then ease
    let d = (phase.value ?? 0) - playPos;
    d = ((d + 0.5) % 1) - 0.5;
    playPos = d > -0.0005 && d < 0.0005 ? phase.value : ((playPos + d * 0.14) % 1 + 1) % 1;
    ui.playheadFill.style.width = `${(playPos * 100).toFixed(2)}%`;
    if (ui.transport?.posVal) {
      const per = S.beatsPerBar || 4;
      const total = Math.max(1, (S.bars || 4) * per);
      const idx = Math.floor(playPos * total) % total;
      const text = `${Math.floor(idx / per) + 1}·${(idx % per) + 1}`;
      // only touch the DOM when the beat actually changes
      if (ui.transport.posVal.textContent !== text) ui.transport.posVal.textContent = text;
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
