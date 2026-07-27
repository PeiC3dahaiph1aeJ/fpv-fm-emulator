"""Scenario-runner smoke tests using the NullSink (no hardware)."""
import os
import sys
import threading
import time
import warnings

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpv_emulator.backends import NullSink, TxConfig
from fpv_emulator.bands import load_band_table
from fpv_emulator.config import list_scenarios, load_scenario
from fpv_emulator.scenarios import ScenarioRunner
from fpv_emulator.signal_gen import generate_frame_iq
from fpv_emulator.video import PAL50


def _run_briefly(scenario, seconds=0.4):
    bt = load_band_table()
    sink = NullSink(TxConfig(fs=8e6, freq_hz=0.0))
    events = []
    runner = ScenarioRunner(sink, bt, on_event=events.append, sleep_slice_s=0.01)
    stop = threading.Event()
    th = threading.Thread(target=runner.run, args=(scenario, stop))
    th.start()
    time.sleep(seconds)
    stop.set()
    th.join(timeout=3)
    assert not th.is_alive(), "engine did not stop"
    return events


def test_sweep_emits_tunes():
    scenario = {
        "name": "t-sweep", "type": "sweep",
        "signal": {"standard": "PAL50", "pattern": "bars", "sample_rate": 8e6,
                   "deviation_pp_hz": 6e6, "gain_db": -10},
        "sweep": {"channels": ["R1", "R2", "R3"], "dwell_s": 0.05, "loops": 0},
    }
    events = _run_briefly(scenario, 0.5)
    tunes = [e for e in events if e["action"] == "tune"]
    assert len(tunes) >= 2
    assert {"start", "stop"} <= {e["action"] for e in events}


def test_power_ramp_emits_power():
    scenario = {
        "name": "t-ramp", "type": "power_ramp",
        "signal": {"standard": "PAL50", "pattern": "bars", "sample_rate": 8e6,
                   "deviation_pp_hz": 6e6},
        "power_ramp": {"channel": "R4", "start_db": -40, "end_db": 0,
                       "duration_s": 0.3, "steps": 10, "mode": "up", "loops": 1},
    }
    events = _run_briefly(scenario, 0.6)
    powers = [e for e in events if e["action"] == "power"]
    assert len(powers) >= 3
    gains = [e["gain_db"] for e in powers]
    assert gains[0] < gains[-1]  # ramp increases


def test_multi_drone_builds():
    scenario = {
        "name": "t-multi", "type": "multi_drone",
        "signal": {"standard": "PAL50", "sample_rate": 20e6, "deviation_pp_hz": 6e6,
                   "gain_db": -6},
        "multi_drone": {
            "center_channel": "R4",
            "drones": [
                {"pattern": "bars", "offset_mhz": -6, "level_db": 0},
                {"pattern": "grid", "offset_mhz": 6, "level_db": -3},
            ],
        },
    }
    events = _run_briefly(scenario, 0.3)
    multi = [e for e in events if e["action"] == "multi"]
    assert multi and multi[0]["n_drones"] == 2


def test_shipped_scenarios_load_and_validate():
    for name, path in list_scenarios().items():
        data = load_scenario(path)
        assert data.get("type") in {"static", "sweep", "power_ramp", "multi_drone"}


def test_nyquist_warning_fires():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        # deviation 30 MHz pp -> peak 15 MHz >> 0.45*8 MHz
        generate_frame_iq("bars", PAL50, 8e6, 30e6)
    assert any("aliasing" in str(x.message).lower() or "аліасинг" in str(x.message).lower()
               for x in w)


def test_soapy_sink_construction_and_graceful():
    import importlib.util
    import numpy as np
    from fpv_emulator.backends import make_sink, SoapySink, TxConfig, soapy_enumerate
    cfg = TxConfig(fs=8e6, freq_hz=1200e6, device="driver=hackrf")
    sink = make_sink("soapy", cfg)
    assert isinstance(sink, SoapySink)
    # enumerate must not fail even without SoapySDR
    assert isinstance(soapy_enumerate(), list)
    # without SoapySDR start() must raise a clear RuntimeError, not crash
    if importlib.util.find_spec("SoapySDR") is None:
        with pytest.raises(RuntimeError):
            sink.start(np.zeros(16, dtype=np.complex64))


# ---------------------------------------------------------------------------
#  Recording sink — proves WHAT the engine did to the device, not just what it
#  logged. Before these tests the pause branch, the live-gain path and the
#  "tune before the first start" ordering were never executed by the suite.
# ---------------------------------------------------------------------------
class RecordingSink(NullSink):
    """A NullSink that remembers the call sequence."""

    def __init__(self, cfg):
        super().__init__(cfg)
        self.calls = []

    def start(self, iq_int16):
        self.calls.append(("start", None))
        super().start(iq_int16)

    def stop(self):
        self.calls.append(("stop", None))
        super().stop()

    def set_freq(self, freq_hz):
        self.calls.append(("set_freq", round(freq_hz / 1e6, 3)))
        super().set_freq(freq_hz)

    def set_gain(self, gain_db):
        self.calls.append(("set_gain", gain_db))
        super().set_gain(gain_db)


def _run_with_sink(scenario, sink, seconds=0.6, before_stop=None):
    bt = load_band_table()
    events = []
    runner = ScenarioRunner(sink, bt, on_event=events.append, sleep_slice_s=0.01)
    stop = threading.Event()
    th = threading.Thread(target=runner.run, args=(scenario, stop))
    th.start()
    if before_stop is not None:
        time.sleep(seconds / 2)
        before_stop(runner)
        time.sleep(seconds / 2)
    else:
        time.sleep(seconds)
    stop.set()
    th.join(timeout=3)
    assert not th.is_alive(), "engine did not stop"
    return events, runner


