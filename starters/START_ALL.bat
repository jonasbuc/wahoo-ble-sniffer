@echo off
chcp 65001 >nul 2>&1
:: ════════════════════════════════════════════════════════════════
::  Bike VR – Start All Services (Windows)
::  Double-click to start everything!
:: ════════════════════════════════════════════════════════════════

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

"%PYTHON%" starters\launcher.py %*
pause
