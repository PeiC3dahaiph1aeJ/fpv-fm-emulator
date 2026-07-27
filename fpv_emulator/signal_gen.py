"""High-level signal generation: composite video -> FM -> cyclic IQ buffer.

Combines the video generator and the FM modulator into an IQ buffer ready for
transmission. A single frame tiles seamlessly, so it is transmitted cyclically
(the buffer lives in the device and repeats without USB involvement).
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

import numpy as np

from .fm import fm_modulate, frequency_shift, occupied_bandwidth_hz
from .i18n import t
from .video import (
    VideoStandard,
    generate_composite,
    generate_composite_color,
    is_color_pattern,
)


@dataclass
class FrameSignal:
    """Generated per-frame IQ buffer + metadata."""

    iq: np.ndarray                 # complex64, |.| normalised
    fs: float                      # sample rate, Hz
    std_name: str
    pattern: str
    deviation_pp_hz: float
    occupied_bw_hz: float
    n_samples: int = field(init=False)
    duration_s: float = field(init=False)

    def __post_init__(self) -> None:
        self.n_samples = int(self.iq.size)
        self.duration_s = self.n_samples / self.fs


# approximate effective video baseband width of our generator (for a bandwidth
# estimate). This is an upper bound; the deviation dominates the occupied bandwidth.
_VIDEO_BW_HZ = 1.5e6

#: Minimum spacing between two simultaneous drone carriers. Measured on the
#: realistic FPV profile (color_bars, deviation 6 MHz, ~11.5 MHz occupied at
#: -30 dB): the notch between the two peaks is ~36 dB at 18 MHz of spacing and
#: ~35 dB at 14 MHz, but collapses to ~16 dB at 12 MHz — from there down the pair
#: reads as one wide signal rather than two targets.
MIN_DRONE_SPACING_HZ = 14e6


# fraction of fs above which the instantaneous frequency folds back
_ALIAS_LIMIT_REL = 0.45

# cache of max|v - mean| (the true peak excursion per Hz of deviation) keyed by
# (pattern, std.name, fs) — the GUI asks for it on every readout update.
_EXCURSION_CACHE: Dict[Tuple[str, str, float], float] = {}


def check_nyquist(deviation_pp_hz: float, fs: float, max_offset_hz: float = 0.0) -> None:
    """Warn if the nominal peak frequency excursion approaches Nyquist (fs/2).

    The nominal instantaneous deviation is +/-deviation_pp/2; for multi-drone
    the maximum carrier offset is added. If that exceeds ~0.45*fs the signal aliases.

    This is a coarse, waveform-independent guard: fm_modulate centres the
    composite, so the TRUE excursion is deviation_pp * max|v - mean|, which
    depends on the pattern (and on fs). The exact check lives in
    check_waveform_aliasing() / would_alias() and is applied to every generated
    frame; this function is kept for callers that only know the parameters.
    """
    peak_excursion = deviation_pp_hz / 2.0 + abs(max_offset_hz)
    limit = _ALIAS_LIMIT_REL * fs
    if peak_excursion > limit:
        warnings.warn(
            t("Aliasing risk: peak excursion {peak} MHz exceeds 0.45*fs = {limit} MHz. "
              "Increase sample_rate or reduce the deviation/offsets.",
              peak=f"{peak_excursion/1e6:.1f}", limit=f"{limit/1e6:.1f}"),
            stacklevel=2,
        )


def _excursion_factor(composite: np.ndarray) -> float:
    """max|v - mean| of a composite frame — fm_modulate centres before modulating."""
    v = np.asarray(composite, dtype=np.float64)
    if v.size == 0:
        return 0.0
    return float(np.abs(v - v.mean()).max())


def _composite_for(pattern: str, std: VideoStandard, fs: float,
                   color_burst: bool = False) -> np.ndarray:
    """Generate a composite frame through the same dispatch generate_frame_iq uses."""
    if is_color_pattern(pattern):
        return generate_composite_color(pattern, std, fs)
    return generate_composite(pattern, std, fs, color_burst=color_burst)


def _warn_aliasing(pattern: str, peak_hz: float, fs: float, stacklevel: int) -> None:
    limit = _ALIAS_LIMIT_REL * fs
    if peak_hz > limit:
        warnings.warn(
            t("Aliasing risk: pattern '{pattern}' reaches a peak excursion of {peak} MHz, "
              "above 0.45*fs = {limit} MHz. Increase sample_rate or reduce the "
              "deviation/offsets.",
              pattern=pattern, peak=f"{peak_hz/1e6:.1f}", limit=f"{limit/1e6:.1f}"),
            stacklevel=stacklevel + 1,
        )


def check_waveform_aliasing(
    composite: np.ndarray,
    pattern: str,
    deviation_pp_hz: float,
    fs: float,
    max_offset_hz: float = 0.0,
    stacklevel: int = 2,
) -> float:
    """Warn if the TRUE peak excursion of this waveform folds over Nyquist.

    fm_modulate centres the composite, so the real instantaneous excursion is
    deviation_pp * max|v - mean| (waveform-dependent), not deviation_pp/2.
    Returns the peak excursion in Hz (+ |carrier offset| for multi-drone).
    """
    peak = deviation_pp_hz * _excursion_factor(composite) + abs(max_offset_hz)
    _warn_aliasing(pattern, peak, fs, stacklevel + 1)
    return peak


def would_alias(pattern: str, std: VideoStandard, fs: float, deviation_pp_hz: float,
                max_offset_hz: float = 0.0) -> Tuple[bool, float]:
    """Would these parameters alias?  -> (aliases, peak_excursion_hz).

    Same criterion as check_waveform_aliasing but without generating a frame every
    time: max|v - mean| is cached per (pattern, std, fs), so repeated calls (the
    GUI readout) cost a dict lookup. Never warns and never raises for an unknown
    pattern/parameters — it returns (False, 0.0) so a readout cannot break the GUI.
    """
    key = (pattern, std.name, float(fs))
    factor = _EXCURSION_CACHE.get(key)
    if factor is None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                composite = _composite_for(pattern, std, fs)
        except (KeyError, ValueError):
            return False, 0.0
        factor = _excursion_factor(composite)
        _EXCURSION_CACHE[key] = factor
    peak = deviation_pp_hz * factor + abs(max_offset_hz)
    return bool(peak > _ALIAS_LIMIT_REL * fs), peak


def generate_frame_iq(
    pattern: str,
    std: VideoStandard,
    fs: float,
    deviation_pp_hz: float,
    color_burst: bool = False,
) -> FrameSignal:
    """Generate one FM-modulated frame of FPV video as IQ.

    Color patterns (color_bars…) automatically go through the color generator.
    """
    check_nyquist(deviation_pp_hz, fs)
    if is_color_pattern(pattern):
        composite = generate_composite_color(pattern, std, fs)
        video_bw = std.color_subcarrier_hz + 1.0e6   # chroma widens the occupied bandwidth
    else:
        composite = generate_composite(pattern, std, fs, color_burst=color_burst)
        video_bw = _VIDEO_BW_HZ
    # the REAL aliasing criterion: fm_modulate centres the composite, so the peak
    # excursion is deviation_pp * max|v - mean|, not deviation_pp/2 (check_nyquist)
    factor = _excursion_factor(composite)
    if is_color_pattern(pattern) or not color_burst:
        _EXCURSION_CACHE[(pattern, std.name, float(fs))] = factor   # prime would_alias()
    _warn_aliasing(pattern, deviation_pp_hz * factor, fs, stacklevel=2)
    iq = fm_modulate(composite, fs, deviation_pp_hz, amplitude=1.0, center=True)
    bw = occupied_bandwidth_hz(deviation_pp_hz, video_bw)
    return FrameSignal(
        iq=iq,
        fs=fs,
        std_name=std.name,
        pattern=pattern,
        deviation_pp_hz=deviation_pp_hz,
        occupied_bw_hz=bw,
    )


@dataclass
class DroneSpec:
    """A single virtual "target" in a multi-drone scenario."""

    pattern: str
    offset_hz: float = 0.0     # carrier offset from the Pluto centre (within fs)
    level_db: float = 0.0      # relative level (0 = maximum)
    std: VideoStandard = None  # None -> the shared std is used


def generate_multi_drone_iq(
    drones: Sequence[DroneSpec],
    std: VideoStandard,
    fs: float,
    deviation_pp_hz: float,
    color_burst: bool = False,
) -> FrameSignal:
    """Sum several FM carriers at different offsets into one IQ buffer.

    All carriers must fall inside the instantaneous bandwidth (|offset| < fs/2
    with margin for the occupied bandwidth of each). For channels separated by
    tens of MHz use the second TX channel of the Pluto+ or sweep them in time.
    """
    if not drones:
        raise ValueError(t("Drone list is empty"))

    max_offset = max(abs(d.offset_hz) for d in drones)
    check_nyquist(deviation_pp_hz, fs, max_offset_hz=max_offset)

    # chroma widens the occupied bandwidth as soon as ONE carrier is a color pattern
    video_bw = (std.color_subcarrier_hz + 1.0e6
                if any(is_color_pattern(d.pattern) for d in drones)
                else _VIDEO_BW_HZ)
    bw = occupied_bandwidth_hz(deviation_pp_hz, video_bw)

    # Two carriers that sit too close stop being two targets: measured on the
    # realistic FPV profile, the notch between the peaks is ~36 dB at 18 MHz of
    # spacing but collapses to ~16 dB at 12 MHz, where the detector sees a single
    # wide blob instead of two drones.
    offsets = sorted(d.offset_hz for d in drones)
    for a, b in zip(offsets, offsets[1:]):
        gap = b - a
        if gap < MIN_DRONE_SPACING_HZ:
            warnings.warn(
                t("Drones {a} MHz and {b} MHz are only {gap} MHz apart — below "
                  "{min_gap} MHz the carriers merge into one blob for the detector.",
                  a=f"{a / 1e6:+.1f}", b=f"{b / 1e6:+.1f}", gap=f"{gap / 1e6:.1f}",
                  min_gap=f"{MIN_DRONE_SPACING_HZ / 1e6:.0f}"),
                stacklevel=2,
            )

    # NOTE: no span-vs-fs warning here on purpose. `bw` is a Carson estimate
    # (16.9 MHz for the realistic profile) while the measured occupied bandwidth
    # is ~11.5 MHz at -30 dB, so a span test built on it cries wolf on
    # configurations that are demonstrably clean (±9 MHz folds only 0.34% of the
    # power). Whether a carrier actually folds is decided per drone below by
    # check_waveform_aliasing, which uses the real waveform excursion plus that
    # drone's own offset.
    span_hz = (offsets[-1] - offsets[0]) + bw

    # buffer length = one frame of the shared standard (deliberately the shared
    # one, not drones[0].std) — computed arithmetically, exactly as the
    # generators lay out their frames: samples_per_line * total_lines.
    n = int(round(std.line_us * 1e-6 * fs)) * std.total_lines
    acc = np.zeros(n, dtype=np.complex64)

    for d in drones:
        d_std = d.std or std
        # same dispatch as generate_frame_iq: color patterns need the color generator
        comp = (generate_composite_color(d.pattern, d_std, fs)
                if is_color_pattern(d.pattern)
                else generate_composite(d.pattern, d_std, fs, color_burst=color_burst))
        # fit to the buffer length (different standards -> different lengths)
        if comp.size >= n:
            comp = comp[:n]
        else:
            reps = int(np.ceil(n / comp.size))
            comp = np.tile(comp, reps)[:n]
        # true excursion of THIS carrier (waveform + its own offset)
        check_waveform_aliasing(comp, d.pattern, deviation_pp_hz, fs,
                                max_offset_hz=d.offset_hz)
        amp = 10.0 ** (d.level_db / 20.0)
        iq = fm_modulate(comp, fs, deviation_pp_hz, amplitude=amp, center=True)
        if abs(d.offset_hz) > 0:
            iq = frequency_shift(iq, fs, d.offset_hz)
        acc += iq

    # normalise to avoid clipping after summation
    peak = np.max(np.abs(acc)) if acc.size else 1.0
    if peak > 0:
        acc = acc / peak
    span = span_hz
    return FrameSignal(
        iq=acc.astype(np.complex64),
        fs=fs,
        std_name=std.name,
        pattern="+".join(d.pattern for d in drones),
        deviation_pp_hz=deviation_pp_hz,
        occupied_bw_hz=span,
    )
