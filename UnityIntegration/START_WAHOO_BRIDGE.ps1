<#
.SYNOPSIS
  Start the Wahoo bridge and GUI on Windows using the repository virtualenv if available.

USAGE
  Right-click and "Run with PowerShell", or execute from an elevated PowerShell prompt:
    .\START_WAHOO_BRIDGE.ps1

This script will:
  - Prefer the repo .venv Python at ..\.venv\Scripts\python.exe
  - Launch the GUI monitor in a new process with --live
  - Launch the canonical bridge in a second process with --live
#>

Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$repoRoot = Resolve-Path (Join-Path $scriptDir "..")
$venvPy = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (Test-Path $venvPy) {
    $python = $venvPy
    Write-Host "Using virtualenv Python: $python"
} else {
    $python = "python"
    Write-Host "Virtualenv not found, falling back to system Python"
}


Write-Host "Starting Wahoo Bridge (new window)..."
Start-Process -FilePath $python -ArgumentList "UnityIntegration\python\wahoo_unity_bridge.py", "--live" -WorkingDirectory $repoRoot

Start-Sleep -Seconds 1

Write-Host "Starting Wahoo Bridge GUI (new window)..."
Start-Process -FilePath $python -ArgumentList "UnityIntegration\python\wahoo_bridge_gui.py", "--live" -WorkingDirectory $repoRoot

Write-Host "Bridge and GUI started. Use Task Manager or the Terminal windows to view output."
