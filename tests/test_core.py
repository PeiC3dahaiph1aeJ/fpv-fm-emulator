"""Offline unit tests for the emulator core (no hardware needed)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpv_emulator.bands import load_band_table
from fpv_emulator.fm import fm_modulate, to_int16_iq, occupied_bandwidth_hz
from fpv_emulator.signal_gen import (
    DroneSpec,
    generate_frame_iq,
    generate_multi_drone_iq,
)
from fpv_emulator.video import PAL50, NTSC60, generate_composite, list_patterns, get_standard


# --------------------------- bands ----------------------------------------
def test_band_lookup_and_case_insensitive():
    bt = load_band_table()
    assert bt.channel("R1").freq_mhz == pytest.approx(5658.0)
    assert bt.channel("r1").freq_hz == bt.channel("R1").freq_hz
    assert bt.channel("R:R8").freq_mhz == pytest.approx(5917.0)


def test_hw_range_guard():
    bt = load_band_table()
    ok_stock, warn = bt.check_reachable(5800e6, "stock")
    assert not ok_stock and warn is not None
    ok_hacked, _ = bt.check_reachable(5800e6, "hacked")
    assert ok_hacked
    ok_low, _ = bt.check_reachable(1200e6, "stock")
    assert ok_low


def test_group_and_band_listing():
    bt = load_band_table()
    assert "5.8G" in bt.groups()
    rb = bt.channels_in_band("R")
    assert len(rb) == 8
    assert rb[0].freq_mhz == pytest.approx(5658.0)


# --------------------------- video ----------------------------------------
@pytest.mark.parametrize("std", [PAL50, NTSC60])
def test_composite_length_is_frame(std):
    fs = 8e6
    comp = generate_composite("bars", std, fs)
    n_line = int(round(std.line_us * 1e-6 * fs))
    assert comp.size == n_line * std.total_lines


@pytest.mark.parametrize("pattern", list_patterns())
def test_composite_levels_in_range(pattern):
    comp = generate_composite(pattern, PAL50, 8e6)
    assert comp.min() >= PAL50.sync_level - 1e-6
    assert comp.max() <= PAL50.white_level + 0.2  # +burst tolerance
    # sync pulse present
    assert np.isclose(comp.min(), PAL50.sync_level, atol=1e-6)


def test_line_rate_close_to_spec():
    fs = 8e6
    n_line = int(round(PAL50.line_us * 1e-6 * fs))
    line_rate = fs / n_line
    assert line_rate == pytest.approx(15625.0, rel=0.01)


def test_low_fs_raises():
    with pytest.raises(ValueError):
        generate_composite("bars", PAL50, 100e3)  # too few samples per line


# --------------------------- fm -------------------------------------------
def test_fm_constant_gives_single_tone():
    fs = 4e6
    dev = 1e6
    v = np.full(200000, 0.25)          # constant
    iq = fm_modulate(v, fs, dev, center=False)
    # constant envelope
    assert np.allclose(np.abs(iq), 1.0, atol=1e-4)
    # frequency = dev * v
    spec = np.abs(np.fft.fftshift(np.fft.fft(iq)))
    freqs = np.fft.fftshift(np.fft.fftfreq(iq.size, 1 / fs))
    peak = freqs[np.argmax(spec)]
    assert peak == pytest.approx(dev * 0.25, abs=fs / iq.size * 4)


def test_fm_centering_zero_mean():
    fs = 4e6
    v = np.linspace(-0.3, 0.7, 100000)
    iq = fm_modulate(v, fs, 2e6, center=True)
    assert np.allclose(np.abs(iq), 1.0, atol=1e-4)


def test_to_int16_range():
    iq = np.exp(1j * np.linspace(0, 100, 1000)).astype(np.complex64)
    out = to_int16_iq(iq, scale=2 ** 14)
    assert out.real.max() <= 32767 and out.real.min() >= -32768
    assert np.abs(out).max() <= 2 ** 14 + 2


def test_carson_bandwidth():
    bw = occupied_bandwidth_hz(6e6, 5e6)
    assert bw == pytest.approx(2 * (3e6 + 5e6))


# --------------------------- signal_gen -----------------------------------
def test_frame_iq_matches_composite():
    fs = 8e6
    frame = generate_frame_iq("grid", PAL50, fs, 6e6)
    comp = generate_composite("grid", PAL50, fs)
    assert frame.iq.size == comp.size
    assert frame.iq.dtype == np.complex64
    assert frame.duration_s == pytest.approx(1.0 / PAL50.fps, rel=0.01)


def test_multi_drone_peak_normalised():
    std = get_standard("PAL50")
    specs = [
        DroneSpec("bars", offset_hz=-4e6, level_db=0),
        DroneSpec("grid", offset_hz=0.0, level_db=-3),
        DroneSpec("checker", offset_hz=4e6, level_db=-6),
    ]
    frame = generate_multi_drone_iq(specs, std, 20e6, 6e6)
    assert np.max(np.abs(frame.iq)) <= 1.0 + 1e-6
    assert frame.iq.size > 0


def test_multi_drone_offsets_present():
    std = get_standard("PAL50")
    specs = [DroneSpec("bars", offset_hz=-5e6, level_db=0),
             DroneSpec("bars", offset_hz=5e6, level_db=0)]
    frame = generate_multi_drone_iq(specs, std, 20e6, 3e6)
    spec = np.abs(np.fft.fftshift(np.fft.fft(frame.iq[: 1 << 16])))
    freqs = np.fft.fftshift(np.fft.fftfreq(1 << 16, 1 / 20e6))
    # energy must be present on both sides of the centre
    left = spec[freqs < -2e6].sum()
    right = spec[freqs > 2e6].sum()
    assert left > 0 and right > 0
