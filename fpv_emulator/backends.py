"""TX backends: Pluto (pyadi-iio), file, and null.

An abstraction over the IQ sink. The core (video/fm/signal_gen) does not depend
on hardware; the hardware import (``adi``) is deferred, so offline development
and the tests work without libiio-Python and without a device.
"""
from __future__ import annotations

import os
import threading
import time
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
        # Error channel: a background TX thread (or a swallowed teardown failure)
        # stores its exception here so the caller can notice that the RF is gone.
        self._error: Optional[BaseException] = None

    @property
    def running(self) -> bool:
        return self._running

    def poll_error(self) -> Optional[BaseException]:
        """Return and CLEAR the last background error (None if there was none).

        The scenario engine polls this while it sleeps: a sink whose TX thread
        died must not keep looking armed while nothing is radiated.
        """
        err, self._error = self._error, None
        return err

    def _record_error(self, exc: BaseException) -> None:
        """Store a background failure (first one wins — it is the root cause)."""
        if self._error is None:
            self._error = exc

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
        try:
            sdr.sample_rate = int(self.cfg.fs)
            sdr.tx_lo = int(self.cfg.freq_hz)
            rf_bw = int(self.cfg.rf_bw_hz or min(self.cfg.fs, 20e6))
            sdr.tx_rf_bandwidth = rf_bw
            sdr.tx_hardwaregain_chan0 = float(self.cfg.gain_db)
            sdr.tx_cyclic_buffer = True
            # Changing tx_lo makes the AD9361 recalibrate, and how long that takes
            # depends on the frequency (VCO band selection). Arming the buffer in
            # the middle of it can leave the DMA not running while tx() still
            # returns success — the "it only starts on the third try" symptom.
            time.sleep(0.25)
        except Exception:
            # Release the half-configured handle, otherwise the next attempt
            # finds the device busy and the real cause is masked.
            del sdr
            raise
        self._sdr = sdr

    def _arm(self, iq_int16: np.ndarray, attempts: int = 3) -> None:
        """Push the cyclic buffer, clearing any stale one first, and retry.

        Field symptom this fixes: "the transmitter does not always start"
        (confirmed on a spectrum analyser). A buffer left allocated by an earlier
        run — or by a run that was killed — makes tx() fail with EBUSY
        ("Open unlocked: -16"). Destroying before arming removes that class of
        failure, and the retry covers a device that needs a moment to settle.
        Silence is the real danger here: a transmitter that quietly does nothing
        makes every measurement taken afterwards wrong, so a failure to arm must
        raise rather than leave _running set.
        """
        last: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            try:
                self._sdr.tx_destroy_buffer()
            except Exception:
                pass                      # nothing to clear — normal on a fresh start
            try:
                self._sdr.tx(iq_int16)
                self._running = True
                return
            except Exception as exc:      # OSError/EBUSY and friends
                last = exc
                time.sleep(0.4 * attempt)
        self._running = False
        raise RuntimeError(
            t("The transmitter did not start after {n} attempts — the device is busy "
              "or in a stale state. Close anything else using the Pluto (the GUI, "
              "another run); if nothing is, unplug the USB, wait ~10 s and plug it "
              "back in. ({err})", n=attempts, err=str(last))
        )

    def start(self, iq_int16: np.ndarray) -> None:
        self._ensure_open()
        self._arm(iq_int16)

    def reload(self, iq_int16: np.ndarray) -> None:
        self._ensure_open()
        self._arm(iq_int16)

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
            except Exception as exc:  # pragma: no cover - needs a device
                # Destroying the cyclic buffer is what switches the RF off. If it
                # fails the device keeps radiating, so this must not be silent —
                # but stop() is called from a finally block and must not raise.
                self._record_error(RuntimeError(
                    t("Failed to stop the Pluto TX buffer ({err}) — "
                      "the device may still be radiating", err=str(exc))
                ))
        self._running = False

    def close(self) -> None:
        self.stop()
        self._sdr = None


