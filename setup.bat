@echo off
chcp 65001 >nul
cd /d "%~dp0"
title FPV FM emulator — setup
echo ============================================================
echo   FPV FM emulator — setup (run once)
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python not found in PATH.
    echo     Install Python 3.9+ from python.org and tick "Add to PATH".
    echo.
    pause
    exit /b 1
)

echo [1/3] Creating the .venv environment ...
python -m venv .venv
if errorlevel 1 (
    echo [!] Failed to create .venv
    pause
    exit /b 1
)

echo [2/3] Upgrading pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip

echo [3/3] Installing dependencies (numpy, PySide6, pyadi-iio ...) ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
".venv\Scripts\python.exe" -m pip install -r requirements-hw.txt

echo.
echo ============================================================
echo   Done. Start the GUI by double-clicking run_gui.bat
echo ============================================================
echo.
echo   (SoapySDR for HackRF/Lime — separately, see requirements-hw.txt)
echo.
pause
