#!/usr/bin/env python3
"""Regenerate the documentation screenshots without Playwright.

uicheck.mjs is the real UI test, but it needs a Playwright install. The
documentation still has to be re-shot every time the UI changes, so this drives
the same Chromium that Playwright cached, over the DevTools protocol, using the
`websockets` client the bridge already depends on.

    python3 -m app.main --window none &        # the stack the page talks to
    python3 scripts/shots.py                   # docs/img/*.png
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"
URL = os.environ.get("UI_URL", "http://127.0.0.1:8181/")
TABS = ("audio", "loop", "clock", "ui", "midi", "session", "505")
try:
    PORT = int(os.environ.get("CDP_PORT", "9333"))
except ValueError:
    PORT = 9333
SIZE = (1280, 800)


def find_chrome() -> str | None:
    for name in ("chrome-headless-shell", "chromium", "chromium-browser",
                 "google-chrome-stable", "brave"):
        found = shutil.which(name)
        if found:
            return found
    cache = pathlib.Path.home() / ".cache" / "ms-playwright"
    for shell in sorted(cache.glob("chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")):
        if shell.is_file():
            return str(shell)
    return None


class CDP:
    """One page, request/response by id, five methods is all this needs."""

    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    async def call(self, method: str, **params):
        self.n += 1
        mid = self.n
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            raw = await self.ws.recv()
            try:
                msg = json.loads(raw)
            except ValueError:          # an event we do not care about
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expression: str, await_promise: bool = False):
        res = await self.call("Runtime.evaluate", expression=expression,
                              returnByValue=True, awaitPromise=await_promise)
        return res.get("result", {}).get("value")

    async def shot(self, path: pathlib.Path) -> None:
        res = await self.call("Page.captureScreenshot", format="png")
        path.write_bytes(base64.b64decode(res["data"]))
        print(f"  {path.relative_to(ROOT)}  {path.stat().st_size // 1024} kB")


async def wait_for(cdp: CDP, expression: str, seconds: float = 30.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if await cdp.js(expression):
            return
        await asyncio.sleep(0.3)
    raise TimeoutError(f"timed out waiting for: {expression}")


async def shoot(cdp: CDP) -> None:
    tap = ("el => el.dispatchEvent(new PointerEvent('pointerup', "
           "{bubbles: true, cancelable: true}))")
    await wait_for(cdp, "!!(window.__ld && window.__ld.state && window.__ld.state())")

    # silent: the screenshots must not depend on a live microphone
    await cdp.js("window.__ld.set('g.masterVol', 0); window.__ld.set('g.metro', 0); true")
    # Chromium's software rasteriser renders text inside a transformed layer with
    # a broken font atlas, and the drawers slide in with `transform`. The result
    # is a screenshot full of garbage glyphs. Drop the slide (it is a 0.22 s
    # animation, invisible in a still) and hide the closed drawers so only the
    # one being photographed is painted.
    await cdp.js(
        "document.head.insertAdjacentHTML('beforeend', '<style>'"
        " + '.drawer.open{transform:none!important;transition:none!important}'"
        " + '.drawer:not(.open){display:none!important}'"
        " + '.drawer .tabs,.drawer .body{overflow:visible!important}'"
        " + '.cfgrow .lbl,.cfgrow .hint{overflow:visible!important;text-overflow:clip!important}'"
        " + '</style>');true")
    await asyncio.sleep(0.8)

    OUT.mkdir(parents=True, exist_ok=True)
    print("main screen")
    await cdp.shot(OUT / "ui-main.png")
    if os.environ.get("SHOTS_ONLY") == "main":
        return   # the drawers cannot be rasterised here; see the docstring


    print("settings tabs")
    await cdp.js(f"({tap})([...document.querySelectorAll('#transport button')]"
                 f".find(b => b.textContent.trim().toLowerCase() === 'setup')); true")
    await asyncio.sleep(0.6)
    # The drawer is `position: fixed` in the app (it slides over the tracks). A
    # fixed, transformed, full-screen layer is exactly what this rasteriser
    # mangles, so for the stills it is laid out in normal flow and the app is
    # hidden behind it -- same pixels, ordinary stacking.
    for tab in TABS:
        await cdp.js(f"({tap})(document.querySelector('#settings .tabs button[data-tab=\"{tab}\"]')); true")
        await asyncio.sleep(0.5)
        await cdp.shot(OUT / f"setup-{tab}.png")
    await cdp.js(f"({tap})(document.getElementById('settings-close')); true")
    await asyncio.sleep(0.5)

    print("fx drawer")
    await cdp.js(
        "(() => { const t = document.querySelector('.track');"
        " const b = [...t.querySelectorAll('button')].find(x => x.textContent.trim() === 'fx');"
        f" if (b) ({tap})(b); return !!b; }})()")
    await asyncio.sleep(0.6)
    await cdp.shot(OUT / "ui-fx.png")
    await cdp.js(f"({tap})(document.getElementById('fx-close')); true")


async def main() -> int:
    chrome = find_chrome()
    if not chrome:
        print("no Chromium found (chrome-headless-shell, chromium, brave)", file=sys.stderr)
        return 1
    import websockets  # noqa: PLC0415 - optional dependency of this tool only

    profile = pathlib.Path(tempfile.mkdtemp(prefix="looper-shots-"))
    proc = subprocess.Popen(
        [chrome, "--headless=new", "--no-sandbox", "--hide-scrollbars",
         # a real (software) GL path: --disable-gpu makes Chromium rasterise
         # transformed layers with a broken font atlas
         "--enable-unsafe-swiftshader", "--use-gl=angle", "--use-angle=swiftshader",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         f"--window-size={SIZE[0]},{SIZE[1]}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        target = None
        for _ in range(100):
            try:
                import urllib.request
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=1) as fh:
                    for t in json.load(fh):
                        if t.get("type") == "page":
                            target = t["webSocketDebuggerUrl"]
                            break
                if target:
                    break
            except OSError:
                pass
            await asyncio.sleep(0.2)
        if not target:
            print("Chromium did not expose a debugging target", file=sys.stderr)
            return 1

        async with websockets.connect(target, max_size=64 * 1024 * 1024) as ws:
            cdp = CDP(ws)
            await cdp.call("Page.enable")
            await cdp.call("Runtime.enable")
            await cdp.call("Emulation.setDeviceMetricsOverride",
                           width=SIZE[0], height=SIZE[1], deviceScaleFactor=1,
                           mobile=False)
            await cdp.call("Page.navigate", url=URL)
            await asyncio.sleep(1.0)
            await shoot(cdp)
        print("\nscreenshots written to docs/img/")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        # a leftover browser profile in /tmp is harmless
        with contextlib.suppress(OSError):
            shutil.rmtree(profile)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
