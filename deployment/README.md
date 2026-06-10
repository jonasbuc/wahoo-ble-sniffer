# CarVR – Container Deployment Guide

This directory contains everything needed to run the four CarVR services
(`analytics-api`, `questionnaire`, `dashboard`, `bridge`) inside containers on a
local Kubernetes cluster.

```
deployment/
├── analytics-api/   # Dockerfile for the FastAPI analytics server
├── bridge/          # Dockerfile for the BLE/mock bridge
├── dashboard/       # Dockerfile for the Streamlit dashboard
├── questionnaire/   # Dockerfile for the FastAPI questionnaire server
├── kubernetes/      # Raw Kubernetes YAML (kubectl / kustomize)
├── helm/carvr/      # Helm chart (recommended)
├── scripts/         # Helper shell scripts
└── README.md        # This file
```

---

## Prerequisites

Install the following tools on your machine.  
On macOS all of them are available via [Homebrew](https://brew.sh).

```bash
brew install docker kubectl helm minikube
# or for kind instead of minikube:
brew install kind
```

| Tool | Purpose |
|---|---|
| `docker` | Build container images |
| `kubectl` | Interact with the cluster |
| `helm` | Deploy / upgrade the Helm chart |
| `minikube` | Single-node local cluster (option A) |
| `kind` | Cluster-in-Docker (option B) |

Run the prerequisite checker at any time:

```bash
bash deployment/scripts/check-prerequisites.sh
```

---

## Architecture

```
Browser / Unity client
        │
  kubectl port-forward
        │
  ┌─────▼──────┐   K8s DNS   ┌───────────────────┐
  │ dashboard  │────────────▶│  analytics-api     │
  │  :8501     │             │  :8080 (HTTP)      │
  └────────────┘             │  :8766 (WS ingest) │
                             └──────────┬──────────┘
  ┌───────────────────┐                 │ shared PVC
  │  questionnaire    │─────────────────┘
  │  :8090            │   /data/participants
  └───────────────────┘
  ┌───────────────────┐
  │  bridge           │ mock mode by default (no Bluetooth in containers)
  │  :8765 (WS)       │ set BRIDGE_MOCK=0 + host-network for real BLE
  └───────────────────┘
```

**Important:** The Streamlit dashboard calls `analytics-api` from Python
server-side code (not from the browser). `LA_API_BASE` must therefore use the
Kubernetes-internal DNS name `http://analytics-api:8080`, not a browser-facing
URL.

### Persistent volumes

| PVC | Default size | Mounted by |
|---|---|---|
| `analytics-api-data-pvc` | 2 Gi | analytics-api |
| `questionnaire-data-pvc` | 512 Mi | questionnaire |
| `dashboard-data-pvc` | 128 Mi | dashboard |
| `bridge-data-pvc` | 128 Mi | bridge |
| `carvr-participants-pvc` | 1 Gi | analytics-api **and** questionnaire (shared) |

---

## Option A – Minikube (recommended)

### 1 – Start Minikube

```bash
minikube start --profile carvr --cpus 4 --memory 4096
```

### 2 – Build images inside Minikube

```bash
eval $(minikube -p carvr docker-env)
bash deployment/scripts/build-images.sh --minikube
```

### 3 – Deploy with Helm

```bash
helm upgrade --install carvr deployment/helm/carvr \
  --namespace carvr-local --create-namespace \
  -f deployment/helm/carvr/values-minikube.yaml \
  --atomic --wait --timeout 5m
```

Or use the one-click script:

```bash
bash deployment/scripts/deploy-minikube.sh
```

### 4 – Access the services

```bash
kubectl port-forward -n carvr-local svc/analytics-api 8080:8080 &
kubectl port-forward -n carvr-local svc/questionnaire 8090:8090 &
kubectl port-forward -n carvr-local svc/dashboard    8501:8501 &
kubectl port-forward -n carvr-local svc/bridge       8765:8765 &
```

Then open:
- Dashboard → http://localhost:8501
- Analytics API → http://localhost:8080/docs
- Questionnaire → http://localhost:8090

---

## Option B – kind

### 1 – Create the cluster and deploy

```bash
bash deployment/scripts/deploy-kind.sh
```

This script:
1. Creates a `kind` cluster named `carvr` with NodePort mappings
2. Builds all four images
3. Loads them into the cluster with `kind load docker-image`
4. Deploys via Helm with `values-kind.yaml`

### 2 – Access via NodePort

| Service | Local port |
|---|---|
| analytics-api | 30080 |
| questionnaire | 30090 |
| dashboard | 30501 |
| bridge | 30765 |

---

## Option C – Raw kubectl / kustomize

No Helm required.

```bash
kubectl apply -k deployment/kubernetes/
```

To remove:

```bash
kubectl delete -k deployment/kubernetes/
# or simply:
kubectl delete namespace carvr-local
```

---

## Updating images

After changing source code, rebuild and redeploy:

```bash
# Minikube
eval $(minikube -p carvr docker-env)
bash deployment/scripts/build-images.sh --minikube
helm upgrade carvr deployment/helm/carvr \
  --namespace carvr-local \
  -f deployment/helm/carvr/values-minikube.yaml

# kind
bash deployment/scripts/build-images.sh --kind
helm upgrade carvr deployment/helm/carvr \
  --namespace carvr-local \
  -f deployment/helm/carvr/values-kind.yaml
```

---

## Bridge mock vs. live mode

Real BLE requires host Bluetooth hardware and `--network=host`, which is not
possible inside standard Kubernetes pods.

| Environment variable | Value | Effect |
|---|---|---|
| `BRIDGE_MOCK` | `1` (default) | Start the mock bridge (no BLE hardware needed) |
| `BRIDGE_MOCK` | `0` | Start the live BLE bridge (requires host-network pod) |

To switch at runtime without rebuilding the image:

```bash
kubectl set env -n carvr-local deployment/bridge BRIDGE_MOCK=0
```

---

## Useful operations

### Check pod status

```bash
kubectl get pods -n carvr-local
```

### Tail logs

```bash
kubectl logs -n carvr-local -l app.kubernetes.io/name=analytics-api -f
kubectl logs -n carvr-local -l app.kubernetes.io/name=questionnaire  -f
kubectl logs -n carvr-local -l app.kubernetes.io/name=dashboard       -f
kubectl logs -n carvr-local -l app.kubernetes.io/name=bridge          -f
```

### Restart a deployment

```bash
kubectl rollout restart -n carvr-local deployment/analytics-api
```

### Check PVC usage

```bash
kubectl get pvc -n carvr-local
```

### Roll back a Helm release

```bash
helm rollback carvr -n carvr-local
# or to a specific revision:
helm rollback carvr 2 -n carvr-local
```

### Run smoke tests

```bash
bash deployment/scripts/smoke-test.sh
```

The script port-forwards all four services, checks health endpoints, then tears
down the port-forwards automatically.

---

## Validation (no cluster needed)

```bash
bash deployment/scripts/validate.sh
```

This script runs (depending on what is installed):

- `kubectl apply --dry-run=client -k deployment/kubernetes/`
- `helm lint deployment/helm/carvr`
- `helm template carvr deployment/helm/carvr -f values-minikube.yaml`
- `helm template carvr deployment/helm/carvr -f values-kind.yaml`
- `yamllint` on all YAML files

If `helm` is not yet installed, install it first:

```bash
brew install helm
```

---

## Cleanup

```bash
# Helm + namespace only (keeps Minikube/kind cluster)
bash deployment/scripts/cleanup.sh

# Helm + namespace + delete Minikube profile
bash deployment/scripts/cleanup.sh --minikube

# Helm + namespace + delete kind cluster
bash deployment/scripts/cleanup.sh --kind
```

---

## Security notes

- All pods run as non-root uid 1000.
- `allowPrivilegeEscalation: false` and `capabilities.drop: [ALL]` are set on
  every container.
- No secrets are stored in plaintext in any YAML file. Use
  `kubectl create secret` or a Helm `--set` override for sensitive values.
- The namespace `carvr-local` is fully isolated; deleting it removes every
  resource created by this chart.
