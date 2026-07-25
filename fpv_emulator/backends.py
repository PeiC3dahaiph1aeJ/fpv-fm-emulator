"""TX backends: Pluto (pyadi-iio), file, and null.

Абстракція приймача IQ. Ядро (video/fm/signal_gen) не залежить від апаратури;
апаратний імпорт (``adi``) відкладений, тож офлайн-розробка й тести працюють без
libiio-Python та без пристрою.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class TxConfig:
    fs: float
    freq_hz: float
    gain_db: float = -10.0          # tx_hardwaregain (0 = макс, -89 = мін)
    rf_bw_hz: Optional[float] = None
    uri: str = "ip:192.168.2.1"     # типовий URI Pluto по USB-Ethernet


class BaseSink(ABC):
    """Загальний інтерфейс приймача IQ-буфера."""

    def __init__(self, cfg: TxConfig):
        self.cfg = cfg
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @abstractmethod
    def start(self, iq_int16: np.ndarray) -> None:
        """Завантажити циклічний буфер і почати передачу."""

    @abstractmethod
    def reload(self, iq_int16: np.ndarray) -> None:
        """Перезалити буфер (зміна патерну/мультидрону)."""

    @abstractmethod
    def set_freq(self, freq_hz: float) -> None:
        """Перестроїти несучу (без перезаливки буфера)."""

    @abstractmethod
    def set_gain(self, gain_db: float) -> None:
        """Змінити потужність (tx_hardwaregain)."""

    @abstractmethod
    def stop(self) -> None:
        """Зупинити передачу."""

    def close(self) -> None:
        self.stop()

    def info(self) -> dict:
        return {
            "backend": type(self).__name__,
            "fs": self.cfg.fs,
            "freq_hz": self.cfg.freq_hz,
            "gain_db": self.cfg.gain_db,
            "running": self._running,
        }


# ---------------------------------------------------------------------------
#  Pluto backend (pyadi-iio)
# ---------------------------------------------------------------------------
class PlutoSink(BaseSink):
    """Передача через ADALM-Pluto / Pluto+ (pyadi-iio)."""

    def __init__(self, cfg: TxConfig):
        super().__init__(cfg)
        self._sdr = None

    def _ensure_open(self) -> None:
        if self._sdr is not None:
            return
        try:
            import adi  # noqa: WPS433 (ліниве імпортування апаратної залежності)
        except ImportError as exc:  # pragma: no cover - залежить від оточення
            raise RuntimeError(
                "pyadi-iio не встановлено. Встановіть: pip install pyadi-iio pylibiio"
            ) from exc

        sdr = adi.Pluto(uri=self.cfg.uri)
        sdr.sample_rate = int(self.cfg.fs)
        sdr.tx_lo = int(self.cfg.freq_hz)
        rf_bw = int(self.cfg.rf_bw_hz or min(self.cfg.fs, 20e6))
        sdr.tx_rf_bandwidth = rf_bw
        sdr.tx_hardwaregain_chan0 = float(self.cfg.gain_db)
        sdr.tx_cyclic_buffer = True
        self._sdr = sdr

    def start(self, iq_int16: np.ndarray) -> None:
        self._ensure_open()
        self._sdr.tx(iq_int16)
        self._running = True

    def reload(self, iq_int16: np.ndarray) -> None:
        self._ensure_open()
        self._sdr.tx_destroy_buffer()
        self._sdr.tx(iq_int16)
        self._running = True

    def set_freq(self, freq_hz: float) -> None:
        self.cfg.freq_hz = freq_hz
        if self._sdr is not None:
            self._sdr.tx_lo = int(freq_hz)

    def set_gain(self, gain_db: float) -> None:
        self.cfg.gain_db = gain_db
        if self._sdr is not None:
            self._sdr.tx_hardwaregain_chan0 = float(gain_db)

    def stop(self) -> None:
        if self._sdr is not None:
            try:
                self._sdr.tx_destroy_buffer()
            except Exception:  # pragma: no cover
                pass
        self._running = False

    def close(self) -> None:
        self.stop()
        self._sdr = None


# ---------------------------------------------------------------------------
#  File backend — запис IQ для інспекції / GNU Radio replay
# ---------------------------------------------------------------------------
class FileSink(BaseSink):
    """Записати циклічний буфер у файл замість ефіру.

    Формат ``.iq`` — interleaved int16 (I,Q,I,Q...), сумісний з GNU Radio /
    inspectrum. Формат ``.npy`` — complex64 numpy.
    """

    def __init__(self, cfg: TxConfig, path: str):
        super().__init__(cfg)
        self.path = path
        self._last: Optional[np.ndarray] = None

    def _write(self, iq_int16: np.ndarray) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        if self.path.endswith(".npy"):
            np.save(self.path, iq_int16.astype(np.complex64))
        else:
            inter = np.empty(iq_int16.size * 2, dtype=np.int16)
            inter[0::2] = iq_int16.real.astype(np.int16)
            inter[1::2] = iq_int16.imag.astype(np.int16)
            inter.tofile(self.path)
        self._last = iq_int16

    def start(self, iq_int16: np.ndarray) -> None:
        self._write(iq_int16)
        self._running = True

    def reload(self, iq_int16: np.ndarray) -> None:
        self._write(iq_int16)

    def set_freq(self, freq_hz: float) -> None:
        self.cfg.freq_hz = freq_hz

    def set_gain(self, gain_db: float) -> None:
        self.cfg.gain_db = gain_db

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
#  Null backend — dry-run / GUI без апаратури
# ---------------------------------------------------------------------------
class NullSink(BaseSink):
    def start(self, iq_int16: np.ndarray) -> None:
        self._running = True

    def reload(self, iq_int16: np.ndarray) -> None:
        pass

    def set_freq(self, freq_hz: float) -> None:
        self.cfg.freq_hz = freq_hz

    def set_gain(self, gain_db: float) -> None:
        self.cfg.gain_db = gain_db

    def stop(self) -> None:
        self._running = False


def make_sink(kind: str, cfg: TxConfig, file_path: Optional[str] = None) -> BaseSink:
    kind = kind.lower()
    if kind == "pluto":
        return PlutoSink(cfg)
    if kind == "file":
        return FileSink(cfg, file_path or "out.iq")
    if kind == "null":
        return NullSink(cfg)
    raise ValueError(f"Невідомий backend '{kind}' (pluto|file|null)")
