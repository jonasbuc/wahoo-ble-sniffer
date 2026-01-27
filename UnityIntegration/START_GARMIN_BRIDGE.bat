@echo off
REM Garmin Speed Sensor Bridge Launcher (Windows)
REM Connects Garmin Speed Sensor 2 to Unity

cd /d "%~dp0"

echo ===============================================================
echo.
echo        GARMIN SPEED SENSOR to UNITY BRIDGE
echo.
echo ===============================================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found!
    echo.
    echo Please install Python from:
    echo https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM Check if venv exists
if not exist "..\\.venv" (
    echo ERROR: Python environment not installed!
    echo.
    echo Please run INSTALL.bat first
    echo.
    pause
    exit /b 1
)

REM Activate venv
call ..\\.venv\\Scripts\\activate.bat

REM Check dependencies
echo Checking dependencies...
python -c "import bleak, websockets" 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Required packages not installed!
    echo.
    echo Please run INSTALL.bat first
    echo.
    pause
    exit /b 1
)

echo OK: Dependencies installed
echo.

echo INSTRUCTIONS:
echo.
echo 1. Wake up your Garmin Speed Sensor 2:
echo    - Spin the wheel or move the sensor
echo    - LED should blink red/green
echo.
echo 2. Keep Unity ready with BikeMovementController
echo.
echo 3. Bridge will auto-connect when sensor is active
echo.
echo Press Ctrl+C to stop
echo.
echo ===============================================================
echo.

REM Start the bridge
python wahoo_unity_bridge.py

REM Keep window open on error
if %errorlevel% neq 0 (
    echo.
    echo Bridge stopped with error
    pause
)
