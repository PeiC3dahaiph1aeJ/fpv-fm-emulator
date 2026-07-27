"""Color-path tests — the production default profile.

Before these existed, rebinding generate_composite_color to a function that raises
still left the whole suite green: the pattern the tool transmits by default
(color_bars) was never executed by a single test.
"""
import os
import sys
import warnings

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fpv_emulator.signal_gen import (
    DroneSpec,
    MIN_DRONE_SPACING_HZ,
    generate_frame_iq,
    generate_multi_drone_iq,
)
from fpv_emulator.video import (
    NTSC60,
    PAL50,
    generate_composite,
    generate_composite_color,
    is_color_pattern,
    list_color_patterns,
)

FS = 20e6          # the production default sample rate
COLOR = "color_bars"


def _line_view(comp, std, fs):
    """Reshape a field into (lines, samples_per_line)."""
    n_line = int(round(std.line_us * 1e-6 * fs))
    return comp.reshape(std.total_lines, n_line), n_line


def _active_offsets(std, fs):
    """First sample of the active picture area within a line, and its length."""
    s = lambda us: int(round(us * 1e-6 * fs))
    a0 = s(std.sync_us) + s(std.back_porch_us)
    a1 = len(np.zeros(int(round(std.line_us * 1e-6 * fs)))) - s(std.front_porch_us)
    return a0, a1


@pytest.mark.parametrize("pattern", list_color_patterns())
def test_color_frame_length_matches_the_field(pattern):
    comp = generate_composite_color(pattern, PAL50, FS)
    n_line = int(round(PAL50.line_us * 1e-6 * FS))
    assert comp.size == n_line * PAL50.total_lines


@pytest.mark.parametrize("pattern", list_color_patterns())
def test_color_levels_stay_in_range(pattern):
    comp = generate_composite_color(pattern, PAL50, FS)
    # sync tip must still define the floor; chroma may overshoot white a little
    assert np.isclose(comp.min(), PAL50.sync_level, atol=1e-2)
    assert comp.max() <= PAL50.white_level + 0.5


def test_chroma_subcarrier_is_present_and_dominates_the_luma_path():
    """The 4.43 MHz chroma must be the dominant HF line, and absent in luma."""
    color = generate_composite_color(COLOR, PAL50, FS)
    luma = generate_composite("bars", PAL50, FS)

    def band_energy(sig, lo, hi):
        spec = np.abs(np.fft.rfft(sig - sig.mean())) ** 2
        freqs = np.fft.rfftfreq(sig.size, 1 / FS)
        return spec[(freqs > lo) & (freqs < hi)].sum()

    sc = PAL50.color_subcarrier_hz
    # dominant HF line sits on the subcarrier
    spec = np.abs(np.fft.rfft(color - color.mean()))
    freqs = np.fft.rfftfreq(color.size, 1 / FS)
    hf = (freqs > 3.0e6) & (freqs < 5.5e6)
    assert abs(freqs[hf][np.argmax(spec[hf])] - sc) < 200e3

    # and it is >20 dB above what the luma path puts in the same band
    ratio_db = 10 * np.log10(band_energy(color, 4e6, 5e6) / band_energy(luma, 4e6, 5e6))
    assert ratio_db > 20, f"chroma only {ratio_db:.1f} dB above luma"


def test_burst_is_on_active_lines_and_absent_from_the_vertical_interval():
    comp = generate_composite_color(COLOR, PAL50, FS)
    lines, n_line = _line_view(comp, PAL50, FS)
    s = lambda us: int(round(us * 1e-6 * FS))
    # back porch window, where the burst lives
    bp = slice(s(PAL50.sync_us), s(PAL50.sync_us) + s(PAL50.back_porch_us))

    first_active = PAL50.total_lines - PAL50.active_lines
    active_burst = np.abs(lines[first_active + 10][bp] - lines[first_active + 10][bp].mean()).max()
    vbi_burst = np.abs(lines[1][bp] - lines[1][bp].mean()).max()

    assert active_burst > 0.05, "no burst on an active line"
    assert vbi_burst < active_burst / 5, "burst leaked into the vertical interval"


