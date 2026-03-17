@echo off
REM Wahoo Bridge GUI Launcher (Windows)
REM Double-click to open status monitor!

cd /d "%~dp0"

REM Prefer repository virtualenv if present
set "REPO_ROOT=%~dp0..\.."
set "VENV_PY=%REPO_ROOT%\.venv\Scripts\python.exe"
set "PYCMD=python"
if exist "%VENV_PY%" (
    set "PYCMD=%VENV_PY%"
)

REM Check if Python is available
"%PYCMD%" --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found!
    pause
    exit /b 1
)

REM Launch GUI (use canonical python/ copy)
"%PYCMD%" "%~dp0..\python\wahoo_bridge_gui.py" --url ws://localhost:8765
