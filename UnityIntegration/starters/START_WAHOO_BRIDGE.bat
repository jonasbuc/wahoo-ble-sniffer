@echo off
REM Wahoo Unity Bridge Starter (Windows)
REM Double-click this file to start the bridge!
REM For the GUI monitor, open START_GUI.bat in a separate window.

cd /d "%~dp0"

echo ============================================================
echo   Wahoo BLE to Unity Bridge
echo ============================================================
echo.
echo Starting Python bridge...
echo.

REM Prefer repository virtualenv created by INSTALL.bat
pushd "%~dp0..\.." >nul 2>&1
set "REPO_ROOT=%CD%"
popd >nul 2>&1
set "VENV_PY=%REPO_ROOT%\.venv\Scripts\python.exe"
set "PYCMD=python"
if exist "%VENV_PY%" (
    set "PYCMD=%VENV_PY%"
) else (
    echo NOTE: No .venv found - run INSTALL.bat first for best results.
    echo Falling back to system Python...
    echo.
)

REM Check if Python is available
"%PYCMD%" --version >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Python not found - neither system Python nor .venv!
    echo.
    echo Install Python or run INSTALL.bat to create the virtual environment.
    echo.
    pause
    exit /b 1
)

REM Check if dependencies are installed; install if missing
"%PYCMD%" -c "import bleak, websockets" >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Dependencies missing - installing now...
    echo.
    "%PYCMD%" -m pip install --upgrade pip
    "%PYCMD%" -m pip install bleak websockets
    echo.
)

echo OK: Dependencies installed
echo.
echo Scanning for Wahoo BLE devices...
echo TIP: Make sure your sensor is awake and nearby!
echo.
echo WebSocket server starting on ws://localhost:8765
echo TIP: Open START_GUI.bat in a separate window to see live data.
echo.
echo ============================================================
echo.

REM Start bridge in this window (foreground - keep it open)
"%PYCMD%" "%~dp0..\python\bike_bridge.py" --live

echo.
echo Bridge stopped.
pause
