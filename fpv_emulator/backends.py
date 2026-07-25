"""TX backends: Pluto (pyadi-iio), file, and null.

An abstraction over the IQ sink. The core (video/fm/signal_gen) does not depend
on hardware; the hardware import (``adi``) is deferred, so offline development
and the tests work without libiio-Python and without a device.
"""
from __future__ import annotations

import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .i18n import t


@dataclass
class TxConfig:
    fs: float
    freq_hz: float
    gain_db: float = -10.0          # tx_hardwaregain (0 = max, -89 = min)
    rf_bw_hz: Optional[float] = None
    uri: str = "ip:192.168.2.1"     # typical Pluto URI over USB-Ethernet
    device: str = "driver=hackrf"   # SoapySDR device args (e.g. driver=hackrf|lime|uhd)


class BaseSink(ABC):
    """Common interface of an IQ-buffer sink."""

    def __init__(self, cfg: TxConfig):
        self.cfg = cfg
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @abstractmethod
    def start(self, iq_int16: np.ndarray) -> None:
        """Upload the cyclic buffer and start transmitting."""

    @abstractmethod
    def reload(self, iq_int16: np.ndarray) -> None:
        """Re-upload the buffer (pattern / multi-drone change)."""

    @abstractmethod
    def set_freq(self, freq_hz: float) -> None:
        """Retune the carrier (without re-uploading the buffer)."""

    @abstractmethod
    def set_gain(self, gain_db: float) -> None:
        """Change the power (tx_hardwaregain)."""

    @abstractmethod
    def stop(self) -> None:
        """Stop transmitting."""

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
    """Transmit through an ADALM-Pluto / Pluto+ (pyadi-iio)."""

    def __init__(self, cfg: TxConfig):
        super().__init__(cfg)
        self._sdr = None

    def _ensure_open(self) -> None:
        if self._sdr is not None:
            return
        try:
            import adi  # noqa: WPS433 (lazy import of the hardware dependency)
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                t("pyadi-iio is not installed. Install it with: "
                  "pip install pyadi-iio pylibiio")
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
#  SoapySDR backend — HackRF, LimeSDR, BladeRF, USRP, Pluto etc. (multi-SDR)
# ---------------------------------------------------------------------------
class SoapySink(BaseSink):
    """Transmit through any SDR that has a SoapySDR driver.

    Unlike the Pluto (cyclic buffer inside the device), there is no hardware
    looping here — the host continuously pushes one frame into the TX stream from
    a background thread. The frame tiles seamlessly (phase is continuous), so the
    loop is clean.

    device args: ``driver=hackrf`` | ``driver=lime`` | ``driver=uhd`` | ...
    Power: the -89..0 slider (Pluto convention) is mapped onto the real gain
    range of the device (0 dB = maximum).
    """

    def __init__(self, cfg: TxConfig):
        super().__init__(cfg)
        self._dev = None
        self._stream = None
        self._thread = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._buf = None            # complex64, normalised frame
        self._gmin = 0.0
        self._gmax = 47.0

    def _soapy(self):
        try:
            import SoapySDR  # noqa: WPS433
            return SoapySDR
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                t("SoapySDR is not installed. Install SoapySDR + the device driver: "
                  "Windows — PothosSDR (contains SoapySDR and the drivers); "
                  "Linux — apt install python3-soapysdr soapysdr-module-hackrf "
                  "(or -lime/-uhd/-bladerf).")
            ) from exc

    def _ensure_open(self) -> None:
        if self._dev is not None:
            return
        S = self._soapy()
        self._dev = S.Device(self.cfg.device or "driver=hackrf")
        self._dev.setSampleRate(S.SOAPY_SDR_TX, 0, float(self.cfg.fs))
        self._dev.setFrequency(S.SOAPY_SDR_TX, 0, float(self.cfg.freq_hz))
        try:
            rng = self._dev.getGainRange(S.SOAPY_SDR_TX, 0)
            self._gmin, self._gmax = float(rng.minimum()), float(rng.maximum())
        except Exception:  # pragma: no cover
            self._gmin, self._gmax = 0.0, 47.0
        self._apply_gain(self.cfg.gain_db)

    def _apply_gain(self, gain_db: float) -> None:
        S = self._soapy()
        # -89..0 (Pluto convention, 0 = max) -> the real range of the device
        norm = max(0.0, min(1.0, (float(gain_db) + 89.0) / 89.0))
        g = self._gmin + norm * (self._gmax - self._gmin)
        self._dev.setGain(S.SOAPY_SDR_TX, 0, float(g))

    @staticmethod
    def _to_cf32(iq_int16: np.ndarray) -> np.ndarray:
        return (iq_int16.astype(np.complex64)) / 32768.0

    def start(self, iq_int16: np.ndarray) -> None:
        self._ensure_open()
        with self._lock:
            self._buf = self._to_cf32(iq_int16)
        if self._thread is None or not self._thread.is_alive():
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._tx_loop, daemon=True)
            self._thread.start()
        self._running = True

    def _tx_loop(self) -> None:  # pragma: no cover - requires SoapySDR + a device
        S = self._soapy()
        dev = self._dev
        st = dev.setupStream(S.SOAPY_SDR_TX, S.SOAPY_SDR_CF32, [0])
        dev.activateStream(st)
        self._stream = st
        try:
            mtu = int(dev.getStreamMTU(st)) or 4096
            pos = 0
            while not self._stop_evt.is_set():
                with self._lock:
                    buf = self._buf
                n = len(buf)
                end = min(pos + mtu, n)
                chunk = np.ascontiguousarray(buf[pos:end])
                r = dev.writeStream(st, [chunk], len(chunk), timeoutUs=1_000_000)
                written = getattr(r, "ret", r if isinstance(r, int) else 0)
                if written > 0:
                    pos += written
                if pos >= n:
                    pos = 0
        finally:
            try:
                dev.deactivateStream(st)
                dev.closeStream(st)
            except Exception:
                pass
            self._stream = None

    def reload(self, iq_int16: np.ndarray) -> None:
        self._ensure_open()
        with self._lock:
            self._buf = self._to_cf32(iq_int16)
        if self._thread is None or not self._thread.is_alive():
            self.start(iq_int16)

    def set_freq(self, freq_hz: float) -> None:
        self.cfg.freq_hz = freq_hz
        if self._dev is not None:
            S = self._soapy()
            self._dev.setFrequency(S.SOAPY_SDR_TX, 0, float(freq_hz))

    def set_gain(self, gain_db: float) -> None:
        self.cfg.gain_db = gain_db
        if self._dev is not None:
            self._apply_gain(gain_db)

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._running = False

    def close(self) -> None:
        self.stop()
        self._dev = None


# ---------------------------------------------------------------------------
#  File backend — write IQ for inspection / GNU Radio replay
# ---------------------------------------------------------------------------
class FileSink(BaseSink):
    """Write the cyclic buffer to a file instead of the air.

    The ``.iq`` format is interleaved int16 (I,Q,I,Q...), compatible with GNU
    Radio / inspectrum. The ``.npy`` format is complex64 numpy.
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
#  Null backend — dry-run / GUI without hardware
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


def soapy_enumerate() -> list:
    """List the available SoapySDR devices (empty if SoapySDR is not installed)."""
    try:
        import SoapySDR
    except ImportError:
        return []
    try:
        return [dict(d) for d in SoapySDR.Device.enumerate()]
    except Exception:
        return []


def make_sink(kind: str, cfg: TxConfig, file_path: Optional[str] = None) -> BaseSink:
    kind = kind.lower()
    if kind == "pluto":
        return PlutoSink(cfg)
    if kind == "soapy":
        return SoapySink(cfg)
    if kind == "file":
        return FileSink(cfg, file_path or "out.iq")
    if kind == "null":
        return NullSink(cfg)
    raise ValueError(
        t("Unknown backend '{kind}' (pluto|soapy|file|null)", kind=kind)
    )
