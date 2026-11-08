#!/usr/bin/env node
// Documentation screenshots, with Playwright.
//
//   mkdir -p /tmp/uitest && cd /tmp/uitest && npm i playwright
//   PLAYWRIGHT_DIR=/tmp/uitest/node_modules node scripts/shots.mjs
//
// The UI must be up first: `LOOPER_OSS_KEEP_ALIVE=1 python3 -m app.main --window none`.
//
// This replaces the CDP version in shots.py for this machine: raw DevTools
// screenshots of the drawer came out with garbled glyphs, because text inside a
// composited layer is mis-rasterised there. Playwright drives the same Chromium
// but through its own launch flags and produces correct text.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const base = process.env.PLAYWRIGHT_DIR || "/tmp/uitest/node_modules";
const require = createRequire(`${base}/`);
const { chromium } = require("playwright");

const URL = process.env.UI_URL || "http://127.0.0.1:8181/";
const OUT = path.resolve("docs/img");
const TABS = ["audio", "loop", "clock", "ui", "midi", "session", "505"];

fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
page.on("pageerror", (e) => console.error(`page error: ${e.message}`));

await page.goto(URL, { waitUntil: "networkidle" });
await page.waitForSelector(".track .pad.main", { timeout: 20000 });
await page.waitForFunction(() => window.__ld?.state?.() != null, { timeout: 20000 });

// silent: the shots must not depend on a live microphone
await page.evaluate(() => {
  window.__ld.set("g.masterVol", 0);
  window.__ld.set("g.metro", 0);
});
await page.waitForTimeout(800);

const shot = async (name) => {
  await page.screenshot({ path: path.join(OUT, name) });
  console.log(`  docs/img/${name}`);
};

await shot("ui-main.png");

await page.locator("#transport button", { hasText: "setup" }).click();
await page.waitForTimeout(500);
for (const tab of TABS) {
  await page.locator(`#settings .tabs button[data-tab="${tab}"]`).click();
  await page.waitForTimeout(400);
  await shot(`setup-${tab}.png`);
}
await page.locator("#settings-close").click();
await page.waitForTimeout(400);

await page.locator(".track").nth(0).getByRole("button", { name: "fx" }).click();
await page.waitForTimeout(500);
await shot("ui-fx.png");
await page.locator("#fx-close").click();

// the shots must not leave the app scaled or the autosave dirty
await page.evaluate(() => window.__ld.cfg("ui.scale", 1));
await page.waitForTimeout(300);

await browser.close();
console.log("\nscreenshots written to docs/img/");
