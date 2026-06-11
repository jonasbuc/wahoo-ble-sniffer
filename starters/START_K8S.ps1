#Requires -Version 5
# ================================================================
#  CarVR – Kubernetes Stack (Windows)
#  Called by START_K8S.bat – can also be run directly in PowerShell.
#
#  First run:  builds Docker images + creates cluster (~3-5 min)
#  Later runs: cluster already exists -> starts in ~30 seconds
# ================================================================
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Working directory: repo root ─────────────────────────────────
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Cluster   = "carvr"
$Namespace = "carvr-local"
$Context   = "kind-$Cluster"

# ── Colour helpers ───────────────────────────────────────────────
function Ok   { param($msg) Write-Host "  [OK] $msg"   -ForegroundColor Green  }
function Info { param($msg) Write-Host "  -->  $msg"   -ForegroundColor Yellow }
function Fail { param($msg) Write-Host "  [X]  $msg"   -ForegroundColor Red    }

function Require-Tool {
    param([string]$Tool, [string]$InstallHint)
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) {
        Fail "$Tool not found."
        Write-Host "       Install via winget:  $InstallHint" -ForegroundColor Gray
        return $false
    }
    return $true
}

Write-Host ""
Write-Host "  +======================================================+" -ForegroundColor Cyan
Write-Host "  |   CarVR Kubernetes Stack                             |" -ForegroundColor Cyan
Write-Host "  +======================================================+" -ForegroundColor Cyan
Write-Host ""

# ── 1. Check required tools ──────────────────────────────────────
$missing = 0
if (-not (Require-Tool "docker"  "winget install Docker.DockerDesktop"))  { $missing++ }
if (-not (Require-Tool "kind"    "winget install Kubernetes.kind"))        { $missing++ }
if (-not (Require-Tool "kubectl" "winget install Kubernetes.kubectl"))     { $missing++ }
if (-not (Require-Tool "helm"    "winget install Helm.Helm"))              { $missing++ }

if ($missing -gt 0) {
    Write-Host ""
    Fail "$missing tool(s) missing. Install them and re-run START_K8S.bat."
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit 1
}
Ok "All required tools found"