def test_pal_v_phase_alternates_between_consecutive_lines():
    """PAL flips the V component every line; without it the decoder loses hue."""
    comp = generate_composite_color(COLOR, PAL50, FS)
    lines, n_line = _line_view(comp, PAL50, FS)
    first_active = PAL50.total_lines - PAL50.active_lines
    a0, a1 = _active_offsets(PAL50, FS)
    sc = PAL50.color_subcarrier_hz

    # V differs per colour bar, so averaging across the whole line cancels out —
    # correlate inside ONE bar. The subcarrier runs continuously across the field,
    # so the phase reference uses the ABSOLUTE sample index.
    n_bars = 8
    width = (a1 - a0) // n_bars

    def v_in_bar(row, bar):
        lo = a0 + bar * width + width // 4
        hi = a0 + bar * width + (3 * width) // 4
        n_abs = row * n_line + np.arange(lo, hi)
        seg = lines[row][lo:hi]
        seg = seg - seg.mean()
        ref_cos = np.cos(2 * np.pi * sc * n_abs / FS)
        return float(np.dot(seg, ref_cos) / len(seg))

    r1 = first_active + 20
    # use the bar with the strongest V on that line
    bar = max(range(n_bars), key=lambda b: abs(v_in_bar(r1, b)))
    v1, v2 = v_in_bar(r1, bar), v_in_bar(r1 + 1, bar)

    assert abs(v1) > 1e-3, f"no measurable V component in bar {bar} ({v1:.5f})"
    assert v1 * v2 < 0, f"V phase did not alternate in bar {bar} ({v1:.5f} vs {v2:.5f})"


def test_color_at_too_low_sample_rate_warns_about_the_subcarrier():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        generate_frame_iq(COLOR, PAL50, 8e6, 6e6)
    assert any("subcarrier" in str(x.message).lower() or "піднесуча" in str(x.message)
               for x in w), "no aliasing warning for color at 8 MSPS"


@pytest.mark.parametrize("std", [PAL50, NTSC60])
def test_frame_iq_dispatches_color_patterns(std):
    """generate_frame_iq must route color patterns to the color generator."""
    frame = generate_frame_iq(COLOR, std, FS, 6e6)
    assert frame.iq.size == generate_composite_color(COLOR, std, FS).size
    assert is_color_pattern(COLOR)


# --------------------------- multi-drone regressions -----------------------
def test_multi_drone_accepts_color_patterns():
    """Used to raise KeyError: the multi-drone path never dispatched on color."""
    frame = generate_multi_drone_iq(
        [DroneSpec(COLOR, offset_hz=-9e6), DroneSpec("color_bars100", offset_hz=9e6)],
        PAL50, 30.72e6, 6e6,
    )
    assert frame.iq.size > 0
    assert np.max(np.abs(frame.iq)) <= 1.0 + 1e-6


def test_multi_drone_warns_when_carriers_would_merge():
    """Below MIN_DRONE_SPACING_HZ the detector sees one blob, not two targets."""
    gap = MIN_DRONE_SPACING_HZ - 2e6
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        generate_multi_drone_iq(
            [DroneSpec(COLOR, offset_hz=-gap / 2), DroneSpec(COLOR, offset_hz=gap / 2)],
            PAL50, 30.72e6, 6e6,
        )
    assert any("apart" in str(x.message) or "рознесені" in str(x.message) for x in w)


def test_multi_drone_is_quiet_at_the_shipped_spacing():
    """±9 MHz on the shipped scenario must not warn (measured clean: 0.34% folded)."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        generate_multi_drone_iq(
            [DroneSpec(COLOR, offset_hz=-9e6), DroneSpec("color_bars100", offset_hz=9e6)],
            PAL50, 30.72e6, 6e6,
        )
    assert not w, f"unexpected warnings: {[str(x.message) for x in w]}"
