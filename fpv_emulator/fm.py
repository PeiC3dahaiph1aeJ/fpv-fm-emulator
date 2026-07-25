"""FM modulation of a real baseband signal into complex IQ.

Analog FPV video uses frequency modulation: the composite video signal deviates
the carrier. Here the baseband (in volts) is turned into complex IQ with a given
deviation. The envelope is constant (|iq| = const), just like a real FM transmitter.
"""
from __future__ import annotations

import numpy as np


def fm_modulate(
    baseband_v: np.ndarray,
    fs: float,
    deviation_pp_hz: float,
    amplitude: float = 1.0,
    center: bool = True,
) -> np.ndarray:
    """FM modulation.

    Parameters
    ----------
    baseband_v : real signal (volts, ~1 Vpp)
    fs         : sample rate, Hz
    deviation_pp_hz : peak-to-peak deviation for a full 1 Vpp video signal
    amplitude  : envelope amplitude (1.0 = full scale)
    center     : remove the mean (so the carrier sits in the middle of the
                 occupied bandwidth)

    Returns
    -------
    complex64 IQ of the same length as the baseband.
    """
    v = np.asarray(baseband_v, dtype=np.float64)
    if center:
        v = v - v.mean()
    # instantaneous frequency deviation: 1 Vpp -> deviation_pp_hz
    inst_freq = deviation_pp_hz * v
    # integral of frequency -> phase
    phase = 2.0 * np.pi * np.cumsum(inst_freq) / fs
    iq = amplitude * np.exp(1j * phase)
    return iq.astype(np.complex64)


def occupied_bandwidth_hz(deviation_pp_hz: float, video_bw_hz: float) -> float:
    """Occupied bandwidth estimate by Carson's rule: 2*(Δf_peak + f_video)."""
    dev_peak = deviation_pp_hz / 2.0
    return 2.0 * (dev_peak + video_bw_hz)


def to_int16_iq(iq: np.ndarray, scale: float = 2 ** 14) -> np.ndarray:
    """Convert complex IQ (|.|<=1) into the format for Pluto TX (int16 I/Q).

    pyadi-iio expects a complex array whose real/imaginary parts fit into the
    int16 range. We scale to ~half-scale (2^14) to keep headroom against
    clipping after several carriers are summed.
    """
    peak = np.max(np.abs(iq)) if iq.size else 1.0
    if peak > 1.0:
        iq = iq / peak  # overflow guard
    i = np.round(iq.real * scale).astype(np.int16)
    q = np.round(iq.imag * scale).astype(np.int16)
    return (i.astype(np.int32) + 1j * q.astype(np.int32)).astype(np.complex64)


def frequency_shift(iq: np.ndarray, fs: float, offset_hz: float) -> np.ndarray:
    """Shift IQ by offset_hz (to place a carrier within the instantaneous bandwidth)."""
    n = np.arange(iq.size)
    return (iq * np.exp(1j * 2.0 * np.pi * offset_hz * n / fs)).astype(np.complex64)
