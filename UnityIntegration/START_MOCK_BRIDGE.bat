@echo off
REM Wahoo Mock Bridge Starter (Windows)
REM Test uden hardware - dobbeltklik på denne fil!

cd /d "%~dp0"

echo ============================================================
echo   🎮 Wahoo MOCK Bridge (Test uden hardware)
echo ============================================================
echo.
echo Dette er til test/udvikling uden KICKR!
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
python -c "import websockets" >nul 2>&1
if %errorlevel% neq 0 (
    echo ⚠️  Websockets mangler!
    echo Installerer websockets...
    echo.
    pip install websockets
    echo.
)

echo ✓ Dependencies OK
echo.
echo 🌐 Mock WebSocket server starter på ws://localhost:8765
echo 📊 Sender simulerede cykeldata...
echo.
echo Dette kan bruges til at udvikle Unity spillet uden at
echo skulle træde konstant på cyklen! 😄
echo.
echo ════════════════════════════════════════════════════════════
echo.

REM Start mock bridge
python mock_wahoo_bridge.py

echo.
echo Mock bridge stoppet.
pause
