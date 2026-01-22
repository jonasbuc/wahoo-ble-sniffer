@echo off
REM Wahoo Mock Bridge Starter (Windows)
REM Test without hardware - double-click this file!

cd /d "%~dp0"

echo ============================================================
echo   Wahoo MOCK Bridge (Test without hardware)
echo ============================================================
echo.
echo This is for testing/development without KICKR!
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
python -c "import websockets" >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Websockets missing!
    echo Installing websockets...
    echo.
    pip install websockets
    echo.
)

echo OK: Dependencies installed
echo.
echo Mock WebSocket server starting on ws://localhost:8765
echo Sending simulated cycling data...
echo.
echo You can use this to develop your Unity game without
echo having to pedal constantly! :)
echo.
echo ════════════════════════════════════════════════════════════
echo.

REM Start mock bridge
python mock_wahoo_bridge.py

echo.
echo Mock bridge stopped.
pause