def test_sweep_pause_switches_the_rf_off_between_channels():
    """pause_s > 0 must really stop the sink, not just log a pause."""
    scenario = {
        "name": "t-pause", "type": "sweep",
        "signal": {"standard": "PAL50", "pattern": "bars", "sample_rate": 8e6,
                   "deviation_pp_hz": 4e6, "gain_db": -10},
        "sweep": {"channels": ["R1", "R2"], "dwell_s": 0.05, "pause_s": 0.05, "loops": 1},
    }
    sink = RecordingSink(TxConfig(fs=8e6, freq_hz=0.0))
    events, _ = _run_with_sink(scenario, sink, seconds=0.8)

    assert any(e["action"] == "pause" for e in events), "no pause event"
    # per channel: retune, start, then stop for the silent gap
    names = [c[0] for c in sink.calls]
    i = names.index("set_freq")
    assert names[i:i + 3] == ["set_freq", "start", "stop"], names[:8]
    # the frequency is set BEFORE the first start (guards the tx_lo = 0 bug)
    assert names.index("set_freq") < names.index("start")


def test_sweep_without_pause_tunes_before_the_first_start():
    """Regression: PlutoSink opened at tx_lo = 0 and died with errno 22."""
    scenario = {
        "name": "t-nopause", "type": "sweep",
        "signal": {"standard": "PAL50", "pattern": "bars", "sample_rate": 8e6,
                   "deviation_pp_hz": 4e6, "gain_db": -10},
        "sweep": {"channels": ["R1", "R2"], "dwell_s": 0.05, "loops": 1},
    }
    sink = RecordingSink(TxConfig(fs=8e6, freq_hz=0.0))
    _run_with_sink(scenario, sink, seconds=0.5)
    names = [c[0] for c in sink.calls]
    assert "start" in names and "set_freq" in names
    assert names.index("set_freq") < names.index("start"), names[:6]


def test_live_gain_reaches_the_sink_during_a_run():
    """The GUI slider writes a value; the worker thread must apply it."""
    scenario = {
        "name": "t-live", "type": "static",
        "signal": {"standard": "PAL50", "pattern": "bars", "sample_rate": 8e6,
                   "deviation_pp_hz": 4e6, "gain_db": -40},
        "static": {"channel": "R1", "hold_s": 0},
    }
    sink = RecordingSink(TxConfig(fs=8e6, freq_hz=0.0))
    events, _ = _run_with_sink(scenario, sink, seconds=0.6,
                               before_stop=lambda r: r.set_live_gain(-33.0))

    assert ("set_gain", -33.0) in sink.calls, sink.calls
    assert any(e["action"] == "live_gain" and e["gain_db"] == -33.0 for e in events)
    assert sink.cfg.gain_db == -33.0


def test_live_gain_is_clamped_to_the_hardware_range():
    """tx_hardwaregain accepts -89..0 only; a wild value must not reach libiio."""
    scenario = {
        "name": "t-clamp", "type": "static",
        "signal": {"standard": "PAL50", "pattern": "bars", "sample_rate": 8e6,
                   "deviation_pp_hz": 4e6, "gain_db": -10},
        "static": {"channel": "R1", "hold_s": 0},
    }
    sink = RecordingSink(TxConfig(fs=8e6, freq_hz=0.0))
    _run_with_sink(scenario, sink, seconds=0.6,
                   before_stop=lambda r: r.set_live_gain(+25.0))
    applied = [v for name, v in sink.calls if name == "set_gain"]
    assert applied and max(applied) <= 0.0, applied


def test_sweep_ranges_never_exceed_stop_mhz():
    """A point past stop_mhz would put ~17 MHz of bandwidth outside the request."""
    scenario = {
        "name": "t-range", "type": "sweep",
        "signal": {"standard": "PAL50", "pattern": "bars", "sample_rate": 8e6,
                   "deviation_pp_hz": 4e6, "gain_db": -10},
        # 5725..5875 with a 40 MHz step used to emit 5885.0
        "sweep": {"ranges": [{"start_mhz": 5725, "stop_mhz": 5875, "step_mhz": 40}],
                  "dwell_s": 0.02, "pause_s": 0, "loops": 1},
    }
    events = _run_briefly(scenario, 0.6)
    tuned = [e["freq_mhz"] for e in events if e["action"] == "tune"]
    assert tuned, "no tune events"
    assert max(tuned) <= 5875.0 + 1e-6, tuned


def test_scenario_with_no_resolvable_channels_fails_loudly():
    """An empty channel list used to spin at 100% CPU with the sink already on."""
    scenario = {
        "name": "t-empty", "type": "sweep",
        "signal": {"standard": "PAL50", "pattern": "bars", "sample_rate": 8e6,
                   "deviation_pp_hz": 4e6, "gain_db": -10},
        "sweep": {"channels": [], "dwell_s": 0.02, "loops": 1},
    }
    bt = load_band_table()
    sink = RecordingSink(TxConfig(fs=8e6, freq_hz=0.0))
    runner = ScenarioRunner(sink, bt, sleep_slice_s=0.01)
    with pytest.raises(ValueError):
        runner.run(scenario, threading.Event())
    assert "start" not in [c[0] for c in sink.calls], "the sink was started anyway"
