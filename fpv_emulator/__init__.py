"""FPV analog-video FM emulator for ADALM-Pluto+.

Генератор тестового аналогового FPV-відеосигналу (композитне відео + ЧМ) для
перевірки роботи детекторів FPV. Ядро (video/fm/signal_gen/bands/scenarios)
не залежить від апаратури — його можна запускати офлайн і покривати тестами.
Передача в ефір іде через бекенд Pluto (pyadi-iio) або у файл.
"""

__version__ = "0.1.0"

from .bands import BandTable, load_band_table
from .video import VideoStandard, PAL50, NTSC60, list_patterns, generate_composite
from .fm import fm_modulate, to_int16_iq, occupied_bandwidth_hz
from .signal_gen import (
    FrameSignal,
    generate_frame_iq,
    generate_multi_drone_iq,
)

__all__ = [
    "__version__",
    "BandTable",
    "load_band_table",
    "VideoStandard",
    "PAL50",
    "NTSC60",
    "list_patterns",
    "generate_composite",
    "fm_modulate",
    "to_int16_iq",
    "occupied_bandwidth_hz",
    "FrameSignal",
    "generate_frame_iq",
    "generate_multi_drone_iq",
]
