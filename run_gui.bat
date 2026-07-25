@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [!] Environment not found. Run setup.bat first ^(double-click^).
    pause
    exit /b 1
)

rem Start the GUI without a console (pythonw). Startup errors are shown in a dialog box.
start "" ".venv\Scripts\pythonw.exe" run_gui.py
