@echo off
REM ----------------------------------------------------------------
REM  run_dashboard.bat  –  start the Streamlit dashboard
REM
REM  Uses -ExecutionPolicy Bypass so the PS1 script runs regardless
REM  of the system-level PowerShell ExecutionPolicy setting.
REM ----------------------------------------------------------------
cd /d "%~dp0..\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_dashboard.ps1"
