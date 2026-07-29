"""Latency-measurement tests — offline, with a fake radio and a fake log."""
import os
import re
import sys
import threading
import time

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpv_emulator.latency import (
    Trial,
    _line_freq_mhz,
    measure_detection_latency,
    summarize,
)
from fpv_emulator.logsource import LogLine, LogSource

# the shipped defaults, kept in one place so the tests guard the real thing
VIDEO_RE = r">>>\s*VIDEO\s+(?P<mhz>\d+)\s*MHz"
CONFIRM_RE = r"DETECTION_STARTED.*?(?P<mhz>\d{3,5})\s*MHz"

# verbatim lines from a real detector session
L_VIDEO_3240 = "W (202734) scan_eng: >>> VIDEO 3240 MHz: pulses=31/10 conf=100% line=15.625kHz"
L_VIDEO_2780 = "W (201094) scan_eng: >>> VIDEO 2780 MHz: pulses=23/10 conf=65% line=15.625kHz"
L_STARTED = "W (206404) Detector: DETECTION_STARTED 3G 3239 MHz (3230-3240)"
L_LOST = "I (190274) Detector: DETECTION_LOST"
L_PARKED = "W (208454) scan_eng: SEEK parked 3G 3239: present | RSSI 1365 (82%)"


# --------------------------- pattern defaults ------------------------------
def test_video_pattern_captures_the_frequency():
    m = re.search(VIDEO_RE, L_VIDEO_3240, re.I)
    assert m and _line_freq_mhz(m) == 3240.0


def test_confirm_pattern_gets_past_the_band_token():
    """'DETECTION_STARTED 3G 3239 MHz' — a \\D* separator cannot pass the '3' of 3G."""
    m = re.search(CONFIRM_RE, L_STARTED, re.I)
    assert m and _line_freq_mhz(m) == 3239.0


@pytest.mark.parametrize("line", [L_LOST, L_PARKED])
def test_markers_do_not_match_unrelated_lines(line):
    assert not re.search(VIDEO_RE, line, re.I)
    assert not re.search(CONFIRM_RE, line, re.I)


def test_summarize_orders_the_percentiles():
    s = summarize([0.1, 0.2, 0.3, 0.4, 0.5])
    assert s["n"] == 5
    assert s["min_ms"] <= s["median_ms"] <= s["p95_ms"] <= s["max_ms"]
    assert s["min_ms"] == pytest.approx(100.0)


def test_summarize_of_nothing_is_empty():
    assert summarize([]) == {}


# --------------------------- fakes -----------------------------------------
class FakeSdr:
    """Just enough of adi.Pluto: records what the run did to the radio."""

    def __init__(self):
        self.tx_lo = 0
        self.tx_cyclic_buffer = False
        self.uploads = 0
        self.gains = []

    @property
    def tx_hardwaregain_chan0(self):
        return self.gains[-1] if self.gains else None

    @tx_hardwaregain_chan0.setter
    def tx_hardwaregain_chan0(self, v):
        self.gains.append(float(v))

    def tx(self, _buf):
        self.uploads += 1

    def tx_destroy_buffer(self):
        pass


class FakeLog(LogSource):
    """Replays scripted lines; each is released when the gain goes up."""

    def __init__(self, script):
        super().__init__()
        self.script = list(script)     # (delay_s, text)
        self.sdr = None

    def _open(self):
        pass

    def _read_chunk(self):
        return ""

    def _close(self):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def arm(self, sdr):
        self.sdr = sdr

    def get(self, timeout):
        # emit the next scripted line once the carrier is on
        if not self.script or self.sdr is None or not self.sdr.gains:
            time.sleep(min(timeout, 0.01))
            return None
        if self.sdr.gains[-1] <= -80:      # still muted
            time.sleep(min(timeout, 0.01))
            return None
        delay, text = self.script.pop(0)
        time.sleep(delay)
        return LogLine(t=time.perf_counter(), text=text, wall=time.time())

    def drain(self):
        return []


def _run(script, **kw):
    sdr, log = FakeSdr(), FakeLog(script)
    log.arm(sdr)
    buf = np.zeros(8, dtype=np.complex64)
    return sdr, measure_detection_latency(
        sdr, buf, log, VIDEO_RE, 3240e6, trials=1, timeout_s=2.0,
        gap_s=0.01, settle_s=0.0, **kw)


# --------------------------- the measurement loop --------------------------
def test_buffer_is_uploaded_once_and_the_carrier_is_gated_by_gain():
    """Re-uploading per trial costs ~105 ms on real hardware — it must not happen."""
    sdr, trials = _run([(0.02, L_VIDEO_3240)])
    assert sdr.uploads == 1, "the buffer was re-uploaded"
    assert any(g <= -80 for g in sdr.gains), "the carrier was never muted"
    assert trials[0].ok


def test_a_hit_on_another_frequency_does_not_end_the_trial():
    """A sweeping detector prints candidates across the band; only ours counts."""
    _, trials = _run([(0.01, L_VIDEO_2780), (0.05, L_VIDEO_3240)])
    tr = trials[0]
    assert tr.ok and tr.reported_mhz == 3240.0
    assert tr.latency_s > 0.05, "it stopped on the 2780 MHz line"


def test_confirmed_detection_is_recorded_after_the_video_hit():
    _, trials = _run([(0.02, L_VIDEO_3240), (0.05, L_STARTED)],
                     confirm_pattern=CONFIRM_RE)
    tr = trials[0]
    assert tr.ok and tr.confirm_s is not None
    assert tr.confirm_s > tr.latency_s
    assert "DETECTION_STARTED" in tr.confirm_line


def test_offset_is_subtracted_from_the_result():
    _, base = _run([(0.05, L_VIDEO_3240)])
    _, shifted = _run([(0.05, L_VIDEO_3240)], offset_s=0.02)
    assert shifted[0].latency_s == pytest.approx(base[0].latency_s - 0.02, abs=0.03)


def test_a_silent_detector_times_out_instead_of_hanging():
    _, trials = _run([])
    assert not trials[0].ok and trials[0].note == "timeout"
