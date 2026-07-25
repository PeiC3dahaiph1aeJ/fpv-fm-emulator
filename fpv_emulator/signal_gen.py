"""High-level signal generation: composite video -> FM -> cyclic IQ buffer.

Combines the video generator and the FM modulator into an IQ buffer ready for
transmission. A single frame tiles seamlessly, so it is transmitted cyclically
(the buffer lives in the device and repeats without USB involvement).
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import List, Sequence

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


def check_nyquist(deviation_pp_hz: float, fs: float, max_offset_hz: float = 0.0) -> None:
    """Warn if the peak frequency excursion approaches Nyquist (fs/2).

    The instantaneous frequency deviation is +/-deviation_pp/2; for multi-drone
    the maximum carrier offset is added. If that exceeds ~0.45*fs the signal aliases.
    """
    peak_excursion = deviation_pp_hz / 2.0 + abs(max_offset_hz)
    limit = 0.45 * fs
    if peak_excursion > limit:
        warnings.warn(
            t("Aliasing risk: peak excursion {peak} MHz exceeds 0.45*fs = {limit} MHz. "
              "Increase sample_rate or reduce the deviation/offsets.",
              peak=f"{peak_excursion/1e6:.1f}", limit=f"{limit/1e6:.1f}"),
            stacklevel=2,
        )


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

    # buffer length = one frame of the shared standard
    ref = generate_composite(drones[0].pattern, std, fs, color_burst=color_burst)
    n = ref.size
    acc = np.zeros(n, dtype=np.complex64)

    for d in drones:
        d_std = d.std or std
        comp = generate_composite(d.pattern, d_std, fs, color_burst=color_burst)
        # fit to the buffer length (different standards -> different lengths)
        if comp.size >= n:
            comp = comp[:n]
        else:
            reps = int(np.ceil(n / comp.size))
            comp = np.tile(comp, reps)[:n]
        amp = 10.0 ** (d.level_db / 20.0)
        iq = fm_modulate(comp, fs, deviation_pp_hz, amplitude=amp, center=True)
        if abs(d.offset_hz) > 0:
            iq = frequency_shift(iq, fs, d.offset_hz)
        acc += iq

    # normalise to avoid clipping after summation
    peak = np.max(np.abs(acc)) if acc.size else 1.0
    if peak > 0:
        acc = acc / peak
    bw = occupied_bandwidth_hz(deviation_pp_hz, _VIDEO_BW_HZ)
    span = (max(d.offset_hz for d in drones) - min(d.offset_hz for d in drones)) + bw
    return FrameSignal(
        iq=acc.astype(np.complex64),
        fs=fs,
        std_name=std.name,
        pattern="+".join(d.pattern for d in drones),
        deviation_pp_hz=deviation_pp_hz,
        occupied_bw_hz=span,
    )
