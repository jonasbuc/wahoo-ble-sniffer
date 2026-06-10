@echo off
chcp 65001 >nul 2>&1
:: ----------------------------------------------------------------
::  CarVR – Stop Kubernetes Stack (Windows)
::  Double-click to stop port-forwards and optionally the cluster.
:: ----------------------------------------------------------------

cd /d "%~dp0\.."

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0STOP_K8S.ps1"

pause
