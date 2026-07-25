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
    assert not th.is_alive(), "рушій не зупинився"
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
    assert gains[0] < gains[-1]  # рампа зростає


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
        # девіація 30 МГц pp -> пік 15 МГц >> 0.45*8МГц
        generate_frame_iq("bars", PAL50, 8e6, 30e6)
    assert any("аліасинг" in str(x.message).lower() or "aliy" in str(x.message).lower()
               or "Ризик" in str(x.message) for x in w)


def test_soapy_sink_construction_and_graceful():
    import importlib.util
    import numpy as np
    from fpv_emulator.backends import make_sink, SoapySink, TxConfig, soapy_enumerate
    cfg = TxConfig(fs=8e6, freq_hz=1200e6, device="driver=hackrf")
    sink = make_sink("soapy", cfg)
    assert isinstance(sink, SoapySink)
    # enumerate не падає навіть без SoapySDR
    assert isinstance(soapy_enumerate(), list)
    # без SoapySDR start() має дати зрозумілу RuntimeError, а не крах
    if importlib.util.find_spec("SoapySDR") is None:
        with pytest.raises(RuntimeError):
            sink.start(np.zeros(16, dtype=np.complex64))
