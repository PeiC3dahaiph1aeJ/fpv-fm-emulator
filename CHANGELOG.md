# Changelog

Versions follow [semantic versioning](https://semver.org): `MAJOR.MINOR.PATCH`.
While the number starts with `0.`, the tool is still a bench instrument under
active development — the minor number moves whenever behaviour on air, or the
meaning of a setting, changes.

The version lives in one place, `fpv_emulator/__init__.py`. **Bump it together
with the entry below** — a test fails if the two disagree, because the point of a
version is to answer "what exactly do you have running?" from a screenshot or a
pasted log, and a number that lags is worse than none.

Shown in the window title and the first line of the event log, in the startup
error dialog, at the top of `probe`, and via `--version`.

---

## 0.2.0 — 2026-08-10

Everything between the first working version and today. There were no releases in
between, so this is one entry rather than invented history.

### Added
- **Colour video.** `color_bars` / `color_bars100` with a 4.43 MHz subcarrier
  (PAL) / 3.58 MHz (NTSC), burst and U/V chroma, including PAL's line-by-line V
  phase alternation. This is what a detector matching on the modulation profile
  actually recognises — a narrow B/W signal gives a high RSSI and is not seen.
- **PAL50 / NTSC60** progressive standards, 50/60 Hz fields. The old `PAL`/`NTSC`
  names map onto them. The field rate is what removes the vertical roll on the
  detector's monitor.
- **SoapySDR backend** — HackRF, LimeSDR, BladeRF, USRP and others, with the
  power slider mapped onto each device's own gain range.
- **Firmware profiles** (`auto` / `adi` / `tezuka`): which ways of setting the
  sample rate may be tried. `auto` asks the board and falls back, and is right for
  every board tested.
- **Support for boards whose IIO device names differ** (Tezuka builds, PlutoSky,
  custom FPGA images) — the control and DMA devices are found by shape rather than
  by the names pyadi hard-codes. An image with no transmit path is now named as
  such instead of failing with `argument of type 'NoneType' is not iterable`.
- **The window reopens where it was left** — every setting is saved on close and
  on Start, and restored next launch.
- **Bilingual interface**, English and Ukrainian, in the GUI and the CLI.
- **One-click Windows launchers** (`setup.bat`, `run_gui.bat`) and a GUI-first
  README; MIT licence.

### Changed
- **Two-drone spacing is now 18 MHz** (±9). Below ~14 MHz the carriers merge and
  the detector reports one target instead of two.
- **An out-of-range sample rate is refused, not clamped.** The buffer is generated
  for the rate that was asked for and the 15.625 kHz line rate scales with it, so a
  substituted rate would radiate a carrier that looks correct on a spectrum
  analyser and that the detector cannot see.
- **Power defaults are lower.** On 1.2 GHz, −30 dB measured better than −10 dB:
  the compressed PA puts video on its harmonics and the detector can lock onto one
  of those while the fundamental shows a black screen.
- Channel names are qualified as `BAND:CHANNEL`. Seven bare `C1`…`C7` names existed
  in four bands, and a sweep could transmit on 3440 MHz while reporting 1200.

### Fixed
- **A transmitter that reported success and stayed silent.** `tx()` returns without
  error while the DMA never starts. The buffer is now verified through digital
  loopback, and a stale context is dropped and reopened before retrying.
- **A device that rejects a setting now says which one**, and quotes the range it
  publishes, instead of one `[Errno 22] Invalid argument` for four different causes.
- A Tezuka build refusing pyadi's interpolating FIR filter at 20 MSPS — the rate is
  set with the filter off instead, and the difference is stated.
- `probe` reports the real traceback rather than a bare "not found", and opens the
  device the same way the transmitter does, so it cannot succeed where `tx` fails.

---

## 0.1.0 — 2026-07-25

First working version: composite video, FM modulation, cyclic buffer on the Pluto,
band/channel tables, the scenario engine (static, sweep with pause, power ramp,
multi-drone), the CLI and the PySide6 GUI.
