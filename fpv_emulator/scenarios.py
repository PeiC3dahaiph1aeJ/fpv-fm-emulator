"""Scenario engine — drives a TX sink over time.

A scenario drives the IQ sink over time: channel hopping (sweep), a power ramp,
several "drones" at once, or a static carrier. The engine steps forward while
checking the stop event, so it works the same in the CLI (main thread) and in the
GUI (a separate QThread).

For the scenario schema (YAML) see config/scenarios/*.yaml and the README.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np

from .backends import BaseSink
from .bands import BandTable
from .fm import to_int16_iq
from .i18n import t
from .signal_gen import (
    DroneSpec,
    FrameSignal,
    generate_frame_iq,
    generate_multi_drone_iq,
)
from .video import get_standard

EventCb = Callable[[dict], None]


@dataclass
class SignalParams:
    """Signal parameters shared across a scenario."""

    standard: str = "PAL50"
    pattern: str = "color_bars"     # realistic FPV profile by default
    sample_rate: float = 20e6       # fits the 4.43 MHz subcarrier + a wide deviation
    deviation_pp_hz: float = 6e6
    gain_db: float = -10.0
    color_burst: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "SignalParams":
        d = dict(d or {})
        return cls(
            standard=str(d.get("standard", "PAL50")),
            pattern=str(d.get("pattern", "color_bars")),
            sample_rate=float(d.get("sample_rate", 20e6)),
            deviation_pp_hz=float(d.get("deviation_pp_hz", 6e6)),
            gain_db=float(d.get("gain_db", -10.0)),
            color_burst=bool(d.get("color_burst", False)),
        )


class ScenarioRunner:
    """Executes a scenario by driving the sink."""

    def __init__(
        self,
        sink: BaseSink,
        band_table: BandTable,
        on_event: Optional[EventCb] = None,
        sleep_slice_s: float = 0.05,
    ):
        self.sink = sink
        self.bands = band_table
        self.on_event = on_event or (lambda e: None)
        self.sleep_slice_s = sleep_slice_s
        self._live_gain_db = None   # set from the GUI thread, applied here

    # -- utils --------------------------------------------------------------
    def _emit(self, action: str, **kw) -> None:
        self.on_event({"t": time.monotonic(), "action": action, **kw})

    def set_live_gain(self, gain_db: float) -> None:
        """Request a power change at runtime (from another thread — a write only).

        Attribute assignment is atomic in CPython; the real sink.set_gain call is
        made on the worker thread via _apply_live(), so libiio is never touched
        from the GUI thread.
        """
        self._live_gain_db = float(gain_db)

    def _apply_live(self) -> None:
        """Apply a deferred power change (worker thread)."""
        g = self._live_gain_db
        if g is not None:
            self._live_gain_db = None
            self.sink.set_gain(g)
            self._emit("live_gain", gain_db=round(g, 1))   # visible confirmation in the log

    def _sleep(self, seconds: float, stop: threading.Event) -> bool:
        """Sleep in slices while reacting to stop. Returns False if interrupted."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if stop.is_set():
                return False
            self._apply_live()
            time.sleep(min(self.sleep_slice_s, max(0.0, end - time.monotonic())))
        return not stop.is_set()

    def _build_frame(self, sp: SignalParams) -> FrameSignal:
        std = get_standard(sp.standard)
        return generate_frame_iq(
            sp.pattern, std, sp.sample_rate, sp.deviation_pp_hz, color_burst=sp.color_burst
        )

    def _resolve_channels(self, cfg: dict) -> List["ChannelRef"]:
        """Extract the (name, freq_hz) list from a sweep config."""
        out: List[ChannelRef] = []
        if "ranges" in cfg:
            # stepped ranges: [{start_mhz, stop_mhz, step_mhz}]
            for rng in cfg["ranges"]:
                start = float(rng["start_mhz"])
                stop = float(rng["stop_mhz"])
                step = float(rng.get("step_mhz", 50.0))
                n = int(round((stop - start) / step)) if step > 0 else 0
                for k in range(n + 1):
                    f = start + k * step
                    out.append(ChannelRef(f"{f:.0f}MHz", f * 1e6))
        elif "channels" in cfg:
            for name in cfg["channels"]:
                ch = self.bands.channel(name)
                out.append(ChannelRef(ch.name, ch.freq_hz))
        elif "band" in cfg:
            for ch in self.bands.channels_in_band(cfg["band"]):
                out.append(ChannelRef(ch.name, ch.freq_hz))
        elif "group" in cfg:
            for ch in self.bands.channels_in_group(cfg["group"]):
                out.append(ChannelRef(ch.name, ch.freq_hz))
        elif "freq_list_mhz" in cfg:
            for f in cfg["freq_list_mhz"]:
                out.append(ChannelRef(f"{f}MHz", float(f) * 1e6))
        else:
            raise ValueError(
                t("sweep: specify channels | band | group | freq_list_mhz")
            )
        return out

    def _channel_freq(self, cfg: dict, key: str = "channel") -> "ChannelRef":
        if key in cfg:
            ch = self.bands.channel(cfg[key])
            return ChannelRef(ch.name, ch.freq_hz)
        if "freq_mhz" in cfg:
            return ChannelRef(f"{cfg['freq_mhz']}MHz", float(cfg["freq_mhz"]) * 1e6)
        raise ValueError(t("Specify '{key}' or 'freq_mhz'", key=key))

    # -- dispatch -----------------------------------------------------------
    def run(self, scenario: dict, stop: Optional[threading.Event] = None) -> None:
        stop = stop or threading.Event()
        stype = str(scenario.get("type", "static")).lower()
        sp = SignalParams.from_dict(scenario.get("signal"))
        self._emit("start", scenario=t(scenario.get("name", stype)), type=stype)
        try:
            if stype == "static":
                self._run_static(scenario, sp, stop)
            elif stype == "sweep":
                self._run_sweep(scenario, sp, stop)
            elif stype == "power_ramp":
                self._run_power_ramp(scenario, sp, stop)
            elif stype == "multi_drone":
                self._run_multi_drone(scenario, sp, stop)
            else:
                raise ValueError(t("Unknown scenario type '{stype}'", stype=stype))
        finally:
            self.sink.stop()
            self._emit("stop", scenario=t(scenario.get("name", stype)))

    # -- scenario implementations ------------------------------------------
    def _run_static(self, scenario: dict, sp: SignalParams, stop: threading.Event) -> None:
        cfg = scenario.get("static", {})
        ref = self._channel_freq(cfg)
        frame = self._build_frame(sp)
        self.sink.set_freq(ref.freq_hz)
        self.sink.set_gain(sp.gain_db)
        self.sink.start(to_int16_iq(frame.iq))
        self._emit("tune", channel=ref.name, freq_mhz=ref.freq_hz / 1e6,
                   gain_db=sp.gain_db, bw_mhz=frame.occupied_bw_hz / 1e6)
        hold = float(cfg.get("hold_s", 0))  # 0 = until stopped
        if hold > 0:
            self._sleep(hold, stop)
        else:
            while not stop.is_set():
                self._apply_live()
                time.sleep(self.sleep_slice_s)

    def _run_sweep(self, scenario: dict, sp: SignalParams, stop: threading.Event) -> None:
        cfg = scenario.get("sweep", {})
        channels = self._resolve_channels(cfg)
        dwell = float(cfg.get("dwell_s", 2.0))     # transmission time on each frequency
        pause = float(cfg.get("pause_s", 0.0))     # silence between frequencies (0 = no pauses)
        loops = int(cfg.get("loops", 0))           # 0 = endless
        frame = self._build_frame(sp)
        buf = to_int16_iq(frame.iq)
        self.sink.set_gain(sp.gain_db)
        if pause <= 0:
            self.sink.start(buf)  # continuous, only the LO is retuned
        lap = 0
        while not stop.is_set():
            for ref in channels:
                if stop.is_set():
                    break
                self.sink.set_freq(ref.freq_hz)
                if pause > 0:
                    self.sink.start(buf)   # (re)start transmission on this frequency
                self._emit("tune", channel=ref.name, freq_mhz=ref.freq_hz / 1e6,
                           gain_db=sp.gain_db, lap=lap)
                if not self._sleep(dwell, stop):
                    break
                if pause > 0:
                    self.sink.stop()       # RF off — pause
                    self._emit("pause", freq_mhz=ref.freq_hz / 1e6, seconds=pause)
                    if not self._sleep(pause, stop):
                        break
            lap += 1
            if loops and lap >= loops:
                break

    def _run_power_ramp(self, scenario: dict, sp: SignalParams, stop: threading.Event) -> None:
        cfg = scenario.get("power_ramp", {})
        ref = self._channel_freq(cfg)
        start_db = float(cfg.get("start_db", -40.0))
        end_db = float(cfg.get("end_db", 0.0))
        duration = float(cfg.get("duration_s", 10.0))
        mode = str(cfg.get("mode", "up")).lower()   # up | down | pingpong
        loops = int(cfg.get("loops", 0))
        steps = max(2, int(cfg.get("steps", 40)))

        frame = self._build_frame(sp)
        self.sink.set_freq(ref.freq_hz)
        self.sink.start(to_int16_iq(frame.iq))
        self._emit("tune", channel=ref.name, freq_mhz=ref.freq_hz / 1e6)

        def ramp(a: float, b: float) -> bool:
            dt = duration / steps
            for k in range(steps + 1):
                if stop.is_set():
                    return False
                g = a + (b - a) * k / steps
                self.sink.set_gain(g)
                self._emit("power", channel=ref.name, gain_db=round(g, 2),
                           freq_mhz=ref.freq_hz / 1e6)
                if not self._sleep(dt, stop):
                    return False
            return True

        lap = 0
        while not stop.is_set():
            if mode == "down":
                ok = ramp(end_db, start_db)
            elif mode == "pingpong":
                ok = ramp(start_db, end_db) and ramp(end_db, start_db)
            else:  # up
                ok = ramp(start_db, end_db)
            if not ok:
                break
            lap += 1
            if loops and lap >= loops:
                break

    def _run_multi_drone(self, scenario: dict, sp: SignalParams, stop: threading.Event) -> None:
        cfg = scenario.get("multi_drone", {})
        center = self._channel_freq(cfg, key="center_channel")
        std = get_standard(sp.standard)
        drones_cfg = cfg.get("drones", [])
        if not drones_cfg:
            raise ValueError(t("multi_drone: specify the 'drones' list"))
        specs = [
            DroneSpec(
                pattern=str(d.get("pattern", sp.pattern)),
                offset_hz=float(d.get("offset_mhz", 0.0)) * 1e6,
                level_db=float(d.get("level_db", 0.0)),
            )
            for d in drones_cfg
        ]
        frame = generate_multi_drone_iq(
            specs, std, sp.sample_rate, sp.deviation_pp_hz, color_burst=sp.color_burst
        )
        self.sink.set_freq(center.freq_hz)
        self.sink.set_gain(sp.gain_db)
        self.sink.start(to_int16_iq(frame.iq))
        self._emit("multi", center_mhz=center.freq_hz / 1e6, n_drones=len(specs),
                   span_mhz=frame.occupied_bw_hz / 1e6,
                   offsets_mhz=[s.offset_hz / 1e6 for s in specs])
        hold = float(cfg.get("hold_s", 0))
        if hold > 0:
            self._sleep(hold, stop)
        else:
            while not stop.is_set():
                self._apply_live()
                time.sleep(self.sleep_slice_s)


@dataclass
class ChannelRef:
    name: str
    freq_hz: float
