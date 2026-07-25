@echo off
chcp 65001 >nul
cd /d "%~dp0"
title FPV FM-емулятор — встановлення
echo ============================================================
echo   FPV FM-емулятор — встановлення (один раз)
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python не знайдено в PATH.
    echo     Встановіть Python 3.9+ з python.org і відзначте "Add to PATH".
    echo.
    pause
    exit /b 1
)

echo [1/3] Створення оточення .venv ...
python -m venv .venv
if errorlevel 1 (
    echo [!] Не вдалося створити .venv
    pause
    exit /b 1
)

echo [2/3] Оновлення pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip

echo [3/3] Встановлення залежностей (numpy, PySide6, pyadi-iio ...) ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
".venv\Scripts\python.exe" -m pip install -r requirements-hw.txt

echo.
echo ============================================================
echo   Готово. Запускайте GUI подвійним кліком на run_gui.bat
echo ============================================================
echo.
echo   (SoapySDR для HackRF/Lime — окремо, див. requirements-hw.txt)
echo.
pause
