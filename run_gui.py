#!/usr/bin/env python
"""GUI launcher.

Easiest way — double-click run_gui.bat.
Or from a terminal:  .venv\\Scripts\\python.exe run_gui.py
"""
import os
import sys
import traceback

_LOG_NAME = "startup_error.log"


def _t(text: str) -> str:
    """Translate ``text``; degrade to English if the package itself is broken."""
    try:
        from fpv_emulator.i18n import t
        return t(text)
    except Exception:
        return text


def _resolve_language() -> None:
    """Pick the language exactly the way the GUI does.

    QSettings first (the combo box selection is stored there and a user who picked
    Ukrainian in the GUI has no FPV_LANG set), then the FPV_LANG env var. Every step
    is optional: a broken PySide6 or a broken package must not hide the error.
    """
    try:
        from fpv_emulator.i18n import LANGUAGES, detect_language, set_language
    except Exception:
        return
    saved = ""
    try:
        from PySide6 import QtCore
        saved = str(QtCore.QSettings("fpv-fm-emulator", "gui").value("language", "") or "")
    except Exception:
        saved = ""
    try:
        set_language(saved if saved in LANGUAGES else detect_language())
    except Exception:
        pass


def _show_error(message: str, *, details: str = "", hint: str = "") -> None:
    """Show a startup failure to the user instead of dying silently.

    ``message`` and ``hint`` are English catalog keys, translated here (the language
    can only be resolved once we know the package is importable). ``details`` is raw
    text (a traceback) and is never translated or formatted.

    run_gui.bat starts the GUI with pythonw.exe, which has no console: sys.stdout and
    sys.stderr are None and print() is a no-op. And when PySide6 is itself the thing
    that is broken, a Qt dialog cannot be built either. So the fallback path writes
    startup_error.log next to this script and uses the plain Win32 MessageBoxW.
    """
    _resolve_language()
    title = _t("FPV emulator — startup error")
    parts = [_t(message)]
    if details:
        parts.append(details.strip("\n"))
    if hint:
        parts.append(_t(hint))
    body = "\n\n".join(parts)

    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        QtWidgets.QMessageBox.critical(None, title, body)
        return
    except Exception:
        pass

    # --- no usable Qt: log to a file and use a toolkit-free dialog ---------------
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), _LOG_NAME)
    try:
        with open(log_path, "w", encoding="utf-8") as fh:
            fh.write(body + "\n")
        body += "\n\n" + _t("Details were written to:") + "\n" + log_path
    except Exception:
        pass

    try:
        print(body)  # no-op under pythonw (sys.stdout is None), useful from a console
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, body, title, 0x10)  # MB_ICONERROR
        except Exception:
            pass


def main() -> None:
    try:
        from gui.app import main as gui_main
        gui_main()
    except SystemExit:
        raise
    except Exception:
        _show_error(
            "Failed to start the GUI:",
            details=traceback.format_exc(),
            hint="Hint: run setup.bat again or check the dependencies "
                 "(pip install -r requirements.txt).",
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
