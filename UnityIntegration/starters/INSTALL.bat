@echo off
REM Wahoo Bridge Auto-Installer (Windows)
REM Double-click to install everything automatically!

cd /d "%~dp0..\.."

echo ============================================================
echo   Wahoo Bridge - Auto Installer
echo ============================================================
echo.
echo This will install everything you need!
echo.

REM ── [1/5] Find Python ────────────────────────────────────────
echo [1/5] Checking Python...
set "PY="
python  --version >nul 2>&1 && set "PY=python"
if not defined PY (
    python3 --version >nul 2>&1 && set "PY=python3"
)
if not defined PY (
    echo ERROR: Python not found!
    echo Please install Python 3.9 or newer from https://www.python.org/downloads/
    echo During install: check "Add Python to PATH"
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('"%PY%" --version') do set PYTHON_VERSION=%%i
echo OK: Found Python %PYTHON_VERSION% via "%PY%"
echo.

REM ── [2/5] Create virtual environment ─────────────────────────
echo [2/5] Creating virtual environment...
if exist .venv\Scripts\python.exe (
    echo OK: Virtual environment already exists
) else (
    if exist .venv rmdir /s /q .venv
    "%PY%" -m venv .venv
    if not exist .venv\Scripts\python.exe (
        echo ERROR: Failed to create virtual environment!
        pause
        exit /b 1
    )
    echo OK: Virtual environment created
)
echo.

REM ── [3/5] Install dependencies ───────────────────────────────
echo [3/5] Installing dependencies...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: pip install failed - check your internet connection.
    pause
    exit /b 1
)
echo OK: Dependencies installed
echo.

REM ── [4/5] Verify ─────────────────────────────────────────────
echo [4/5] Verifying installation...
.venv\Scripts\python.exe -c "import bleak, websockets" >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Installation verification failed!
    pause
    exit /b 1
)
echo OK: All packages verified
echo.

REM ── [5/5] Done ───────────────────────────────────────────────
echo [5/5] Setup complete
echo.
echo ============================================================
echo   INSTALLATION COMPLETE!
echo ============================================================
echo.
echo Next steps:
echo 1. Double-click START_WAHOO_BRIDGE.bat to start the bridge
echo 2. Double-click START_GUI.bat in a separate window to see live data
echo 3. Start Unity and connect!
echo.
echo Happy cycling!
echo.
pause
