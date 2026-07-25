"""PySide6 GUI: manual control + scenario runner for the FPV FM video emulator.

Run:  python -m gui.app     (or  python run_gui.py)
"""
from __future__ import annotations

import os
import sys
import threading

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6 import QtCore, QtGui, QtWidgets

from fpv_emulator.backends import TxConfig, make_sink
from fpv_emulator.bands import load_band_table
from fpv_emulator.config import list_scenarios, load_scenario
from fpv_emulator.fm import occupied_bandwidth_hz
from fpv_emulator.i18n import LANGUAGES, detect_language, get_language, set_language, t
from fpv_emulator.scenarios import ScenarioRunner
from fpv_emulator.video import (
    STANDARDS,
    get_standard,
    is_color_pattern,
    list_all_patterns,
    render_pattern_image,
)


# ---------------------------------------------------------------------------
#  Background scenario worker (runs in its own QThread)
# ---------------------------------------------------------------------------
class ScenarioWorker(QtCore.QObject):
    event = QtCore.Signal(dict)
    finished = QtCore.Signal()
    error = QtCore.Signal(str)

    def __init__(self, sink, band_table, scenario):
        super().__init__()
        self.sink = sink
        self.bands = band_table
        self.scenario = scenario
        self._stop = threading.Event()
        self.runner = None

    @QtCore.Slot()
    def run(self):
        self.runner = ScenarioRunner(self.sink, self.bands, on_event=self.event.emit)
        try:
            self.runner.run(self.scenario, self._stop)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
        finally:
            try:
                self.sink.close()
            except Exception:
                pass
            self.finished.emit()

    def set_gain(self, gain_db: float):
        # request the desired power; the worker thread applies it (do not touch libiio here)
        if self.runner is not None:
            self.runner.set_live_gain(gain_db)

    def stop(self):
        self._stop.set()


# ---------------------------------------------------------------------------
#  Pattern preview widget
# ---------------------------------------------------------------------------
class PatternPreview(QtWidgets.QLabel):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(320, 240)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setStyleSheet("background:#101014; border:1px solid #333;")

    def show_pattern(self, pattern: str):
        arr = render_pattern_image(pattern, 240, 320)
        img = np.ascontiguousarray((arr * 255).astype(np.uint8))
        if img.ndim == 3:  # RGB (color pattern)
            h, w, _ = img.shape
            qimg = QtGui.QImage(img.data, w, h, w * 3, QtGui.QImage.Format_RGB888)
        else:              # luma (monochrome)
            h, w = img.shape
            qimg = QtGui.QImage(img.data, w, h, w, QtGui.QImage.Format_Grayscale8)
        self.setPixmap(QtGui.QPixmap.fromImage(qimg).scaled(
            self.width(), self.height(),
            QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))


