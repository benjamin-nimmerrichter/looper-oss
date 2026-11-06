// Touch-first primitives. Everything is pointer-driven: no hover, no
// double-tap zoom, no reliance on native input widgets (their thumbs are
// too small on an 8" panel). Every control is at least --tap tall (48px).

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

// One OSC send per animation frame at most: pointermove fires ~120x/s, and every
// send becomes a synth.set on the engine.
let queuedSend = null;
let queuedRaf = 0;
function coalesce(fn) {
  queuedSend = fn;
  if (!queuedRaf) {
    queuedRaf = requestAnimationFrame(() => {
      queuedRaf = 0;
      const f = queuedSend;
      queuedSend = null;
      if (f) f();
    });
  }
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

/** Horizontal fader. Drag anywhere on the track, or tap to jump. */
export function Fader({ min = 0, max = 1, value = 0, format = (v) => v.toFixed(2), onInput }) {
  const root = el("div", "fader");
  const fill = el("div", "fill");
  const thumb = el("div", "thumb");
  const read = el("div", "read");
  root.append(fill, thumb, read);

  let v = clamp(value, min, max);
  const draw = () => {
    const p = (v - min) / (max - min);
    fill.style.width = `${p * 100}%`;
    thumb.style.left = `${p * 100}%`;
    read.textContent = format(v);
  };
  const set = (nv, fire = true) => {
    v = clamp(nv, min, max);
    draw();
    if (fire && onInput) coalesce(() => onInput(v));
  };

  const fromEvent = (e) => {
    const r = root.getBoundingClientRect();
    return min + ((clamp(e.clientX - r.left, 0, r.width)) / r.width) * (max - min);
  };

  root.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    root.setPointerCapture(e.pointerId);
    root.classList.add("active");
    set(fromEvent(e));
  });
  root.addEventListener("pointermove", (e) => {
    if (root.hasPointerCapture(e.pointerId)) set(fromEvent(e));
  });
  const end = (e) => {
    if (root.hasPointerCapture(e.pointerId)) root.releasePointerCapture(e.pointerId);
    root.classList.remove("active");
  };
  root.addEventListener("pointerup", end);
  root.addEventListener("pointercancel", end);

  draw();
  root.setValue = (nv) => set(nv, false);
  return root;
}

/** Rotary knob: vertical drag, 270 degree sweep. */
export function Knob({ min = 0, max = 1, value = 0, label = "", format = (v) => v.toFixed(2), onInput, curve = 1 }) {
  // curve > 1 gives finer control at the low end (frequencies, thresholds)
  const root = el("div", "knobwrap");
  const knob = el("div", "knob");
  const ring = el("div", "ring");
  const dot = el("div", "dot");
  knob.append(ring, dot);
  const cap = el("div", "knoblabel", label);
  const read = el("div", "read");
  root.append(knob, cap, read);

  let v = clamp(value, min, max);
  const toNorm = (x) => Math.pow(clamp((x - min) / (max - min), 0, 1), 1 / curve);
  const fromNorm = (p) => min + (max - min) * Math.pow(clamp(p, 0, 1), curve);
  const draw = () => {
    const p = toNorm(v);
    root.style.setProperty("--p", p);
    dot.style.transform = `rotate(${-135 + p * 270}deg)`;
    read.textContent = format(v);
  };
  const set = (nv, fire = true) => {
    v = clamp(nv, min, max);
    draw();
    if (fire && onInput) coalesce(() => onInput(v));
  };

  let startY = 0;
  let startP = 0;
  knob.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    knob.setPointerCapture(e.pointerId);
    startY = e.clientY;
    startP = toNorm(v);
    root.classList.add("active");
  });
  knob.addEventListener("pointermove", (e) => {
    if (!knob.hasPointerCapture(e.pointerId)) return;
    set(fromNorm(startP + (startY - e.clientY) / 150));
  });
  const end = (e) => {
    if (knob.hasPointerCapture(e.pointerId)) knob.releasePointerCapture(e.pointerId);
    root.classList.remove("active");
  };
  knob.addEventListener("pointerup", end);
  knob.addEventListener("pointercancel", end);

  draw();
  root.setValue = (nv) => set(nv, false);
  return root;
}

/**
 * Pad button. `hold` (ms) makes it a long-press action instead of a tap,
 * which is how destructive things (clear) are guarded on a touchscreen.
 */
