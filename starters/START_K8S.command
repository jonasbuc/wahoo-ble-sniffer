#!/bin/bash
# ════════════════════════════════════════════════════════════════
#  CarVR – Kubernetes Stack (macOS)
#  Double-click to start all 4 services in Kubernetes (kind).
#
#  First run:  builds Docker images + creates cluster (~3–5 min)
#  Later runs: cluster already exists → starts in ~30 seconds
#
#  Services opened in your browser:
#    Dashboard      → http://localhost:8501
#    Analytics API  → http://localhost:8080/docs
#    Questionnaire  → http://localhost:8090
# ════════════════════════════════════════════════════════════════
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"

CLUSTER="carvr"
NAMESPACE="carvr-local"
CONTEXT="kind-${CLUSTER}"

# ── Colours ───────────────────────────────────────────────────
GREEN="\033[0;32m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
ok()   { echo -e "  ${GREEN}✓${NC}  $*"; }
info() { echo -e "  ${YELLOW}→${NC}  $*"; }
fail() { echo -e "  ${RED}✗${NC}  $*"; }

echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║   🚴  CarVR Kubernetes Stack                        ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Check required tools ───────────────────────────────────
MISSING=0
for tool in docker kind kubectl helm; do
  if ! command -v "$tool" &>/dev/null; then
    fail "$tool not found — install with: brew install $tool"
    MISSING=1
  fi
done
if [ "$MISSING" -eq 1 ]; then
  echo ""
  echo "  Install missing tools and try again."
  read -rp "  Press Enter to close …"
  exit 1
fi

# ── 2. Check Docker daemon ────────────────────────────────────
if ! docker info &>/dev/null; then
  info "Docker Desktop is not running — starting it …"
  open /Applications/Docker.app
  echo "  Waiting for Docker daemon (up to 60 s) …"
  for i in $(seq 1 12); do
    docker info &>/dev/null && break || sleep 5
    [ "$i" -eq 12 ] && { fail "Docker did not start in time."; read -rp "  Press Enter to close …"; exit 1; }
  done
fi
ok "Docker is running"

# ── 3. Create kind cluster if it doesn't exist ────────────────
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER}$"; then
  ok "Kind cluster '${CLUSTER}' already exists"
else
  info "Creating kind cluster '${CLUSTER}' (first time only) …"
  kind create cluster --name "$CLUSTER" --config - <<'EOF'
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
EOF
  ok "Cluster created"
fi

# ── 4. Build images only if they don't exist yet ──────────────
NEED_BUILD=0
for img in analytics-api analytics-ingest analytics-ws questionnaire dashboard; do
  docker image inspect "carvr/${img}:latest" &>/dev/null || { NEED_BUILD=1; break; }
done

if [ "$NEED_BUILD" -eq 1 ]; then
  info "Building Docker images (first time — this takes ~3 min) ..."
  bash deployment/scripts/build-images.sh
  ok "Images built"
else
  ok "Docker images already exist — skipping build"
fi

# ── 5. Load images into kind (skip if already loaded) ─────────────
info "Loading images into kind cluster …"
for img in analytics-api analytics-ingest analytics-ws questionnaire dashboard; do
  kind load docker-image "carvr/${img}:latest" --name "$CLUSTER" 2>&1 | grep -v "^$" | sed 's/^/    /'
done
ok "Images loaded"

# ── 6. Deploy / upgrade via Helm ──────────────────────────────
info "Deploying with Helm …"
helm upgrade --install carvr deployment/helm/carvr \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --values deployment/helm/carvr/values-kind.yaml \
  --kube-context "$CONTEXT" \
  --wait --timeout 5m \
  --rollback-on-failure 2>&1 | tail -3
ok "Helm release deployed"

# ── 7. Start port-forwards in background ──────────────────────
info "Starting port-forwards …"

# Kill any stale port-forwards from a previous run
pkill -f "kubectl port-forward.*carvr-local" 2>/dev/null || true
sleep 1

kubectl port-forward svc/analytics-api    8080:8080 -n "$NAMESPACE" --context "$CONTEXT" &>/dev/null &
kubectl port-forward svc/analytics-ingest 8766:8766 -n "$NAMESPACE" --context "$CONTEXT" &>/dev/null &
kubectl port-forward svc/analytics-ws     8768:8768 -n "$NAMESPACE" --context "$CONTEXT" &>/dev/null &
kubectl port-forward svc/questionnaire    8090:8090 -n "$NAMESPACE" --context "$CONTEXT" &>/dev/null &
kubectl port-forward svc/dashboard        8501:8501 -n "$NAMESPACE" --context "$CONTEXT" &>/dev/null &
# Bridge runs locally on the host machine — not deployed in K8s.
# Start it separately with starters/START_BRIDGE.command

# Wait for all ports to be ready
for port in 8080 8766 8768 8090 8501; do
  for i in $(seq 1 20); do
    nc -z localhost "$port" 2>/dev/null && break || sleep 0.5
  done
  nc -z localhost "$port" 2>/dev/null && ok "localhost:${port} ready" || { fail "localhost:${port} did not open"; }
done

# ── 8. Open in browser ────────────────────────────────────────
echo ""
info "Opening services in browser …"
open "http://localhost:8501"          # Dashboard
sleep 0.5
open "http://localhost:8090"          # Questionnaire
sleep 0.5
open "http://localhost:8080/docs"     # Analytics API

echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║   ✓  All services are running!                      ║"
echo "  ║                                                      ║"
echo "  ║   Dashboard        →  http://localhost:8501         ║"
echo "  ║   Questionnaire    →  http://localhost:8090         ║"
echo "  ║   Analytics API    →  http://localhost:8080/docs    ║"
echo "  ║   Analytics Ingest →  ws://localhost:8766           ║"
echo "  ║   Analytics WS     →  ws://localhost:8768           ║"
echo "  ║                                                      ║"
echo "  ║   Bridge runs locally — use START_BRIDGE.command    ║"
echo "  ║                                                      ║"
echo "  ║   Close this window to stop all port-forwards.      ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""

# Keep running — when the terminal window is closed, the
# port-forward processes (children of this shell) are killed.
wait
