"""FM modulation of a real baseband signal into complex IQ.

Аналогове FPV-відео використовує частотну модуляцію: композитний відеосигнал
відхиляє несучу. Тут baseband (вольти) перетворюється на комплексний IQ із
заданою девіацією. Огинаюча стала (|iq| = const) — як у реального ЧМ-передавача.
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
    """ЧМ-модуляція.

    Parameters
    ----------
    baseband_v : дійсний сигнал (вольти, ~1 Vpp)
    fs         : частота дискретизації, Гц
    deviation_pp_hz : девіація «розмах-у-розмах» на повний 1 Vpp відеосигналу
    amplitude  : амплітуда огинаючої (1.0 = повна шкала)
    center     : прибрати середнє (щоб несуча була по центру зайнятої смуги)

    Returns
    -------
    complex64 IQ тієї ж довжини, що й baseband.
    """
    v = np.asarray(baseband_v, dtype=np.float64)
    if center:
        v = v - v.mean()
    # миттєве відхилення частоти: 1 Vpp -> deviation_pp_hz
    inst_freq = deviation_pp_hz * v
    # інтеграл частоти -> фаза
    phase = 2.0 * np.pi * np.cumsum(inst_freq) / fs
    iq = amplitude * np.exp(1j * phase)
    return iq.astype(np.complex64)


def occupied_bandwidth_hz(deviation_pp_hz: float, video_bw_hz: float) -> float:
    """Оцінка зайнятої смуги за правилом Карсона: 2*(Δf_peak + f_video)."""
    dev_peak = deviation_pp_hz / 2.0
    return 2.0 * (dev_peak + video_bw_hz)


def to_int16_iq(iq: np.ndarray, scale: float = 2 ** 14) -> np.ndarray:
    """Перетворити комплексний IQ (|.|<=1) у формат для Pluto TX (int16 I/Q).

    pyadi-iio очікує комплексний масив, де дійсна/уявна частини вкладаються в
    діапазон int16. Масштабуємо до ~half-scale (2^14), щоб мати запас від
    клацання після сумування кількох несучих.
    """
    peak = np.max(np.abs(iq)) if iq.size else 1.0
    if peak > 1.0:
        iq = iq / peak  # захист від переповнення
    i = np.round(iq.real * scale).astype(np.int16)
    q = np.round(iq.imag * scale).astype(np.int16)
    return (i.astype(np.int32) + 1j * q.astype(np.int32)).astype(np.complex64)


def frequency_shift(iq: np.ndarray, fs: float, offset_hz: float) -> np.ndarray:
    """Зсунути IQ на offset_hz (для розміщення несучої в межах миттєвої смуги)."""
    n = np.arange(iq.size)
    return (iq * np.exp(1j * 2.0 * np.pi * offset_hz * n / fs)).astype(np.complex64)