# ── 2. Check Docker daemon ───────────────────────────────────────
Info "Checking Docker daemon ..."
$dockerReady = $false
for ($i = 1; $i -le 24; $i++) {
    $result = & docker info 2>&1
    if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
    if ($i -eq 1) {
        Info "Docker Desktop is not running — starting it ..."
        Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe" -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 5
}
if (-not $dockerReady) {
    Fail "Docker Desktop did not start in time. Open it manually and retry."
    Read-Host "  Press Enter to close"
    exit 1
}
Ok "Docker is running"

# ── 3. Create kind cluster if it doesn't exist ───────────────────
$clusters = & kind get clusters 2>&1
if ($clusters -match "^$Cluster$") {
    Ok "Kind cluster '$Cluster' already exists"
} else {
    Info "Creating kind cluster '$Cluster' (first time only) ..."

    $kindConfig = @"
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30080
        hostPort: 30080
        protocol: TCP
      - containerPort: 30090
        hostPort: 30090
        protocol: TCP
      - containerPort: 30501
        hostPort: 30501
        protocol: TCP
      - containerPort: 30765
        hostPort: 30765
        protocol: TCP
"@
    $tmpFile = [System.IO.Path]::GetTempFileName() + ".yaml"
    $kindConfig | Set-Content -Path $tmpFile -Encoding UTF8
    & kind create cluster --name $Cluster --config $tmpFile
    Remove-Item $tmpFile -ErrorAction SilentlyContinue

    if ($LASTEXITCODE -ne 0) {
        Fail "Failed to create kind cluster."
        Read-Host "  Press Enter to close"
        exit 1
    }
    Ok "Cluster created"
}

# ── 4. Build images only if they don't exist yet ─────────────────
$needBuild = $false
foreach ($img in @("analytics-api","analytics-ingest","analytics-ws","questionnaire","dashboard")) {
    $check = & docker image inspect "carvr/${img}:latest" 2>&1
    if ($LASTEXITCODE -ne 0) { $needBuild = $true; break }
}

if ($needBuild) {
    Info "Building Docker images (first time — this takes ~3 min) ..."
    & bash deployment/scripts/build-images.sh
    if ($LASTEXITCODE -ne 0) {
        Fail "Image build failed."
        Read-Host "  Press Enter to close"
        exit 1
    }
    Ok "Images built"
} else {
    Ok "Docker images already exist — skipping build"
}

# ── 5. Load images into kind cluster ─────────────────────────────
Info "Loading images into kind cluster ..."
foreach ($img in @("analytics-api","analytics-ingest","analytics-ws","questionnaire","dashboard")) {
    Write-Host "    Loading carvr/${img}:latest ..." -ForegroundColor Gray
    & kind load docker-image "carvr/${img}:latest" --name $Cluster
}
Ok "Images loaded"

# ── 6. Deploy / upgrade via Helm ─────────────────────────────────
Info "Deploying with Helm ..."
& helm upgrade --install carvr deployment/helm/carvr `
    --namespace $Namespace `
    --create-namespace `
    --values deployment/helm/carvr/values-kind.yaml `
    --kube-context $Context `
    --wait --timeout 5m `
    --rollback-on-failure

if ($LASTEXITCODE -ne 0) {
    Fail "Helm deploy failed. Check logs with:"
    Write-Host "       kubectl logs deployment/analytics-api -n $Namespace --context $Context" -ForegroundColor Gray
    Read-Host "  Press Enter to close"
    exit 1
}
Ok "Helm release deployed"

# ── 7. Start port-forwards in background ─────────────────────────
Info "Starting port-forwards ..."

# Kill any stale port-forwards from a previous run
Get-Process -Name "kubectl" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*port-forward*carvr-local*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

$pfArgs = @(
    @("svc/analytics-api",    "8080:8080"),
    @("svc/analytics-ingest", "8766:8766"),
    @("svc/analytics-ws",     "8768:8768"),
    @("svc/questionnaire",    "8090:8090"),
    @("svc/dashboard",        "8501:8501")
    # Bridge runs locally on the host machine — not deployed in K8s.
    # Start it separately with starters\START_BRIDGE.bat
)
foreach ($pf in $pfArgs) {
    Start-Process -FilePath "kubectl" `
        -ArgumentList "port-forward $($pf[0]) $($pf[1]) -n $Namespace --context $Context" `
        -WindowStyle Hidden
}

# Wait for all ports to be ready (up to 15 s each)
$ports = @(8080, 8766, 8768, 8090, 8501)
foreach ($port in $ports) {
    $ready = $false
    for ($i = 1; $i -le 30; $i++) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $tcp.Connect("localhost", $port)
            $tcp.Close()
            $ready = $true
            break
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if ($ready) { Ok "localhost:$port ready" }
    else         { Fail "localhost:$port did not open in time" }
}

# ── 8. Open in browser ────────────────────────────────────────────
Write-Host ""
Info "Opening services in browser ..."
Start-Process "http://localhost:8501"       # Dashboard
Start-Sleep -Milliseconds 500
Start-Process "http://localhost:8090"       # Questionnaire
Start-Sleep -Milliseconds 500
Start-Process "http://localhost:8080/docs"  # Analytics API docs

Write-Host ""
Write-Host "  +======================================================+" -ForegroundColor Green
Write-Host "  |   All services are running!                         |" -ForegroundColor Green
Write-Host "  |                                                      |" -ForegroundColor Green
Write-Host "  |   Dashboard        ->  http://localhost:8501        |" -ForegroundColor Green
Write-Host "  |   Questionnaire    ->  http://localhost:8090        |" -ForegroundColor Green
Write-Host "  |   Analytics API    ->  http://localhost:8080/docs   |" -ForegroundColor Green
Write-Host "  |   Analytics Ingest ->  ws://localhost:8766          |" -ForegroundColor Green
Write-Host "  |   Analytics WS     ->  ws://localhost:8768          |" -ForegroundColor Green
Write-Host "  |                                                      |" -ForegroundColor Green
Write-Host "  |   Bridge runs locally — use START_BRIDGE.bat        |" -ForegroundColor Green
Write-Host "  |                                                      |" -ForegroundColor Green
Write-Host "  |   To STOP:  run START_K8S.bat and choose Stop,      |" -ForegroundColor Green
Write-Host "  |   or run:   kind delete cluster --name carvr        |" -ForegroundColor Green
Write-Host "  +======================================================+" -ForegroundColor Green
Write-Host ""
Read-Host "  Press Enter to close this window (port-forwards keep running)"
