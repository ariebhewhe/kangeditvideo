@echo off
rem KangEdit launcher: creates the virtual environment on first run.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Setting up KangEdit for the first time...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || goto :error
)
start "" ".venv\Scripts\pythonw.exe" main.py %*
exit /b 0

:error
echo Setup failed. Make sure Python 3.10+ is installed and on PATH.
pause
exit /b 1
