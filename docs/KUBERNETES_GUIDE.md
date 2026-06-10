# CarVR Kubernetes Stack — Operations Guide

This guide covers everything you need to install, start, operate and stop
the CarVR analytics stack running in a local Kubernetes cluster.

---

## Table of contents

1. [What this stack is](#1-what-this-stack-is)
2. [Prerequisites](#2-prerequisites)
   - [macOS](#macos)
   - [Windows](#windows)
3. [First-time setup](#3-first-time-setup)
4. [Starting the stack](#4-starting-the-stack)
5. [Accessing the services](#5-accessing-the-services)
6. [Daily operations](#6-daily-operations)
7. [Stopping the stack](#7-stopping-the-stack)
8. [Updating after code changes](#8-updating-after-code-changes)
9. [Troubleshooting](#9-troubleshooting)
10. [Resource usage](#10-resource-usage)
11. [Architecture reference](#11-architecture-reference)

---

## 1. What this stack is

Four services run as containers inside a local Kubernetes cluster:

| Service | Port | Purpose |
|---|---|---|
| **analytics-api** | 8080 (HTTP) / 8766 (WS) | FastAPI server — stores sessions, HR data, scoring |
| **questionnaire** | 8090 | FastAPI server — participant registration, pre/post questionnaires |
| **dashboard** | 8501 | Streamlit — live analytics visualisation |
| **bridge** | 8765 (WS) | Simulated HR data source (mock mode, no hardware needed) |

All data is written to **persistent volumes** inside the cluster — it
survives pod restarts and Helm upgrades. Only deleting the cluster itself
removes all data.

> **Development mode still works.**  
> The Kubernetes stack is completely separate from the normal
> `START_ALL.command` / `START_ALL.bat` development setup.
> Both can coexist; they use different data directories.

---

## 2. Prerequisites

### macOS

Open **Terminal** and run:

```bash
# 1. Homebrew (if not already installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. Container tools
brew install kind kubectl helm

# 3. Docker Desktop  (provides the docker command + the container runtime)
brew install --cask docker
open /Applications/Docker.app   # start it once so it finishes first-run setup
```

Verify everything is installed:

```bash
docker version        # should print Client + Server
kind version          # kind v0.32.0 or later
kubectl version --client
helm version
```

**Minimum versions tested:**

| Tool | Version |
|---|---|
| Docker Desktop | 4.x or later |
| kind | 0.20 or later |
| kubectl | 1.28 or later |
| helm | 3.12 or later |

**Hardware requirements:**

| Resource | Minimum | Recommended |
|---|---|---|
| RAM | 6 GB free | 8 GB free |
| Disk | 8 GB free | 12 GB free |
| CPU | 2 cores | 4 cores |

> Images are ~913 MB each × 4 = ~3.6 GB total. The kind cluster node
> itself uses ~500 MB RAM at idle.

---

### Windows

Open **PowerShell as Administrator** and run:

```powershell
# Install all tools via winget
winget install Docker.DockerDesktop
winget install Kubernetes.kind
winget install Kubernetes.kubectl
winget install Helm.Helm

# Allow PowerShell scripts to run (required for START_K8S.ps1)
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
```

After installing Docker Desktop:
1. Open **Docker Desktop** from the Start menu
2. Wait for the whale icon in the system tray to stop animating
3. In Docker Desktop → Settings → Resources → set at least **4 GB RAM**

Verify:

```powershell
docker version
kind version
kubectl version --client
helm version
```

---

## 3. First-time setup

> Only needed once. Subsequent starts skip every step that is already done.

**macOS — double-click** `starters/START_K8S.command`  
**Windows — double-click** `starters/START_K8S.bat`

What happens on first run (takes **3–5 minutes**):

```
Step 1  Check docker / kind / kubectl / helm are installed
Step 2  Wait for Docker Desktop daemon to be ready
Step 3  Create kind cluster "carvr"             (~60 s)
Step 4  Build 4 Docker images from source       (~3 min)
Step 5  Load images into the cluster            (~30 s)
Step 6  Deploy via Helm (helm upgrade --install) (~30 s)
Step 7  Start port-forwards
Step 8  Open Dashboard, Questionnaire, API docs in browser
```

After the first run the images and cluster are cached — subsequent
starts complete in under **30 seconds**.

---

## 4. Starting the stack

### One-click (recommended)

| OS | File to double-click |
|---|---|
| macOS | `starters/START_K8S.command` |
| Windows | `starters/START_K8S.bat` |

### From a terminal

**macOS / Linux:**
```bash
bash starters/START_K8S.command
```

**Windows PowerShell:**
```powershell
powershell -ExecutionPolicy Bypass -File starters\START_K8S.ps1
```

### Manual step-by-step (advanced)

```bash
# 1. Build images (skip if already built)
bash deployment/scripts/build-images.sh

# 2. Load into kind cluster
for img in analytics-api questionnaire dashboard bridge; do
  kind load docker-image "carvr/${img}:latest" --name carvr
done

# 3. Deploy / upgrade
helm upgrade --install carvr deployment/helm/carvr \
  --namespace carvr-local --create-namespace \
  --values deployment/helm/carvr/values-kind.yaml \
  --kube-context kind-carvr --wait --timeout 5m

# 4. Start port-forwards
kubectl port-forward svc/analytics-api 8080:8080 -n carvr-local --context kind-carvr &
kubectl port-forward svc/questionnaire 8090:8090 -n carvr-local --context kind-carvr &
kubectl port-forward svc/dashboard     8501:8501 -n carvr-local --context kind-carvr &
kubectl port-forward svc/bridge        8765:8765 -n carvr-local --context kind-carvr &
```

---

## 5. Accessing the services

After starting, all services are available on `localhost`:

| Service | URL | Description |
|---|---|---|
| **Dashboard** | http://localhost:8501 | Live analytics — open this during a session |
| **Questionnaire** | http://localhost:8090 | Participant registration and questionnaires |
| **Analytics API docs** | http://localhost:8080/docs | Swagger UI — explore and test all endpoints |
| **Analytics healthcheck** | http://localhost:8080/healthz | Returns `{"status":"ok"}` when running |
| **Questionnaire healthcheck** | http://localhost:8090/api/healthz | Returns `{"status":"ok"}` when running |
| **Bridge WebSocket** | ws://localhost:8765 | HR data stream (Unity connects here) |

> **Unity connection:** point the Unity BLE bridge client at
> `ws://localhost:8765` — the bridge sends simulated HR data in the
> same binary format as the real Wahoo TICKR.

---

## 6. Daily operations

### Check pod status

```bash
kubectl get pods -n carvr-local --context kind-carvr
```

Expected output — all pods `1/1 Running`:

```
NAME                             READY   STATUS    RESTARTS   AGE
analytics-api-6c58f694ff-xxxxx   1/1     Running   0          5m
bridge-c5f78ff95-xxxxx           1/1     Running   0          5m
dashboard-8646669d44-xxxxx       1/1     Running   0          5m
questionnaire-7bcc5d68f-xxxxx    1/1     Running   0          5m
```

### View logs

```bash
# Follow logs for a specific service
kubectl logs -f deployment/analytics-api -n carvr-local --context kind-carvr
kubectl logs -f deployment/questionnaire  -n carvr-local --context kind-carvr
kubectl logs -f deployment/dashboard      -n carvr-local --context kind-carvr
kubectl logs -f deployment/bridge         -n carvr-local --context kind-carvr

# Last 50 lines only
kubectl logs --tail=50 deployment/analytics-api -n carvr-local --context kind-carvr
```

### Restart a service

```bash
kubectl rollout restart deployment/analytics-api -n carvr-local --context kind-carvr
```

### Check persistent volumes

```bash
kubectl get pvc -n carvr-local --context kind-carvr
```

All PVCs should be `Bound`:

```
NAME                     STATUS   CAPACITY
analytics-api-data-pvc   Bound    2Gi
questionnaire-data-pvc   Bound    1Gi
dashboard-data-pvc       Bound    1Gi
bridge-data-pvc          Bound    1Gi
carvr-participants-pvc   Bound    1Gi   ← shared by analytics-api + questionnaire
```

### Run smoke tests

Verifies all services are healthy and can read/write test data:

```bash
KUBE_CONTEXT=kind-carvr bash deployment/scripts/smoke-test.sh
```

Expected: `21 passed, 0 failed`.

### Open a shell inside a pod

```bash
kubectl exec -it deployment/analytics-api -n carvr-local --context kind-carvr -- bash
```

### Check data files on a volume

```bash
# List session files
kubectl exec deployment/analytics-api -n carvr-local --context kind-carvr \
  -- find /data -name "*.jsonl" -o -name "*.db" | sort

# List participant log directories
kubectl exec deployment/analytics-api -n carvr-local --context kind-carvr \
  -- ls /data/participants/
```

### Roll back a bad deployment

```bash
# Roll back to the previous Helm revision
helm rollback carvr -n carvr-local --kube-context kind-carvr

# Or list all revisions and roll back to a specific one
helm history carvr -n carvr-local --kube-context kind-carvr
helm rollback carvr 2 -n carvr-local --kube-context kind-carvr
```

---

## 7. Stopping the stack

### One-click (recommended)

| OS | File to double-click |
|---|---|
| macOS | `starters/STOP_K8S.command` |
| Windows | `starters/STOP_K8S.bat` |

You will be asked to choose one of three levels:

```
1) Stop port-forwards only   → pods keep running, reconnect in seconds
2) Uninstall Helm release    → stops all pods, frees RAM, keeps cluster
3) Delete kind cluster       → removes absolutely everything
```

### From a terminal

**Level 1 — close port-forwards only (pods keep running):**

```bash
# macOS/Linux
pkill -f "kubectl port-forward.*carvr-local"

# Windows PowerShell
Get-Process kubectl | Stop-Process -Force
```

**Level 2 — stop pods, keep cluster:**

```bash
helm uninstall carvr -n carvr-local --kube-context kind-carvr
kubectl delete namespace carvr-local --context kind-carvr
```

**Level 3 — delete everything:**

```bash
kind delete cluster --name carvr
```

---

## 8. Updating after code changes

When you change Python source code, rebuild only the affected image and
redeploy:

```bash
# Rebuild one image (e.g. analytics-api)
docker build -f deployment/analytics-api/Dockerfile -t carvr/analytics-api:latest .

# Load into cluster
kind load docker-image carvr/analytics-api:latest --name carvr

# Helm upgrade (applies the new image)
helm upgrade carvr deployment/helm/carvr \
  --namespace carvr-local \
  --values deployment/helm/carvr/values-kind.yaml \
  --kube-context kind-carvr \
  --wait

# Or just restart to pick up the already-loaded image
kubectl rollout restart deployment/analytics-api -n carvr-local --context kind-carvr
```

To rebuild **all** images at once:

```bash
bash deployment/scripts/build-images.sh
for img in analytics-api questionnaire dashboard bridge; do
  kind load docker-image "carvr/${img}:latest" --name carvr
done
helm upgrade carvr deployment/helm/carvr \
  --namespace carvr-local \
  --values deployment/helm/carvr/values-kind.yaml \
  --kube-context kind-carvr --wait
```

---

## 9. Troubleshooting

### Pod stuck in `CrashLoopBackOff` or `Error`

```bash
# See why it crashed
kubectl logs deployment/<name> -n carvr-local --context kind-carvr --previous

# Describe the pod for events
kubectl describe pod -l app.kubernetes.io/name=<name> -n carvr-local --context kind-carvr
```

Common causes:

| Symptom | Cause | Fix |
|---|---|---|
| `unrecognized arguments` | Wrong CLI flags in Dockerfile CMD | Check `deployment/<name>/Dockerfile` CMD line |
| `ModuleNotFoundError` | Package not installed in image | Rebuild the image after updating `requirements.txt` |
| `Address already in use` | Port conflict on host | Run `lsof -i :8080` and kill the conflicting process |
| Image pull error | Image not loaded into kind | Re-run `kind load docker-image ...` |

### Port-forward drops / "connection refused"

Port-forwards in Kubernetes are not persistent — they can drop if the
pod restarts. Simply re-run `START_K8S.command` / `START_K8S.bat`.
It is idempotent and will kill stale forwards before starting new ones.

### `helm upgrade` fails with timeout

```bash
# Check what is blocking rollout
kubectl get pods -n carvr-local --context kind-carvr
kubectl describe pod <pod-name> -n carvr-local --context kind-carvr

# Force delete a stuck pod (Kubernetes will recreate it)
kubectl delete pod <pod-name> -n carvr-local --context kind-carvr
```

### Docker Desktop not starting

On macOS, Docker Desktop sometimes requires a manual launch after a reboot:
```bash
open /Applications/Docker.app
```
Wait ~30 s for the whale icon in the menu bar to stop animating.

### kind cluster context missing

If `kubectl` says the context `kind-carvr` does not exist:
```bash
kind get kubeconfig --name carvr | kubectl config merge - ~/.kube/config
```
Or simply delete and recreate the cluster — data is in Docker volumes
and will be lost, but the cluster itself is ephemeral by design.

### Check which cluster kubectl is talking to

```bash
kubectl config get-contexts          # list all
kubectl config current-context       # show active
kubectl config use-context kind-carvr  # switch to kind cluster
```

> **Note:** Docker Desktop has its own built-in Kubernetes cluster
> (`docker-desktop` context). Our pods live in `kind-carvr`. The
> Docker Desktop "Kubernetes" panel will always show "No pods" because
> it only shows its own cluster — this is expected and correct.

---

## 10. Resource usage

| Component | Disk | RAM (idle) |
|---|---|---|
| Each Docker image | ~913 MB | — |
| All 4 images total | ~3.6 GB | — |
| kind cluster node | ~200 MB | ~500 MB |
| analytics-api pod | — | ~256 MB |
| questionnaire pod | — | ~128 MB |
| dashboard pod | — | ~256 MB |
| bridge pod | — | ~64 MB |
| Persistent volumes | ~6.5 GB reserved | — |
| **Total** | **~10 GB** | **~1.2 GB** |

Free up disk space when not in use:

```bash
kind delete cluster --name carvr          # removes cluster volumes
docker image prune -f                      # removes dangling layers
docker builder prune -f                    # removes build cache (~1.6 GB)
```

---

## 11. Architecture reference

```
Your machine (localhost)
│
│  port-forward tunnels (kubectl)
├─ :8501 ──────────────────────────► dashboard pod
├─ :8090 ──────────────────────────► questionnaire pod
├─ :8080 / :8766 ──────────────────► analytics-api pod
└─ :8765 ──────────────────────────► bridge pod (mock HR data)
                                         │
                              Kubernetes internal DNS
                              (http://analytics-api:8080)
                                         │
                              ┌──────────▼──────────┐
                              │  analytics-api pod  │
                              │  /data/analytics.db │◄─── analytics-api-data-pvc (2Gi)
                              │  /data/sessions/    │
                              │  /data/pulse/       │
                              │  /data/participants/│◄─┐
                              └─────────────────────┘  │ carvr-participants-pvc
                                                        │ (shared, 1Gi)
                              ┌─────────────────────┐  │
                              │  questionnaire pod  │  │
                              │  /data/participants/│◄─┘
                              │  /data/questionnaire│◄─── questionnaire-data-pvc (1Gi)
                              └─────────────────────┘

Namespace: carvr-local   Cluster: kind-carvr   Context: kind-carvr
```

**Data flow:**
1. Bridge generates simulated HR frames → WebSocket `:8765`
2. Unity / test client sends frames to analytics-api WebSocket `:8766`
3. analytics-api writes raw frames to `pulse.jsonl` on PVC **first**
4. analytics-api upserts scores into SQLite DB (`analytics.db`)
5. Dashboard polls analytics-api HTTP `:8080` and renders live charts
6. Questionnaire registers participants and stores answers independently
