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
echo   [1/5] Tjekker Python ...
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
for /f "tokens=1 delims=." %%a in ("%PYTHON_VERSION%") do set PY_MAJOR=%%a
for /f "tokens=2 delims=." %%b in ("%PYTHON_VERSION%") do set PY_MINOR=%%b
if %PY_MAJOR% LSS 3 (
    echo   X  Python %PYTHON_VERSION% fundet, men projektet kraever Python ^>= 3.11!
    echo      Installer en nyere version fra https://www.python.org/downloads/
    pause
    exit /b 1
)
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 11 (
    echo   X  Python %PYTHON_VERSION% fundet, men projektet kraever Python ^>= 3.11!
    echo      Installer en nyere version fra https://www.python.org/downloads/
    pause
    exit /b 1
)
echo   OK  Python %PYTHON_VERSION%
echo.

REM -- 2. Virtual environment -----------------------------------------
echo   [2/5] Opretter virtual environment ...
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

REM -- 2b. PowerShell ExecutionPolicy (so PS1 scripts can run) ---------
echo   [2b] Konfigurerer PowerShell ExecutionPolicy ...
powershell.exe -NoProfile -NonInteractive -Command ^
  "try { Set-ExecutionPolicy RemoteSigned -Scope CurrentUser -Force; Write-Host '  OK  ExecutionPolicy sat til RemoteSigned (CurrentUser)' } catch { Write-Host ('  !   Kunne ikke aendre ExecutionPolicy: ' + $_.Exception.Message) }"
echo.

REM -- 3. Install dependencies ----------------------------------------
echo   [3/5] Installerer afhaengigheder ...
.venv\Scripts\python.exe -m pip install --upgrade pip
if %errorlevel% neq 0 (
    echo   X  pip upgrade fejlede
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo   X  pip install -r requirements.txt fejlede - tjek internetforbindelsen
    echo      og at requirements.txt ikke er beskadiget.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m pip install -e .
if %errorlevel% neq 0 (
    echo   X  pip install -e . fejlede - tjek at pyproject.toml er korrekt.
    pause
    exit /b 1
)
echo   OK  Alle pakker installeret
echo.

REM -- 4. Verify ------------------------------------------------------
echo   [4/5] Verificerer installation ...
.venv\Scripts\python.exe starters\preflight.py
if %errorlevel% neq 0 (
    echo   X  Verifikation fejlede!
    pause
    exit /b 1
)
echo.

REM -- 5. Init database -----------------------------------------------
echo   [5/5] Initialiserer database ...
.venv\Scripts\python.exe live_analytics\scripts\init_db.py
if %errorlevel% neq 0 (
    echo   X  Database init fejlede!
    pause
    exit /b 1
)
echo   OK  Database klar
echo.

echo   ================================================
echo      INSTALLATION FAERDIG!
echo   ================================================
echo.
echo   Naeste trin:
echo     Double-click  starters\START_ALL.bat
echo.
pause
