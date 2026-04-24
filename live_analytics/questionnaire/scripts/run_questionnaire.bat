@echo off
REM ----------------------------------------------------------------
REM  run_questionnaire.bat  –  start the Questionnaire server
REM
REM  Uses -ExecutionPolicy Bypass so the PS1 script runs regardless
REM  of the system-level PowerShell ExecutionPolicy setting.
REM ----------------------------------------------------------------
cd /d "%~dp0..\..\..\"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_questionnaire.ps1"
