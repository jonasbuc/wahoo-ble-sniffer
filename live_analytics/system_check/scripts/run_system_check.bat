@echo off
REM ----------------------------------------------------------------
REM  run_system_check.bat  –  start the System Check GUI server
REM
REM  Uses -ExecutionPolicy Bypass so the PS1 script runs regardless
REM  of the system-level PowerShell ExecutionPolicy setting.
REM ----------------------------------------------------------------
cd /d "%~dp0..\..\..\"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_system_check.ps1"
