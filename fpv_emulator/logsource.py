"""Log sources — read a detector's log stream and timestamp every line on arrival.

Used by the latency measurements: the moment a line ARRIVES is our observation of
when the detector reported something. That is deliberately not the same as when the
detector internally decided — the transport (USB-serial buffering) and the
detector's own print cadence sit in between, and both are part of what we measure.

Timestamps come from ``time.perf_counter()`` (monotonic, high resolution) so they can
be compared with the transmit timestamps without wall-clock drift.
"""
from __future__ import annotations

import queue
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from .i18n import t


@dataclass
class LogLine:
    """One line from the detector, with the instant it reached us."""

    t: float          # perf_counter() at arrival
    text: str
    wall: float       # time.time(), for human-readable logs


class LogSource(ABC):
    """A stream of timestamped log lines."""

    def __init__(self) -> None:
        self._q: "queue.Queue[LogLine]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._error: Optional[BaseException] = None

    # -- lifecycle ----------------------------------------------------------
    @abstractmethod
    def _open(self) -> None:
        """Open the underlying handle (raise on failure)."""

    @abstractmethod
    def _read_chunk(self) -> str:
        """Return whatever text is available, or '' — must not block for long."""

    @abstractmethod
    def _close(self) -> None:
        """Release the underlying handle."""

    def start(self) -> None:
        self._open()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._close()

    def __enter__(self) -> "LogSource":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- reader thread ------------------------------------------------------
    def _loop(self) -> None:
        buf = ""
        while not self._stop.is_set():
            try:
                chunk = self._read_chunk()
            except Exception as exc:  # noqa: BLE001 — surfaced via poll_error()
                self._error = exc
                return
            if not chunk:
                continue
            # Timestamp the moment the chunk arrived: every line completed by this
            # chunk is credited to it. Splitting first and timestamping later would
            # attribute processing time to the detector.
            now, wall = time.perf_counter(), time.time()
            buf += chunk
            while True:
                idx = buf.find("\n")
                if idx < 0:
                    break
                line, buf = buf[:idx], buf[idx + 1:]
                line = line.strip("\r").strip()
                if line:
                    self._q.put(LogLine(t=now, text=line, wall=wall))

    # -- consumer API -------------------------------------------------------
    def poll_error(self) -> Optional[BaseException]:
        err, self._error = self._error, None
        return err

    def drain(self) -> List[LogLine]:
        """Take everything queued right now (used to clear stale lines)."""
        out: List[LogLine] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                return out

    def get(self, timeout: float) -> Optional[LogLine]:
        """Next line, or None if nothing arrived within ``timeout`` seconds."""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


class SerialLogSource(LogSource):
    """Detector log over a COM port / UART."""

    def __init__(self, port: str, baud: int = 115200, read_timeout: float = 0.01):
        super().__init__()
        self.port = port
        self.baud = baud
        self.read_timeout = read_timeout
        self._ser = None

    def _open(self) -> None:
        try:
            import serial  # noqa: WPS433 — optional dependency
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                t("pyserial is not installed. Install it with: pip install pyserial")
            ) from exc
        # small timeout: we poll often so a line is timestamped close to its arrival
        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=self.read_timeout)
        except serial.SerialException as exc:
            # A COM port admits exactly one process. The usual cause is a `listen`
            # session or a terminal monitor still holding it — say so instead of
            # dumping a traceback about access rights.
            if "denied" in str(exc).lower() or "access" in str(exc).lower():
                raise RuntimeError(
                    t("Port {port} is busy — another program is holding it "
                      "(a 'listen' session or a serial terminal). Close it and retry.",
                      port=self.port)
                ) from exc
            raise RuntimeError(
                t("Cannot open port {port}: {err}", port=self.port, err=str(exc))
            ) from exc

    def _read_chunk(self) -> str:
        n = self._ser.in_waiting or 1
        data = self._ser.read(n)
        return data.decode("utf-8", errors="replace") if data else ""

    def _close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None


class FileLogSource(LogSource):
    """Follow a growing log file (tail -f)."""

    def __init__(self, path: str, poll_s: float = 0.01, from_start: bool = False):
        super().__init__()
        self.path = path
        self.poll_s = poll_s
        self.from_start = from_start
        self._fh = None

    def _open(self) -> None:
        self._fh = open(self.path, "r", encoding="utf-8", errors="replace")
        if not self.from_start:
            self._fh.seek(0, 2)   # skip whatever is already there

    def _read_chunk(self) -> str:
        data = self._fh.read()
        if not data:
            time.sleep(self.poll_s)
        return data

    def _close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None


def list_serial_ports() -> List[dict]:
    """Available COM ports (empty if pyserial is missing)."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [{"device": p.device, "description": p.description or ""}
            for p in list_ports.comports()]
