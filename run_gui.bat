@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [!] Environment not found. Run setup.bat first ^(double-click^).
    pause
    exit /b 1
)

rem Fail loudly here rather than in a windowless pythonw process: if setup.bat did not
rem finish, PySide6 is missing and the GUI cannot even build its own error dialog.
".venv\Scripts\python.exe" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo [!] PySide6 does not import from .venv - the GUI cannot start.
    echo     Run setup.bat again and read its output: the dependency installation failed.
    pause
    exit /b 1
)

rem Start the GUI without a console (pythonw). Startup errors are shown in a dialog box
rem and written to startup_error.log next to this file.
start "" ".venv\Scripts\pythonw.exe" run_gui.py