# ---------------------------------------------------------------------------
#  Main window
# ---------------------------------------------------------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        # language must be active BEFORE any widget is created
        self.settings = QtCore.QSettings("fpv-fm-emulator", "gui")
        saved = str(self.settings.value("language", "") or "")
        set_language(saved if saved in LANGUAGES else detect_language())

        self.bands = load_band_table()
        self.thread = None
        self.worker = None

        self._build_ui()

    # -- ui construction (re-runnable: used to retranslate in place) ---------
    def _build_ui(self, state: dict | None = None):
        self.setWindowTitle(
            t("FPV FM emulator for Pluto+ · test signal for FPV detectors"))

        central = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(central)
        root.addLayout(self._build_left(), 0)
        root.addLayout(self._build_right(), 1)
        self.setCentralWidget(central)   # old central widget is deleteLater()'d

        self._refresh_channels()
        if state is None:
            self.cb_pattern.setCurrentText("color_bars")   # default: realistic FPV profile
        else:
            self._apply_state(state)
        self._update_readouts()
        self._set_running(self.thread is not None)
        self._on_backend_changed(self.cb_backend.currentText())   # initial SDR field state
        if state is not None:
            # restore the log last: rebuilding may have appended hints of its own
            self.log.setPlainText(state.get("log", ""))
            self.log.moveCursor(QtGui.QTextCursor.End)

    # -- left: controls -----------------------------------------------------
    def _build_left(self) -> QtWidgets.QVBoxLayout:
        col = QtWidgets.QVBoxLayout()

        # backend
        gb_be = QtWidgets.QGroupBox(t("Output"))
        f = QtWidgets.QFormLayout(gb_be)
        self.cb_backend = QtWidgets.QComboBox()
        self.cb_backend.addItems(["pluto", "soapy", "null", "file"])
        self.cb_backend.currentTextChanged.connect(self._on_backend_changed)
        self.ed_uri = QtWidgets.QLineEdit("ip:192.168.2.1")
        self.ed_device = QtWidgets.QLineEdit("driver=hackrf")   # SoapySDR args
        self.ed_file = QtWidgets.QLineEdit("out.iq")
        self.cb_lang = QtWidgets.QComboBox()
        for code, label in LANGUAGES.items():
            self.cb_lang.addItem(label, code)
        i_lang = self.cb_lang.findData(get_language())
        if i_lang >= 0:
            self.cb_lang.setCurrentIndex(i_lang)
        self.cb_lang.currentIndexChanged.connect(self._on_language_changed)
        f.addRow(t("Backend:"), self.cb_backend)
        f.addRow(t("URI Pluto:"), self.ed_uri)
        f.addRow(t("SDR (soapy):"), self.ed_device)
        f.addRow(t("File (file):"), self.ed_file)
        f.addRow(t("Language:"), self.cb_lang)
        hb_probe = QtWidgets.QHBoxLayout()
        self.btn_probe = QtWidgets.QPushButton(t("Probe Pluto"))
        self.btn_probe.clicked.connect(self._on_probe)
        self.btn_devices = QtWidgets.QPushButton(t("List SDRs"))
        self.btn_devices.clicked.connect(self._on_list_devices)
        hb_probe.addWidget(self.btn_probe)
        hb_probe.addWidget(self.btn_devices)
        f.addRow(hb_probe)
        col.addWidget(gb_be)

        # frequency
        gb_fr = QtWidgets.QGroupBox(t("Frequency"))
        f = QtWidgets.QFormLayout(gb_fr)
        self.cb_band = QtWidgets.QComboBox()
        self.cb_band.addItem(t("— all —"), None)
        for b in self.bands.list_bands():
            self.cb_band.addItem(b, b)
        self.cb_band.currentIndexChanged.connect(self._refresh_channels)
        self.cb_channel = QtWidgets.QComboBox()
        self.cb_channel.currentIndexChanged.connect(self._on_channel_changed)
        self.sp_freq = QtWidgets.QDoubleSpinBox()
        self.sp_freq.setRange(50.0, 6000.0)
        self.sp_freq.setDecimals(1)
        self.sp_freq.setSuffix(" " + t("MHz"))
        self.sp_freq.setValue(5658.0)
        self.cb_hw = QtWidgets.QComboBox()
        self.cb_hw.addItems(["hacked", "stock"])
        self.cb_hw.currentIndexChanged.connect(self._update_readouts)
        self.sp_freq.valueChanged.connect(self._update_readouts)
        f.addRow(t("Band:"), self.cb_band)
        f.addRow(t("Channel:"), self.cb_channel)
        f.addRow(t("Carrier:"), self.sp_freq)
        f.addRow(t("HW range:"), self.cb_hw)
        col.addWidget(gb_fr)

        # signal
        gb_sig = QtWidgets.QGroupBox(t("Signal"))
        f = QtWidgets.QFormLayout(gb_sig)
        self.cb_std = QtWidgets.QComboBox()
        self.cb_std.addItems(list(STANDARDS.keys()))
        self.cb_pattern = QtWidgets.QComboBox()
        self.cb_pattern.addItems(list_all_patterns())   # luma + color
        self.cb_pattern.currentTextChanged.connect(self._on_pattern_changed)
        self.sp_fs = QtWidgets.QDoubleSpinBox()
        self.sp_fs.setRange(1.0, 61.44)
        self.sp_fs.setDecimals(2)
        self.sp_fs.setSuffix(" MSPS")
        self.sp_fs.setValue(20.0)   # default for realistic FPV (color + wide bandwidth)
        self.sp_fs.valueChanged.connect(self._update_readouts)
        self.sp_dev = QtWidgets.QDoubleSpinBox()
        self.sp_dev.setRange(0.1, 30.0)
        self.sp_dev.setDecimals(2)
        self.sp_dev.setSuffix(" " + t("MHz pp"))
        self.sp_dev.setValue(6.0)
        self.sp_dev.valueChanged.connect(self._update_readouts)
        self.chk_burst = QtWidgets.QCheckBox(t("Color burst"))
        self.cb_std.currentIndexChanged.connect(self._update_readouts)
        f.addRow(t("Standard:"), self.cb_std)
        f.addRow(t("Pattern:"), self.cb_pattern)
        f.addRow(t("Sample rate:"), self.sp_fs)
        f.addRow(t("Deviation:"), self.sp_dev)
        f.addRow(self.chk_burst)
        col.addWidget(gb_sig)

        # power
        gb_pw = QtWidgets.QGroupBox(t("Power (tx_hardwaregain)"))
        v = QtWidgets.QVBoxLayout(gb_pw)
        self.sl_gain = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.sl_gain.setRange(-89, 0)
        self.sl_gain.setValue(-10)
        self.lbl_gain = QtWidgets.QLabel(t("{v} dB", v=-10))
        self.sl_gain.valueChanged.connect(
            lambda x: (self.lbl_gain.setText(t("{v} dB", v=x)), self._live_gain(x)))
        v.addWidget(self.sl_gain)
        v.addWidget(self.lbl_gain)
        col.addWidget(gb_pw)

        # mode + start/stop
        gb_run = QtWidgets.QGroupBox(t("Mode"))
        v = QtWidgets.QVBoxLayout(gb_run)
        self.cb_mode = QtWidgets.QComboBox()
        self.cb_mode.addItem(t("Manual carrier (static)"), None)
        for name in list_scenarios():
            self.cb_mode.addItem(t("Scenario: {name}", name=name), name)
        v.addWidget(self.cb_mode)
        hb = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton(t("▶ Start"))
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop = QtWidgets.QPushButton(t("■ Stop"))
        self.btn_stop.clicked.connect(self._on_stop)
        hb.addWidget(self.btn_start)
        hb.addWidget(self.btn_stop)
        v.addLayout(hb)
        col.addWidget(gb_run)

        col.addStretch(1)
        return col

    # -- right: preview + readouts + log ------------------------------------
    def _build_right(self) -> QtWidgets.QVBoxLayout:
        col = QtWidgets.QVBoxLayout()
        self.preview = PatternPreview()
        col.addWidget(self.preview)

        self.lbl_read = QtWidgets.QLabel()
        self.lbl_read.setStyleSheet("font-family:monospace;")
        self.lbl_read.setWordWrap(True)
        col.addWidget(self.lbl_read)

        col.addWidget(QtWidgets.QLabel(t("Event log:")))
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setStyleSheet("font-family:monospace; font-size:11px;")
        col.addWidget(self.log, 1)

        self.status = self.statusBar()
        return col

    # -- language -----------------------------------------------------------
    def _capture_state(self) -> dict:
        """Snapshot every user-set value so a rebuild does not lose it."""
        return {
            "backend": self.cb_backend.currentText(),
            "uri": self.ed_uri.text(),
            "device": self.ed_device.text(),
            "file": self.ed_file.text(),
            "band_index": self.cb_band.currentIndex(),
            "channel": self.cb_channel.currentData(),
            "freq": self.sp_freq.value(),
            "hw": self.cb_hw.currentText(),
            "standard": self.cb_std.currentText(),
            "pattern": self.cb_pattern.currentText(),
            "fs": self.sp_fs.value(),
            "dev": self.sp_dev.value(),
            "burst": self.chk_burst.isChecked(),
            "gain": self.sl_gain.value(),
            "mode_index": self.cb_mode.currentIndex(),
            "log": self.log.toPlainText(),
        }

    def _apply_state(self, state: dict):
        """Restore a snapshot taken by :meth:`_capture_state` without firing handlers."""
        for widget, value in (
            (self.cb_backend, state.get("backend")),
            (self.cb_hw, state.get("hw")),
            (self.cb_std, state.get("standard")),
            (self.cb_pattern, state.get("pattern")),
        ):
            if value:
                widget.blockSignals(True)
                widget.setCurrentText(value)
                widget.blockSignals(False)
        self.ed_uri.setText(state.get("uri", ""))
        self.ed_device.setText(state.get("device", ""))
        self.ed_file.setText(state.get("file", ""))

        band_index = int(state.get("band_index", 0) or 0)
        if 0 <= band_index < self.cb_band.count():
            self.cb_band.blockSignals(True)
            self.cb_band.setCurrentIndex(band_index)
            self.cb_band.blockSignals(False)
            self._refresh_channels()
        chan = state.get("channel")
        if chan is not None:
            i = self.cb_channel.findData(chan)
            if i >= 0:
                self.cb_channel.blockSignals(True)
                self.cb_channel.setCurrentIndex(i)
                self.cb_channel.blockSignals(False)

        for spin, value in ((self.sp_freq, state.get("freq")),
                            (self.sp_fs, state.get("fs")),
                            (self.sp_dev, state.get("dev"))):
            if value is not None:
                spin.blockSignals(True)
                spin.setValue(float(value))
                spin.blockSignals(False)

        self.chk_burst.setChecked(bool(state.get("burst")))

        gain = int(state.get("gain", self.sl_gain.value()))
        self.sl_gain.blockSignals(True)
        self.sl_gain.setValue(gain)
        self.sl_gain.blockSignals(False)
        self.lbl_gain.setText(t("{v} dB", v=gain))

        mode_index = int(state.get("mode_index", 0) or 0)
        if 0 <= mode_index < self.cb_mode.count():
            self.cb_mode.setCurrentIndex(mode_index)

        self.preview.show_pattern(self.cb_pattern.currentText())

    def _on_language_changed(self, index: int):
        code = self.cb_lang.itemData(index)
        if not code or code == get_language():
            return
        self.settings.setValue("language", code)
        set_language(code)
        state = self._capture_state()
        # rebuild outside this signal handler — the combo itself is about to be replaced
        QtCore.QTimer.singleShot(0, lambda: self._build_ui(state))

    # -- helpers ------------------------------------------------------------
    def _refresh_channels(self):
        self.cb_channel.blockSignals(True)
        self.cb_channel.clear()
        band = self.cb_band.currentData()
        chans = (self.bands.channels_in_band(band) if band
                 else sorted((c for g in self.bands.groups()
                              for c in self.bands.channels_in_group(g)),
                             key=lambda c: c.freq_hz))
        for ch in chans:
            # userData = unambiguous 'band:channel' key (channel names repeat across bands)
            self.cb_channel.addItem(f"{ch.name}  ({ch.freq_mhz:.0f})", f"{ch.band}:{ch.name}")
        self.cb_channel.blockSignals(False)
        self._on_channel_changed()

    def _on_channel_changed(self):
        key = self.cb_channel.currentData()
        if key:
            self.sp_freq.blockSignals(True)
            self.sp_freq.setValue(self.bands.channel(key).freq_mhz)
            self.sp_freq.blockSignals(False)
            self._update_readouts()

    def _on_pattern_changed(self, pattern: str):
        self.preview.show_pattern(pattern)
        if is_color_pattern(pattern) and self.sp_fs.value() < 18.0:
            # realistic FPV: 4.43 MHz subcarrier + wide bandwidth -> fs 20 MSPS
            self.sp_fs.setValue(20.0)
            self._log(t("[info] Color pattern — fs raised to 20 MSPS "
                        "(subcarrier + wide bandwidth)."))
        self._update_readouts()   # bandwidth depends on the pattern (color is wider)

    def _current_signal(self) -> dict:
        return {
            "standard": self.cb_std.currentText(),
            "pattern": self.cb_pattern.currentText(),
            "sample_rate": self.sp_fs.value() * 1e6,
            "deviation_pp_hz": self.sp_dev.value() * 1e6,
            "gain_db": float(self.sl_gain.value()),
            "color_burst": self.chk_burst.isChecked(),
        }

    def _update_readouts(self):
        fs = self.sp_fs.value() * 1e6
        dev = self.sp_dev.value() * 1e6
        std = get_standard(self.cb_std.currentText())
        n = int(round(std.line_us * 1e-6 * fs)) * std.total_lines
        mb = n * 4 / 1e6  # complex int16 = 4 bytes/sample
        # color patterns are wider because of the subcarrier — computed as in signal_gen
        pattern = self.cb_pattern.currentText()
        video_bw = (std.color_subcarrier_hz + 1.0e6) if is_color_pattern(pattern) else 1.5e6
        bw = occupied_bandwidth_hz(dev, video_bw)
        freq = self.sp_freq.value() * 1e6
        ok, warn = self.bands.check_reachable(freq, self.cb_hw.currentText())
        peak = dev / 2
        alias = ("  " + t("⚠ ALIASING (raise fs)")) if peak > 0.45 * fs else ""
        lines = [
            t("Carrier:     {mhz} MHz", mhz=f"{freq/1e6:.1f}"),
            t("Frame:       {n} samples · {ms} ms · buffer ~{mb} MB",
              n=n, ms=f"{std.frame_period_s*1e3:.1f}", mb=f"{mb:.2f}"),
            t("Occupied bandwidth ~{bw} MHz (fs={fs} MSPS){alias}",
              bw=f"{bw/1e6:.1f}", fs=f"{fs/1e6:.1f}", alias=alias),
            t("Line rate:   {khz} kHz", khz=f"{std.line_rate_hz/1e3:.2f}"),
        ]
        if not ok:
            lines.append(f"⚠ {warn}")
        self.lbl_read.setText("\n".join(lines))

    def _build_scenario(self) -> dict:
        mode = self.cb_mode.currentData()
        if mode is None:
            return {
                "name": "GUI-static",
                "type": "static",
                "signal": self._current_signal(),
                "static": {"freq_mhz": self.sp_freq.value(), "hold_s": 0},
            }
        return load_scenario(list_scenarios()[mode])

    def _make_sink(self, fs: float):
        kind = self.cb_backend.currentText()
        cfg = TxConfig(fs=fs, freq_hz=self.sp_freq.value() * 1e6,
                       gain_db=float(self.sl_gain.value()),
                       uri=self.ed_uri.text(), rf_bw_hz=min(fs, 20e6),
                       device=self.ed_device.text())
        return make_sink(kind, cfg, file_path=self.ed_file.text())

    def _on_backend_changed(self, name: str):
        soapy = (name == "soapy")
        self.ed_device.setEnabled(soapy)
        self.btn_devices.setEnabled(soapy)
        if name == "null":
            self._log(t("[WARN] backend = null — dry run, nothing goes on air."))
        elif soapy:
            self._log(t("[info] backend = soapy — set the device in the «SDR (soapy)» field "
                        "(e.g. driver=hackrf|lime|uhd). «List SDRs» shows what is available."))

    def _on_list_devices(self):
        from fpv_emulator.backends import soapy_enumerate
        devs = soapy_enumerate()
        if not devs:
            self._log(t("No SoapySDR devices found (or SoapySDR is not installed)."))
            return
        self._log(t("SDRs found (SoapySDR):"))
        for d in devs:
            self._log(f"  driver={d.get('driver','?')}  {d.get('label','')}")

    # -- actions ------------------------------------------------------------
    def _on_probe(self):
        from fpv_emulator.probe import probe
        self.status.showMessage(t("Probing Pluto…"))
        QtWidgets.QApplication.processEvents()
        res = probe(uri=self.ed_uri.text())
        self._log(res.summary())
        if res.inferred_preset:
            self.cb_hw.setCurrentText(res.inferred_preset)
        self.status.showMessage(t("Probe finished"), 4000)

    def _on_start(self):
        if self.thread is not None:
            return
        try:
            scenario = self._build_scenario()
            fs = float(scenario.get("signal", {}).get("sample_rate", self.sp_fs.value() * 1e6))
            sink = self._make_sink(fs)
        except Exception as exc:  # noqa: BLE001
            self._log(t("[ERROR] {msg}", msg=exc))
            return

        self.thread = QtCore.QThread()
        self.worker = ScenarioWorker(sink, self.bands, scenario)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.event.connect(self._on_event)
        self.worker.error.connect(lambda m: self._log(t("[ERROR] {msg}", msg=m)))
        self.worker.finished.connect(self._on_finished)
        self.thread.start()
        self._set_running(True)
        self._log(t("— start: {name} —", name=t(scenario.get("name", ""))))

    def _on_stop(self):
        if self.worker:
            self.worker.stop()
            self.status.showMessage(t("Stopping…"))

    def _on_finished(self):
        if self.thread:
            self.thread.quit()
            self.thread.wait(2000)
        self.thread = None
        self.worker = None
        self._set_running(False)
        self.status.showMessage(t("Stopped"), 3000)

    def _live_gain(self, gain_db: int):
        # live power change while transmitting — routed through the worker thread
        if self.worker:
            self.worker.set_gain(float(gain_db))

    def _on_event(self, e: dict):
        from fpv_emulator.cli import _fmt_event
        self._log(_fmt_event(e))
        if e.get("action") in ("tune", "power"):
            self.status.showMessage(t(
                "{ch} @ {mhz} MHz, {gain} dB",
                ch=e.get("channel", ""),
                mhz=f"{e.get('freq_mhz', 0):.1f}",
                gain=e.get("gain_db", "")))

    def _log(self, msg: str):
        self.log.appendPlainText(msg)

    def _set_running(self, running: bool):
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        for w in (self.cb_backend, self.ed_uri, self.ed_device, self.cb_mode,
                  self.sp_fs, self.cb_std, self.sp_dev, self.cb_lang):
            w.setEnabled(not running)

    def closeEvent(self, ev: QtGui.QCloseEvent):
        self._on_stop()
        if self.thread:
            self.thread.quit()
            self.thread.wait(2000)
        ev.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.resize(920, 640)
    win.show()
    win.preview.show_pattern(win.cb_pattern.currentText())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
