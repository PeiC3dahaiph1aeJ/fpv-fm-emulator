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

echo [1/4] Creating the .venv environment ...
python -m venv .venv
if errorlevel 1 (
    echo [!] Failed to create .venv
    pause
    exit /b 1
)

echo [2/4] Upgrading pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip

echo [3/4] Installing the core dependencies (numpy, PyYAML, PySide6) ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [!] Dependency installation FAILED - see the errors above.
    echo     Nothing was installed: pip aborts the whole requirements file when a
    echo     single entry cannot be resolved. The GUI will not start like this.
    echo     Fix the error - usually an unsupported Python version - and run setup.bat again.
    echo.
    pause
    exit /b 1
)

echo [4/4] Installing the hardware dependencies (pyadi-iio, pylibiio) ...
".venv\Scripts\python.exe" -m pip install -r requirements-hw.txt
if errorlevel 1 (
    echo.
    echo [i] The hardware dependencies were NOT installed - this is NOT fatal.
    echo     The GUI, the CLI and the file/null backends work without them;
    echo     only real transmission through Pluto+ needs pyadi-iio + pylibiio.
    echo.
)

echo.
echo ============================================================
echo   Done. Start the GUI by double-clicking run_gui.bat
echo ============================================================
echo.
echo   (SoapySDR for HackRF/Lime — separately, see requirements-hw.txt)
echo.
pause
