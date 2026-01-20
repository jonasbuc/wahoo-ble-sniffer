@echo off
REM Wahoo Bridge Auto-Installer (Windows)
REM Double-click to install everything automatically!

cd /d "%~dp0\.."

echo ============================================================
echo   Wahoo Bridge - Auto Installer
echo ============================================================
echo.
echo This will install everything you need!
echo.

REM Check Python version
echo [1/5] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found!
    echo Please install Python from: https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation!
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version') do set PYTHON_VERSION=%%i
echo OK: Found Python %PYTHON_VERSION%
echo.

REM Create virtual environment
echo [2/5] Creating virtual environment...
if exist .venv (
    echo OK: Virtual environment already exists
) else (
    python -m venv .venv
    echo OK: Virtual environment created
)
echo.

REM Install dependencies
echo [3/5] Installing dependencies...
call .venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
python -m pip install --quiet bleak websockets

echo OK: Dependencies installed
echo.

REM Verify installation
echo [4/5] Verifying installation...
python -c "import bleak, websockets" >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Installation verification failed!
    pause
    exit /b 1
)
echo OK: All packages verified
echo.

REM Setup complete
echo [5/5] Setting up starter scripts...
echo OK: Starter scripts ready
echo.

echo ============================================================
echo   INSTALLATION COMPLETE!
echo ============================================================
echo.
echo Next steps:
echo 1. Go to UnityIntegration folder
echo 2. Double-click START_WAHOO_BRIDGE.bat
echo 3. Start Unity and connect!
echo.
echo Happy cycling! :)
echo.
pause
