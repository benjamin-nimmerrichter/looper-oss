#!/usr/bin/env node
// Looper OSS UI verification: the 48px touch-target floor, layout fit at the
// real screen sizes, and that the settings drawer actually drives the engine.
//
// Needs playwright (kept out of the repo):
//   mkdir -p /tmp/uitest && cd /tmp/uitest && npm i playwright && npx playwright install chromium
//   PLAYWRIGHT_DIR=/tmp/uitest/node_modules ./scripts/uicheck.mjs
//
// Screenshots land in /tmp/looper-oss-ui/.
import { createRequire } from "node:module";
import fs from "node:fs";

const base = process.env.PLAYWRIGHT_DIR || "/tmp/uitest/node_modules";
const require = createRequire(`${base}/`);
const { chromium } = require("playwright");

const RAW_URL = process.env.UI_URL || "http://127.0.0.1:8181/";
const URL = (() => {
  const u = new globalThis.URL(RAW_URL);
  if (u.protocol !== "http:" && u.protocol !== "https:") throw new Error("bad UI_URL");
  return u.toString();
})();
const OUT = "/tmp/looper-oss-ui";
const SIZES = [
  { w: 1280, h: 800, name: "8inch" },
  { w: 1024, h: 600, name: "small" },
  { w: 1920, h: 1080, name: "fhd" },
  { w: 800, h: 1280, name: "portrait" },
];

const problems = [];
const note = (ok, msg) => {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${msg}`);
  if (!ok) problems.push(msg);
};

fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
page.on("pageerror", (e) => problems.push(`page error: ${e.message}`));

await page.goto(URL, { waitUntil: "networkidle" });
await page.waitForSelector(".track .pad.main", { timeout: 15000 });
await page.waitForFunction(() => window.__ld?.state?.() != null, { timeout: 15000 });
// silent run: this check taps pads, which can monitor a live mic
await page.evaluate(() => {
  window.__ld.set("g.masterVol", 0);
  window.__ld.set("g.metro", 0);
});

// ---------------------------------------------------------------- layouts --
for (const s of SIZES) {
  await page.setViewportSize({ width: s.w, height: s.h });
  await page.waitForTimeout(400);
  const fit = await page.evaluate(() => ({
    sw: document.documentElement.scrollWidth,
    cw: document.documentElement.clientWidth,
    sh: document.documentElement.scrollHeight,
    ch: document.documentElement.clientHeight,
    tracks: document.querySelectorAll(".track").length,
    trackH: Math.round(document.querySelector(".track")?.getBoundingClientRect().height ?? 0),
  }));
  note(fit.sw <= fit.cw + 1 && fit.sh <= fit.ch + 1,
    `${s.name} ${s.w}x${s.h}: no overflow (${fit.sw}x${fit.sh} in ${fit.cw}x${fit.ch})`);
  note(fit.tracks === 5 && fit.trackH > 200,
    `${s.name}: 5 track columns, ${fit.trackH}px tall`);
  await page.screenshot({ path: `${OUT}/${s.name}-${s.w}x${s.h}.png` });
}

// ------------------------------------------------------------ tap targets --
await page.setViewportSize({ width: 1280, height: 800 });
await page.waitForTimeout(300);
const audit = await page.evaluate(() => window.__ld.audit(48));
note(audit.bad.length === 0,
  `main view: ${audit.total} interactive targets, all >= 48px` +
  (audit.bad.length ? ` — too small: ${JSON.stringify(audit.bad.slice(0, 5))}` : ""));

// -------------------------------------------------------------- behaviour --
const before = await page.evaluate(() => window.__ld.state().tracks[0].state);
await page.locator(".track").nth(0).locator(".pad.main").click();
await page.waitForTimeout(1200);
const after = await page.evaluate(() => window.__ld.state().tracks[0].state);
note(after !== before, `tapping a pad changes track state (${before} -> ${after})`);

// -------- settings drawer: open, switch tab, drag the scale fader ----------
await page.locator("#transport button", { hasText: "setup" }).click();
await page.waitForTimeout(400);
note(await page.locator("#settings.open").count() === 1, "settings drawer opens");

const auditSettingsAudio = await page.evaluate(() => window.__ld.audit(48));
note(auditSettingsAudio.bad.length === 0,
  `settings/audio: ${auditSettingsAudio.total} targets >= 48px` +
  (auditSettingsAudio.bad.length ? ` — ${JSON.stringify(auditSettingsAudio.bad.slice(0, 4))}` : ""));

await page.locator('#settings .tabs button[data-tab="ui"]').click();
await page.waitForTimeout(400);
const scaleFader = page.locator('#settings .cfgrow:has-text("scale") .fader');
const box = await scaleFader.boundingBox();
if (box) {
  // a finger tap at 75% of the bar; mouse drags are unreliable in headless
  await scaleFader.dispatchEvent("pointerdown", {
    pointerId: 1,
    clientX: box.x + box.width * 0.75,
    clientY: box.y + box.height / 2,
    bubbles: true,
  });
  await scaleFader.dispatchEvent("pointerup", { pointerId: 1, bubbles: true });
}
await page.waitForTimeout(600);
const scale = await page.evaluate(() =>
  Number(getComputedStyle(document.documentElement).getPropertyValue("--scale")));
note(scale > 1.05, `scale fader changes the UI scale (--scale = ${scale.toFixed(2)})`);

const auditScaled = await page.evaluate(() => window.__ld.audit(48));
note(auditScaled.bad.length === 0,
  `scaled view: ${auditScaled.total} targets >= 48px` +
  (auditScaled.bad.length ? ` — ${JSON.stringify(auditScaled.bad.slice(0, 6))}` : ""));
await page.screenshot({ path: `${OUT}/scaled.png` });

// scale must survive a reload (config persistence through the bridge)
await page.reload({ waitUntil: "networkidle" });
await page.waitForFunction(() => window.__ld?.state?.() != null, { timeout: 15000 });
const scaleAfterReload = await page.evaluate(() =>
  Number(getComputedStyle(document.documentElement).getPropertyValue("--scale")));
note(Math.abs(scaleAfterReload - scale) < 0.06,
  `scale persisted across reload (${scaleAfterReload.toFixed(2)})`);

// put it back so the repo config stays at 1.0
await page.evaluate(() => {
  window.__ld.cfg("ui.scale", 1);
  document.documentElement.style.setProperty("--scale", "1");
});
await page.waitForTimeout(400);
await page.keyboard.press("Escape");

// -------------------------------------------------------------- fx drawer --
await page.locator(".track").nth(0).getByRole("button", { name: "fx" }).click();
await page.waitForTimeout(500);
const auditFx = await page.evaluate(() => window.__ld.audit(48));
note(auditFx.bad.length === 0, `fx drawer: ${auditFx.total} targets >= 48px` +
  (auditFx.bad.length ? ` — ${JSON.stringify(auditFx.bad.slice(0, 4))}` : ""));
await page.screenshot({ path: `${OUT}/fx-drawer.png` });

await browser.close();

console.log(problems.length ? `\n${problems.length} UI problems` : "\nUI OK");
process.exit(problems.length ? 1 : 0);
