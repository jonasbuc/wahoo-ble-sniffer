@echo off
chcp 65001 >nul 2>&1
:: ----------------------------------------------------------------
::  Wahoo Bridge GUI Monitor (Windows)
::  Double-click to open the live status window.
::  (Bridge must already be running.)
:: ----------------------------------------------------------------

cd /d "%~dp0\.."

set PYTHON=.venv\Scripts\python.exe

if not exist "%PYTHON%" (
    echo.
    echo   X  Virtual environment ikke fundet!
    echo      Koer INSTALL.bat foerst.
    echo.
    pause
    exit /b 1
)

"%PYTHON%" bridge\wahoo_bridge_gui.py --url ws://localhost:8765
pause
