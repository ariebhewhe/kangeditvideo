@echo off
rem KangEdit launcher for Windows: sets up .venv, the Python packages and FFmpeg when needed, then starts.
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo Python 3.10 or newer is required: https://www.python.org/downloads/
    echo Tick "Add python.exe to PATH" in the installer.
    pause
    exit /b 1
)
%PY% install.py --run %*
if errorlevel 1 (
    echo.
    echo Setup failed - see the messages above.
    pause
    exit /b 1
)
exit /b 0
