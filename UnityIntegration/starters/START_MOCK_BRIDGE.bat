@echo off
REM Wahoo Mock Bridge Starter (Windows)
REM Test without hardware - double-click this file!

cd /d "%~dp0"

echo ============================================================
echo   Wahoo MOCK Bridge (Test without hardware)
echo ============================================================
echo.
echo This is for testing/development without hardware!
echo.

REM Check if Python is installed
REM Prefer repository virtualenv (created by INSTALL.bat) if present
set "REPO_ROOT=%~dp0..\.."
set "VENV_PY=%REPO_ROOT%\.venv\Scripts\python.exe"
set "PYCMD=python"
if exist "%VENV_PY%" (
    set "PYCMD=%VENV_PY%"
)

REM Check if Python is available (either venv or system)
"%PYCMD%" --version >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Python not found (neither system Python nor .venv)!
    echo.
    echo Install Python or run INSTALL.bat to create the virtual environment.
    echo.
    pause
    exit /b 1
)

REM Check if dependencies are installed; install via the chosen Python if missing
"%PYCMD%" -c "import websockets" >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: websockets missing for %PYCMD%!
    echo Installing websockets into environment used by %PYCMD%...
    echo.
    "%PYCMD%" -m pip install --upgrade pip
    "%PYCMD%" -m pip install websockets
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

REM Start bridge in mock mode (no BLE hardware needed)
start "Wahoo Mock Bridge" "%PYCMD%" "%~dp0..\python\bike_bridge.py"

REM Give bridge a moment to start, then launch GUI monitor
timeout /t 2 /nobreak >nul
start "Wahoo Bridge GUI" "%PYCMD%" "%~dp0..\python\wahoo_bridge_gui.py" --url ws://localhost:8765

echo.
echo Mock bridge and GUI started.
pause
