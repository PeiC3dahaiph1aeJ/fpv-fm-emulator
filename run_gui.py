#!/usr/bin/env python
"""Запуск GUI.

Найпростіше — подвійний клік по run_gui.bat.
Або з терміналу:  .venv\\Scripts\\python.exe run_gui.py
"""
import sys
import traceback


def _show_error(message: str) -> None:
    """Показати помилку старту діалоговим вікном (а не тихо впасти)."""
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        QtWidgets.QMessageBox.critical(None, "FPV-емулятор — помилка запуску", message)
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
            "Не вдалося запустити GUI:\n\n"
            + traceback.format_exc()
            + "\nПідказка: запустіть setup.bat ще раз або перевірте залежності "
            "(pip install -r requirements.txt)."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
