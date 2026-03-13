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

REM Check if dependencies are installed
REM Check if dependencies are installed; install via the chosen Python if missing
"%PYCMD%" -c "import bleak, websockets" >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Dependencies missing for %PYCMD%!
    echo Installing bleak and websockets into environment used by %PYCMD%...
    echo.
    "%PYCMD%" -m pip install --upgrade pip
    "%PYCMD%" -m pip install bleak websockets
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

REM Start bridge first, then GUI
REM Start canonical bridge in a new window so it runs independently
start "Wahoo Bridge" "%PYCMD%" "%~dp0..\python\wahoo_unity_bridge.py" --live

REM Give the bridge a moment to initialize, then start the GUI monitor in a separate window
timeout /t 2 /nobreak >nul
start "Wahoo Bridge GUI" "%PYCMD%" "%~dp0..\python\wahoo_bridge_gui.py" --live

echo.
echo Bridge stopped.
pause
