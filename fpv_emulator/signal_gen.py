"""High-level signal generation: composite video -> FM -> cyclic IQ buffer.

Об'єднує генератор відео та ЧМ-модулятор в готовий до передачі IQ-буфер. Один
кадр безшовно тайлиться, тож його передають циклічно (буфер лежить у пристрої й
повторюється без участі USB).
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import List, Sequence

import numpy as np

from .fm import fm_modulate, frequency_shift, occupied_bandwidth_hz
from .video import (
    VideoStandard,
    generate_composite,
    generate_composite_color,
    is_color_pattern,
)


@dataclass
class FrameSignal:
    """Згенерований кадровий IQ-буфер + метадані."""

    iq: np.ndarray                 # complex64, |.| нормовано
    fs: float                      # частота дискретизації, Гц
    std_name: str
    pattern: str
    deviation_pp_hz: float
    occupied_bw_hz: float
    n_samples: int = field(init=False)
    duration_s: float = field(init=False)

    def __post_init__(self) -> None:
        self.n_samples = int(self.iq.size)
        self.duration_s = self.n_samples / self.fs


# приблизна ефективна ширина відео-baseband нашого генератора (для оцінки смуги).
# Це оцінка «зверху»; головний внесок у зайняту смугу дає девіація.
_VIDEO_BW_HZ = 1.5e6


def check_nyquist(deviation_pp_hz: float, fs: float, max_offset_hz: float = 0.0) -> None:
    """Попередити, якщо пікова частотна екскурсія наближається до Найквіста (fs/2).

    Миттєве відхилення частоти = ±deviation_pp/2; для мультидрону додається
    максимальний зсув несучої. Якщо це перевищує ~0.45*fs — сигнал завернеться.
    """
    peak_excursion = deviation_pp_hz / 2.0 + abs(max_offset_hz)
    limit = 0.45 * fs
    if peak_excursion > limit:
        warnings.warn(
            f"Ризик аліасингу: пікова екскурсія {peak_excursion/1e6:.1f} МГц перевищує "
            f"0.45*fs = {limit/1e6:.1f} МГц. Підніміть sample_rate або зменшіть "
            f"девіацію/зсуви.",
            stacklevel=2,
        )


def generate_frame_iq(
    pattern: str,
    std: VideoStandard,
    fs: float,
    deviation_pp_hz: float,
    color_burst: bool = False,
) -> FrameSignal:
    """Згенерувати один ЧМ-модульований кадр FPV-відео як IQ.

    Кольорові патерни (color_bars…) автоматично йдуть через кольоровий генератор.
    """
    check_nyquist(deviation_pp_hz, fs)
    if is_color_pattern(pattern):
        composite = generate_composite_color(pattern, std, fs)
        video_bw = std.color_subcarrier_hz + 1.0e6   # хрома розширює зайняту смугу
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
    """Одна віртуальна «ціль» у мультидрон-сценарії."""

    pattern: str
    offset_hz: float = 0.0     # зсув несучої відносно центру Pluto (в межах fs)
    level_db: float = 0.0      # відносний рівень (0 = максимум)
    std: VideoStandard = None  # None -> береться спільний std


def generate_multi_drone_iq(
    drones: Sequence[DroneSpec],
    std: VideoStandard,
    fs: float,
    deviation_pp_hz: float,
    color_burst: bool = False,
) -> FrameSignal:
    """Сумувати кілька ЧМ-несучих на різних зсувах у один IQ-буфер.

    Усі несучі мають лежати в межах миттєвої смуги (|offset| < fs/2 з запасом на
    зайняту смугу кожної). Для рознесених на десятки МГц каналів використовуйте
    другий TX-канал Pluto+ або перебір у часі.
    """
    if not drones:
        raise ValueError("Список дронів порожній")

    max_offset = max(abs(d.offset_hz) for d in drones)
    check_nyquist(deviation_pp_hz, fs, max_offset_hz=max_offset)

    # довжина буфера = кадр спільного стандарту
    ref = generate_composite(drones[0].pattern, std, fs, color_burst=color_burst)
    n = ref.size
    acc = np.zeros(n, dtype=np.complex64)

    for d in drones:
        d_std = d.std or std
        comp = generate_composite(d.pattern, d_std, fs, color_burst=color_burst)
        # підігнати під довжину буфера (різні стандарти -> різна довжина)
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

    # нормування, щоб уникнути клацання після сумування
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
