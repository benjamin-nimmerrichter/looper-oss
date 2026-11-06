# Acknowledgements

Looper OSS is deliberately a thin skin. Everything that makes sound was written
by someone else, years earlier, and given away. This file is the thank-you.

## SuperCollider

**James McCartney**, and the long list of people who have kept the project alive
since: thank you for the engine this whole thing stands on.

The README makes a claim — *no hand-written DSP* — and that claim is only
possible because scsynth already had `BufWr`/`BufRd` for the layer buffers,
`Phasor` for the loop clock, `Amplitude` for the meters, `FreeVerb2` for the
reverb send, `Compander` for the gate, `LagUD` for every smoothed control, and
`RecordBuf` for the takes. The per-track state machine in `engine/looper.scd` is
about 1,200 lines and roughly none of it is a signal path; the signal path is
yours.

Special thanks for `Server:sync`, `SendReply` and `BufWr.kr` being documented
well enough that a metering design could be rebuilt from the help files after
three wrong attempts.

## sc3-plugins

The community plugin collection supplies the biquads behind **fx → filter**:
`BLowPass` and `BHiPass`. Without them the per-track filter would have been a
second-order hand-rolled thing, or absent.

## VSTPlugin — Christof Ressi (IEM Graz)

The optional plugin slots — load, editor window, parameter list, and the opaque
state blob that `sessions/…/vst_*.bin` round-trips — are entirely `VSTPlugin`.
It is BSD-3-Clause and it just works, including the part where a missing
extension must not abort the block that mentions it.

## Python side

- **python-osc** (Attwad and contributors) — OSC over UDP in both directions,
  including the `AsyncIOOSCUDPServer` the bridge runs in its event loop.
- **websockets** (Aymeric Augustin and contributors) — the browser link, and the
  reason the UI needs no build step and no framework.

## PipeWire — Wim Taymans

Low-latency audio on Linux without a JACK daemon to babysit. `pipewire-jack`
makes scsynth an ordinary graph client, which is what lets the whole thing run
from a shell script on a CachyOS box.

## Inspiration and reference designs

- **Boss RC-505 / RC-505 mkII** — the gesture vocabulary this project is
  deliberately shaped like, so muscle memory transfers to the hardware.
- **SooperLooper** — the reference for threshold record, multiply, reverse and
  rate; `FEATURES.md` measures itself against it.

## Test and tooling

- **Playwright** (Microsoft) — `scripts/uicheck.mjs` proves the 48 px touch-target
  floor and the layout fit at every panel size, and takes the screenshots.

## Package maintainers

Everyone packaging SuperCollider, sc3-plugins and PipeWire for Arch, Debian,
Fedora and the AUR. This project installs nothing itself, which is only pleasant
because those packages are correct.

---

If you maintain one of the projects above and would like the wording here
changed, that is a one-line pull request and it will be merged.
