@echo off
REM Wahoo Bridge GUI Launcher (Windows)
REM Double-click to open status monitor!

cd /d "%~dp0"

REM Check if Python is available
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found!
    pause
    exit /b 1
)

REM Launch GUI
python wahoo_bridge_gui.py
