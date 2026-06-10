#Requires -Version 5
# ================================================================
#  CarVR – Stop Kubernetes Stack (Windows)
#  Called by STOP_K8S.bat – can also be run directly in PowerShell.
# ================================================================
Set-StrictMode -Version Latest
$ErrorActionPreference = "SilentlyContinue"

$Cluster   = "carvr"
$Namespace = "carvr-local"
$Context   = "kind-$Cluster"

function Ok   { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green  }
function Info { param($msg) Write-Host "  -->  $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  +======================================================+" -ForegroundColor Cyan
Write-Host "  |   CarVR Kubernetes Stack – Stop                     |" -ForegroundColor Cyan
Write-Host "  +======================================================+" -ForegroundColor Cyan
Write-Host ""

# ── 1. Stop port-forwards ─────────────────────────────────────────
Info "Stopping port-forwards ..."
Get-Process -Name "kubectl" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Ok "Port-forwards stopped"

# ── 2. Ask what else to stop ──────────────────────────────────────
Write-Host ""
Write-Host "  What else would you like to stop?" -ForegroundColor White
Write-Host "    [1]  Nothing – only port-forwards (pods keep running)"    -ForegroundColor Gray
Write-Host "    [2]  Uninstall Helm release (stops pods, keeps cluster)"  -ForegroundColor Gray
Write-Host "    [3]  Delete entire kind cluster (removes everything)"     -ForegroundColor Gray
Write-Host ""
$choice = Read-Host "  Enter 1, 2 or 3"

switch ($choice) {
    "2" {
        Info "Uninstalling Helm release ..."
        & helm uninstall carvr --namespace $Namespace --kube-context $Context 2>&1 | Out-Null
        & kubectl delete namespace $Namespace --context $Context 2>&1 | Out-Null
        Ok "Helm release and namespace removed"
        Write-Host "  Restart anytime with START_K8S.bat (uses --skip-build)" -ForegroundColor Gray
    }
    "3" {
        Info "Deleting kind cluster '$Cluster' ..."
        & kind delete cluster --name $Cluster 2>&1 | Out-Null
        Ok "Cluster deleted — all pods and PVCs removed"
        Write-Host "  Restart anytime with START_K8S.bat (rebuilds cluster)" -ForegroundColor Gray
    }
    default {
        Ok "Only port-forwards stopped — pods are still running in the cluster"
        Write-Host "  Reconnect anytime with START_K8S.bat" -ForegroundColor Gray
    }
}

Write-Host ""
Read-Host "  Press Enter to close"
