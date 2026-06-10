#!/bin/bash
# ════════════════════════════════════════════════════════════════
#  CarVR – Stop Kubernetes Stack (macOS)
#  Double-click to stop port-forwards and optionally the cluster.
# ════════════════════════════════════════════════════════════════
set -euo pipefail

cd "$(dirname "$0")/.."

CLUSTER="carvr"
NAMESPACE="carvr-local"
CONTEXT="kind-${CLUSTER}"

GREEN="\033[0;32m"; YELLOW="\033[1;33m"; NC="\033[0m"
ok()   { echo -e "  ${GREEN}✓${NC}  $*"; }
info() { echo -e "  ${YELLOW}→${NC}  $*"; }

echo ""
echo "  ╔══════════════════════════════════════════════════════╗"
echo "  ║   CarVR Kubernetes Stack – Stop                     ║"
echo "  ╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. Stop port-forwards ─────────────────────────────────────────
info "Stopping port-forwards ..."
pkill -f "kubectl port-forward.*carvr-local" 2>/dev/null && ok "Port-forwards stopped" || ok "No port-forwards were running"

# ── 2. Ask what else to stop ──────────────────────────────────────
echo ""
echo "  What else would you like to stop?"
echo "    1)  Nothing – only port-forwards (pods keep running)"
echo "    2)  Uninstall Helm release (stops pods, keeps cluster)"
echo "    3)  Delete entire kind cluster (removes everything)"
echo ""
read -rp "  Enter 1, 2 or 3: " choice

case "$choice" in
  2)
    info "Uninstalling Helm release ..."
    helm uninstall carvr --namespace "$NAMESPACE" --kube-context "$CONTEXT" 2>/dev/null || true
    kubectl delete namespace "$NAMESPACE" --context "$CONTEXT" 2>/dev/null || true
    ok "Helm release and namespace removed"
    echo "  Restart anytime with START_K8S.command (uses existing images)"
    ;;
  3)
    info "Deleting kind cluster '$CLUSTER' ..."
    kind delete cluster --name "$CLUSTER"
    ok "Cluster deleted — all pods and PVCs removed"
    echo "  Restart anytime with START_K8S.command (rebuilds cluster)"
    ;;
  *)
    ok "Only port-forwards stopped — pods are still running in the cluster"
    echo "  Reconnect anytime with START_K8S.command"
    ;;
esac

echo ""
read -rp "  Press Enter to close …"
