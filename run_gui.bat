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
rem
rem numpy is checked too, and it is the one that matters when a .venv has been copied
rem between machines: PySide6 ships stable-ABI wheels that import under any Python, so
rem it passes while numpy - compiled for one exact version - does not. Checking only
rem PySide6 let the GUI start and then die inside numpy's C extensions, which reads as
rem a broken program rather than a mismatched environment.
".venv\Scripts\python.exe" -c "import PySide6, numpy" >nul 2>nul
if errorlevel 1 (
    echo [!] The .venv environment does not work with the Python running it.
    echo.
    echo     Most often this means .venv was COPIED from another computer. It cannot
    echo     be: it hard-codes the path of the Python that built it and holds binaries
    echo     compiled for that exact version.
    echo.
    echo     Fix: delete the .venv folder and run setup.bat here.
    echo.
    pause
    exit /b 1
)

rem Start the GUI without a console (pythonw). Startup errors are shown in a dialog box
rem and written to startup_error.log next to this file.
start "" ".venv\Scripts\pythonw.exe" run_gui.py
