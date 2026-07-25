@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [!] Оточення не знайдено. Спершу запустіть setup.bat ^(подвійний клік^).
    pause
    exit /b 1
)

rem Запуск GUI без консолі (pythonw). Помилки старту покаже діалогове вікно.
start "" ".venv\Scripts\pythonw.exe" run_gui.py
