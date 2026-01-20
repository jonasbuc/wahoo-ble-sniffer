@echo off
REM Wahoo Unity Bridge Starter (Windows)
REM Double-click this file to start the bridge!

cd /d "%~dp0"

echo ============================================================
echo   Wahoo BLE to Unity Bridge
echo ============================================================
echo.
echo Starting Python bridge...
echo.

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Python not found!
    echo.
    echo Download Python from: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM Check if dependencies are installed
python -c "import bleak, websockets" >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Dependencies missing!
    echo Installing bleak and websockets...
    echo.
    pip install bleak websockets
    echo.
)

echo OK: Dependencies installed
echo.
echo Scanning for KICKR and TICKR...
echo TIP: Pedal to wake up your KICKR!
echo.
echo WebSocket server starting on ws://localhost:8765
echo.
echo ════════════════════════════════════════════════════════════
echo.

REM Start bridge
python wahoo_unity_bridge.py

echo.
echo Bridge stopped.
pause
