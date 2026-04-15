@echo off
chcp 65001 >nul 2>&1
REM ----------------------------------------------------------------
REM  Bike VR - Full Installer (Windows)
REM  Double-click this file to set up everything.
REM ----------------------------------------------------------------

cd /d "%~dp0.."

echo.
echo   ================================================
echo      Bike VR - Installer
echo   ================================================
echo.

REM -- 1. Find Python ------------------------------------------------
echo   [1/4] Tjekker Python ...
set "PY="
python  --version >nul 2>&1 && set "PY=python"
if not defined PY (
    python3 --version >nul 2>&1 && set "PY=python3"
)
if not defined PY (
    echo   X  Python ikke fundet!
    echo      Installer fra https://www.python.org/downloads/
    echo      Husk at markere "Add Python to PATH"
    pause
    exit /b 1
)
for /f "tokens=2" %%i in ('"%PY%" --version') do set PYTHON_VERSION=%%i
echo   OK  Python %PYTHON_VERSION%
echo.

REM -- 2. Virtual environment -----------------------------------------
echo   [2/4] Opretter virtual environment ...
if exist .venv\Scripts\python.exe (
    echo   OK  .venv eksisterer allerede
) else (
    if exist .venv rmdir /s /q .venv
    "%PY%" -m venv .venv
    if not exist .venv\Scripts\python.exe (
        echo   X  Kunne ikke oprette .venv!
        pause
        exit /b 1
    )
    echo   OK  .venv oprettet
)
echo.

REM -- 3. Install dependencies ----------------------------------------
echo   [3/4] Installerer afhaengigheder ...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
.venv\Scripts\python.exe -m pip install --quiet -e .
if %errorlevel% neq 0 (
    echo   X  pip install fejlede - tjek internetforbindelsen
    pause
    exit /b 1
)
echo   OK  Alle pakker installeret
echo.

REM -- 4. Verify ------------------------------------------------------
echo   [4/4] Verificerer installation ...
.venv\Scripts\python.exe -c "import bleak, websockets, fastapi, uvicorn, pydantic, streamlit; print('  OK  Alle moduler OK')"
if %errorlevel% neq 0 (
    echo   X  Verifikation fejlede!
    pause
    exit /b 1
)
echo.

echo   ================================================
echo      INSTALLATION FAERDIG!
echo   ================================================
echo.
echo   Naeste trin:
echo     Double-click  starters\START_ALL.bat
echo.
pause