export function Pad({ label, sub = "", onTap, onHold, hold = 0, cls = "" }) {
  const root = el("button", `pad ${cls}`);
  const main = el("span", "pad-main", label);
  const sec = el("span", "pad-sub", sub);
  const prog = el("span", "pad-prog");
  root.append(main, sec, prog);
  root.type = "button";

  let timer = null;
  let held = false;

  const cancel = () => {
    if (timer) clearTimeout(timer);
    timer = null;
    prog.style.transition = "none";
    prog.style.width = "0%";
  };

  root.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    held = false;
    if (onHold && hold) {
      prog.style.transition = "none";
      prog.style.width = "0%";
      requestAnimationFrame(() => {
        prog.style.transition = `width ${hold}ms linear`;
        prog.style.width = "100%";
      });
      timer = setTimeout(() => {
        held = true;
        timer = null;
        onHold();
      }, hold);
    }
  });
  root.addEventListener("pointerup", (e) => {
    e.preventDefault();
    if (timer) {
      cancel();
      if (onTap) onTap();
    } else if (!held && onTap) {
      onTap();
    }
  });
  root.addEventListener("pointercancel", cancel);
  root.addEventListener("pointerleave", () => {
    if (timer) cancel();
  });

  root.setLabel = (a, b) => {
    main.textContent = a;
    if (b != null) sec.textContent = b;
  };
  root.setHold = (ms) => {
    hold = ms;
  };
  return root;
}

export function Button({ label, onTap, cls = "", toggle = false }) {
  const root = el("button", `btn ${cls}`, label);
  root.type = "button";
  root.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    if (toggle) root.classList.toggle("on");
  });
  root.addEventListener("pointerup", (e) => {
    e.preventDefault();
    if (onTap) onTap();
  });
  return root;
}

export function Segmented({ options, value, onPick, cls = "" }) {
  const root = el("div", `seg ${cls}`);
  const btns = options.map((o) => {
    const b = el("button", "seg-btn", o.label);
    b.type = "button";
    b.dataset.v = o.value;
    b.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      onPick(o.value);
      root.setValue(o.value);
    });
    root.append(b);
    return b;
  });
  root.setValue = (v) =>
    btns.forEach((b) => b.classList.toggle("on", String(b.dataset.v) === String(v)));
  root.setValue(value);
  return root;
}

export function Meter(vertical = false) {
  const root = el("div", `meter${vertical ? " v" : ""}`);
  const l = el("div", "meter-bar");
  root.append(l);
  root.set = (v) => {
    l.style.width = vertical ? "100%" : `${clamp(v * 100 * 1.6, 0, 100)}%`;
    l.style.height = vertical ? `${clamp(v * 100 * 1.6, 0, 100)}%` : "100%";
    l.style.background = v > 0.75 ? "#e05a4f" : v > 0.45 ? "#e8a33d" : "#57b65f";
  };
  return root;
}

/** − value + : the safest input for a finger. */
export function Stepper({ min = 0, max = 100, step = 1, value = 0, format = (v) => String(v), onInput }) {
  const root = el("div", "seg");
  const minus = el("button", "seg-btn", "\u2212");
  const read = el("div", "num");
  const plus = el("button", "seg-btn", "+");
  minus.type = plus.type = "button";
  let v = clamp(value, min, max);
  const draw = () => {
    read.textContent = format(v);
    minus.disabled = v <= min;
    plus.disabled = v >= max;
  };
  const set = (nv, fire = true) => {
    v = clamp(Number((nv / step).toFixed(0)) * step, min, max);
    draw();
    if (fire && onInput) coalesce(() => onInput(v));
  };
  minus.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    set(v - step);
  });
  plus.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    set(v + step);
  });
  root.append(minus, read, plus);
  draw();
  root.setValue = (nv) => set(nv, false);
  return root;
}

/** Native select is the right control for a long device list on touch. */
export function Select({ options, value, onPick }) {
  const root = el("select", "sel");
  const fill = (opts, val) => {
    root.replaceChildren();
    opts.forEach((o) => {
      const opt = el("option", null, o.label);
      opt.value = String(o.value);
      root.append(opt);
    });
    if (val != null) root.value = String(val);
  };
  fill(options, value);
  root.addEventListener("change", () => onPick(root.value));
  root.setOptions = (opts, val) => fill(opts, val);
  return root;
}

/** Outline every interactive element and report the ones under `min` px. */
export function auditTapTargets(min = 48) {
  const layer = document.getElementById("devoverlay");
  const badge = document.getElementById("devbadge");
  if (!layer) return { total: 0, bad: [] };
  layer.replaceChildren();
  const sel = "button, select, .fader, .knob, .pad, .seg-btn, .btn";
  const nodes = Array.from(document.querySelectorAll(sel)).filter((n) => {
    const r = n.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
  const bad = [];
  nodes.forEach((n) => {
    const r = n.getBoundingClientRect();
    const small = r.height < min - 0.5 || r.width < min - 0.5;
    const box = document.createElement("div");
    box.className = `box${small ? " bad" : ""}`;
    box.style.left = `${r.left}px`;
    box.style.top = `${r.top}px`;
    box.style.width = `${r.width}px`;
    box.style.height = `${r.height}px`;
    layer.append(box);
    if (small) {
      bad.push({
        cls: n.className,
        text: (n.textContent || "").slice(0, 18),
        w: Math.round(r.width),
        h: Math.round(r.height),
      });
    }
  });
  if (badge) {
    badge.classList.add("on");
    badge.classList.toggle("bad", bad.length > 0);
    badge.textContent = bad.length
      ? `${bad.length}/${nodes.length} targets under ${min}px`
      : `${nodes.length} targets ok (\u2265${min}px)`;
  }
  return { total: nodes.length, bad };
}
