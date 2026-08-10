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

## 0.4.0 — 2026-08-10

### Fixed
- **The shipped scenarios transmitted a signal no detector was going to recognise.**
  `static_R1`, `power_ramp_R4` and `sweep_raceband` ran at **8 MSPS** with a
  monochrome pattern, and `sweep_allbands` at 10 MSPS with `multiburst`. All four
  date from before the colour work and were never revisited, so on the bench they
  showed no picture while manual mode was fine. Every scenario now uses the profile
  that is actually recognised: `color_bars`, at least 20 MSPS, 7 MHz deviation.
  Measured: one carrier is 11.9 MHz wide at −30 dB, and at 30.72 MSPS the two-drone
  case still folds only 0.49 % past 0.45·fs with the carriers 66 dB apart.

### Changed
- The default deviation is 7 MHz, in the GUI and for a scenario that omits it. It
  was 6.

### Added
- A scenario below 20 MSPS now warns and says why — the colour subcarrier aliases
  and the occupied bandwidth is too narrow, so the carrier reads as strong and is
  not recognised as video. A narrow signal is not an error, which is exactly why
  nothing said anything for months. A test also asserts that nothing we ship trips
  the guard.

---

## 0.3.1 — 2026-08-10

### Added
- Two multi-drone scenarios that make "the detector reported one target" mean
  something. On its own that result cannot tell "the second carrier is not on air"
  from "the detector only reports the strongest", and a spectrum analyser is not
  always at hand.
  - `multi_drone_swap` — the same pair with the levels mirrored. Run it straight
    after `multi_drone`: if the reported frequency moves, both carriers are
    radiating and the question is the detector's, not the signal's.
  - `multi_drone_equal` — both targets at the same level. `multi_drone` makes the
    second one 6 dB weaker to stand for a drone further away, which at 5.8 GHz —
    where the Pluto output is weakest — can put it under the threshold on its own.

---

## 0.3.0 — 2026-08-10

### Added
- **A «Verbose» switch beside the event log.** Notes about how the board had to be
  configured — the FIR filter being dropped, a firmware profile contradicting the
  board — are true on every run for a board that always needs them, and noise by
  the third Start. They now appear once per session, or on every run with the
  switch on. Anything meaning "what is on air is not what you asked for" is never
  filed away: aliasing, a transmitter that did not start, a rate that does not fit.
  The distinction is a warning class, not a text match.

---

## 0.2.1 — 2026-08-10

Found by an adversarial review of 0.2.0: 29 candidate findings, 26 refuted, 3 real.

### Fixed
- **A restored dry run no longer looks like a transmission.** Because the backend
  is remembered now, an operator whose last session used `null` reopened the GUI
  with `null` still selected — and the restore replaced the event log immediately
  after the "nothing goes on air" warning was written into it. They would press
  Start and read a log identical to a real run. The log is now only replaced when
  the snapshot actually carries one, which is the language-rebuild case it was
  written for.
- **The Ukrainian startup-failure dialog was half English.** Its title and trailer
  were translated, the message and the hint were not — because they are passed as
  arguments to `_show_error()` rather than sitting inside a `t()` call, and the
  test that guards the catalogue could not see them. It sees wrapper arguments now.
- The notice that a board refused the FIR filter repeated on every arming retry.
  It is reported when the answer *changes*, so a board that starts refusing between
  opens is still announced.

- A test that timed genuine randomness to prove the inter-trial pause is
  jittered, and failed about once in 140 runs when four draws happened to land
  close together. The draw is scripted now: a suite that cries wolf is one nobody
  reads.

### Added
- Tests covering `PlutoSink` against a fake board: that it identifies the firmware,
  passes the profile through instead of defaulting to auto, warns once rather than
  per retry, and refuses an out-of-range rate before writing anything. Four separate
  mutations of that code used to pass the whole suite.

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
