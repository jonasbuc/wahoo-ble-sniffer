@echo off
chcp 65001 >nul 2>&1
:: ----------------------------------------------------------------
::  Wahoo BLE Bridge (Windows)
::  Double-click to start the real BLE bridge.
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

echo.
echo   Wahoo BLE Bridge
echo   Scanning for Wahoo BLE devices ...
echo   WebSocket server - ws://localhost:8765
echo   TIP: Open START_GUI.bat to see live data.
echo.

"%PYTHON%" UnityIntegration\python\bike_bridge.py --live

echo.
echo   Bridge stoppet.
pause