# ---------------------------------------------------------------------------
#  SoapySDR backend — HackRF, LimeSDR, BladeRF, USRP, Pluto etc. (multi-SDR)
# ---------------------------------------------------------------------------

#: consecutive non-positive writeStream returns tolerated before the TX loop
#: gives up (SOAPY_SDR_TIMEOUT is -1; with timeoutUs=1 s this is several
#: seconds of a device that accepts nothing).
_WRITE_FAIL_LIMIT = 5

#: how long stop() waits for the TX loop to leave writeStream (seconds)
_STOP_JOIN_TIMEOUT_S = 5.0

#: shorter re-join budget once a thread is already known to be stuck, so that
#: repeated stop()/close() calls do not add minutes to a shutdown
_STUCK_JOIN_TIMEOUT_S = 0.5


def _write_ret(r) -> int:
    """Normalise the many shapes of a SoapySDR writeStream() return value."""
    if isinstance(r, (tuple, list)):
        r = r[0] if r else 0
    r = getattr(r, "ret", r)
    try:
        return int(r)
    except (TypeError, ValueError):
        return 0


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
        self._stream = None         # owned by THIS object, not by the TX thread
        self._thread = None
        # Event of the current run. start() always creates a NEW one, so a
        # thread that outlived its stop() can never be un-cancelled.
        self._stop_evt = threading.Event()
        self._stop_evt.set()        # nothing is running yet
        self._lock = threading.Lock()
        self._buf = None            # complex64, normalised frame
        self._gmin = 0.0
        self._gmax = 47.0
        self._stuck = False         # a previous TX thread refused to join

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
        try:
            self._dev.setSampleRate(S.SOAPY_SDR_TX, 0, float(self.cfg.fs))
            self._dev.setFrequency(S.SOAPY_SDR_TX, 0, float(self.cfg.freq_hz))
            try:
                rng = self._dev.getGainRange(S.SOAPY_SDR_TX, 0)
                self._gmin, self._gmax = float(rng.minimum()), float(rng.maximum())
            except Exception:  # pragma: no cover
                self._gmin, self._gmax = 0.0, 47.0
            self._apply_gain(self.cfg.gain_db)
        except Exception:
            # A half-configured device must not be reused: the next start()
            # would skip _ensure_open() and transmit at the wrong fs/frequency.
            self._dev = None
            raise

    def _apply_gain(self, gain_db: float) -> None:
        S = self._soapy()
        # -89..0 (Pluto convention, 0 = max) -> the real range of the device
        norm = max(0.0, min(1.0, (float(gain_db) + 89.0) / 89.0))
        g = self._gmin + norm * (self._gmax - self._gmin)
        self._dev.setGain(S.SOAPY_SDR_TX, 0, float(g))

    @staticmethod
    def _to_cf32(iq_int16: np.ndarray) -> np.ndarray:
        return (iq_int16.astype(np.complex64)) / 32768.0

    # -- stream ownership (this object, never the thread) --------------------
    def _open_stream(self):
        S = self._soapy()
        st = self._dev.setupStream(S.SOAPY_SDR_TX, S.SOAPY_SDR_CF32, [0])
        try:
            self._dev.activateStream(st)
        except Exception:
            try:
                self._dev.closeStream(st)
            except Exception:
                pass
            raise
        self._stream = st
        return st

    def _close_stream(self) -> None:
        st, self._stream = self._stream, None
        if st is None or self._dev is None:
            return
        try:
            self._dev.deactivateStream(st)
        except Exception as exc:  # pragma: no cover - device teardown
            self._record_error(RuntimeError(
                t("Failed to deactivate the TX stream ({err}) — "
                  "the device may still be radiating", err=str(exc))
            ))
        try:
            self._dev.closeStream(st)
        except Exception:  # pragma: no cover - device teardown
            pass

    def _reap_thread(self) -> bool:
        """Join a TX loop that has already exited. True if none is alive."""
        th = self._thread
        if th is None:
            return True
        if th.is_alive():
            return False
        th.join(timeout=1.0)
        self._thread = None
        self._stuck = False
        self._close_stream()        # the dead loop's stream belongs to us
        return True

    def start(self, iq_int16: np.ndarray) -> None:
        self._ensure_open()
        buf = self._to_cf32(iq_int16)
        if not self._reap_thread():
            if not self._stop_evt.is_set():
                # A live loop that was never asked to stop: just swap the frame.
                with self._lock:
                    self._buf = buf
                self._running = True
                return
            # Asked to stop, still alive: it owns the stream and the device, so
            # starting a second loop would open a second stream on one device.
            raise RuntimeError(t(
                "The previous transmission thread is still running — "
                "TX was not restarted (device {device} is busy)",
                device=self.cfg.device or "driver=hackrf",
            ))
        with self._lock:
            self._buf = buf
        st = self._open_stream()
        stop_evt = threading.Event()
        self._stop_evt = stop_evt
        self._thread = threading.Thread(
            target=self._tx_loop, args=(self._dev, st, stop_evt),
            daemon=True, name="soapy-tx",
        )
        self._running = True
        self._thread.start()

    def _tx_loop(self, dev, st, stop_evt) -> None:
        """Push the frame into the TX stream until stopped or until it fails.

        Every failure — including a device that stops accepting samples — is
        stored in the error channel and clears ``running``: a dead TX loop must
        never look like a transmitting one.
        """
        try:
            try:
                mtu = int(dev.getStreamMTU(st))
            except Exception:
                mtu = 0
            mtu = mtu or 4096
            pos = 0
            fails = 0
            while not stop_evt.is_set():
                with self._lock:
                    buf = self._buf
                if buf is None or len(buf) == 0:
                    stop_evt.wait(0.01)
                    continue
                n = len(buf)
                if pos >= n:
                    pos = 0
                end = min(pos + mtu, n)
                chunk = np.ascontiguousarray(buf[pos:end])
                written = _write_ret(
                    dev.writeStream(st, [chunk], len(chunk), timeoutUs=1_000_000)
                )
                if written > 0:
                    pos += written
                    fails = 0
                else:
                    # <0 is a Soapy error code (SOAPY_SDR_TIMEOUT == -1); 0 means
                    # nothing was accepted. Either way, do not spin silently.
                    fails += 1
                    if fails >= _WRITE_FAIL_LIMIT:
                        raise RuntimeError(t(
                            "SoapySDR writeStream failed {n} times in a row "
                            "(code {code}) — transmission stopped",
                            n=fails, code=written,
                        ))
                if pos >= n:
                    pos = 0
        except BaseException as exc:  # noqa: BLE001 - the thread must not die mute
            self._record_error(exc)
        finally:
            # The stream is owned by the sink object: stop()/start() close it.
            # Closing it here would race with a concurrent stop().
            self._running = False

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
        # Called from ScenarioRunner.run()'s finally — it must never raise, or it
        # would mask the scenario outcome. Failures go to the error channel.
        self._stop_evt.set()
        self._running = False
        th = self._thread
        if th is not None:
            th.join(timeout=_STUCK_JOIN_TIMEOUT_S if self._stuck
                    else _STOP_JOIN_TIMEOUT_S)
            if th.is_alive():
                # Keep the reference (dropping it would let the next start()
                # resurrect an orphan) and keep its stream open — closing a
                # stream inside a live writeStream() is a use-after-free.
                self._stuck = True
                self._record_error(RuntimeError(t(
                    "The transmission thread did not stop within {sec} s — "
                    "the device may still be radiating", sec=f"{_STOP_JOIN_TIMEOUT_S:.0f}",
                )))
                return
            self._thread = None
            self._stuck = False
        self._close_stream()

    def close(self) -> None:
        self.stop()
        if self._thread is not None and self._thread.is_alive():
            # Do not drop the device while a thread may still be writing to it:
            # a fresh _ensure_open() would open a second handle on one radio.
            return
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
