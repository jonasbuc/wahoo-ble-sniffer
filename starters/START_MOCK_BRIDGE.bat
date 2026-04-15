@echo off
chcp 65001 >nul 2>&1
:: ----------------------------------------------------------------
::  Wahoo MOCK Bridge (Windows)
::  Double-click to start simulated cycling data.
::  No BLE hardware needed!
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
echo   Wahoo MOCK Bridge (test)
echo   Sender simuleret cykeldata ...
echo   WebSocket server - ws://localhost:8765
echo.

"%PYTHON%" UnityIntegration\python\bike_bridge.py

echo.
echo   Mock bridge stoppet.
pause
