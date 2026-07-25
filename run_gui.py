#!/usr/bin/env python
"""GUI launcher.

Easiest way — double-click run_gui.bat.
Or from a terminal:  .venv\\Scripts\\python.exe run_gui.py
"""
import sys
import traceback


def _show_error(message: str) -> None:
    """Show a startup failure in a dialog instead of dying silently."""
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        QtWidgets.QMessageBox.critical(None, "FPV emulator — startup error", message)
    except Exception:
        print(message)


def main() -> None:
    try:
        from gui.app import main as gui_main
        gui_main()
    except SystemExit:
        raise
    except Exception:
        _show_error(
            "Failed to start the GUI:\n\n"
            + traceback.format_exc()
            + "\nHint: run setup.bat again or check the dependencies "
            "(pip install -r requirements.txt)."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
