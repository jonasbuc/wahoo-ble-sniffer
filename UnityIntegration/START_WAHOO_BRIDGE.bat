@echo off
REM Wahoo Unity Bridge Starter (Windows)
REM Dobbeltklik på denne fil for at starte bridge'en!

cd /d "%~dp0"

echo ============================================================
echo   🚴‍♂️ Wahoo BLE to Unity Bridge
echo ============================================================
echo.
echo Starting Python bridge...
echo.

REM Tjek om Python er installeret
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠️  Python ikke fundet!
    echo.
    echo Download Python fra: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

REM Tjek om dependencies er installeret
python -c "import bleak, websockets" >nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠️  Dependencies mangler!
    echo Installerer bleak og websockets...
    echo.
    pip install bleak websockets
    echo.
)

echo ✓ Dependencies OK
echo.
echo 🔍 Scanner efter KICKR og TICKR...
echo 💡 Tips: Træd på pedalerne for at vække KICKR!
echo.
echo 🌐 WebSocket server starter på ws://localhost:8765
echo.
echo ════════════════════════════════════════════════════════════
echo.

REM Start bridge
python wahoo_unity_bridge.py

echo.
echo Bridge stoppet.
pause
