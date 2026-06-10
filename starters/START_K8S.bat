@echo off
chcp 65001 >nul 2>&1
:: ----------------------------------------------------------------
::  CarVR - Kubernetes Stack (Windows)
::  Double-click to start all 4 services in Kubernetes (kind).
::
::  First run:  builds Docker images + creates cluster (~3-5 min)
::  Later runs: cluster already exists -> starts in ~30 seconds
::
::  Services opened in your browser:
::    Dashboard      -> http://localhost:8501
::    Analytics API  -> http://localhost:8080/docs
::    Questionnaire  -> http://localhost:8090
:: ----------------------------------------------------------------

cd /d "%~dp0\.."

:: Launch the PowerShell script from the same directory
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0START_K8S.ps1"

pause
