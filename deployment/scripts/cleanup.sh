#!/usr/bin/env bash
# ── cleanup.sh ────────────────────────────────────────────────────────────────
# Removes ONLY the CarVR resources from the current cluster.
# Never touches default namespace or other projects.
#
# Usage:
#   ./deployment/scripts/cleanup.sh              # Helm uninstall + namespace
#   ./deployment/scripts/cleanup.sh --minikube   # also delete Minikube profile
#   ./deployment/scripts/cleanup.sh --kind       # also delete kind cluster
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

NAMESPACE="carvr-local"
RELEASE="carvr"
MINIKUBE_PROFILE="carvr"
KIND_CLUSTER="carvr"
DELETE_MINIKUBE=0
DELETE_KIND=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --minikube) DELETE_MINIKUBE=1; shift ;;
    --kind)     DELETE_KIND=1;     shift ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  CarVR — Cleanup"
echo "  This removes ONLY carvr resources (namespace: $NAMESPACE)."
echo "══════════════════════════════════════════════════════════"
echo ""

# ── 1. Helm uninstall ─────────────────────────────────────────────────────────
if command -v helm &>/dev/null; then
  if helm status "$RELEASE" --namespace "$NAMESPACE" &>/dev/null; then
    echo "► Uninstalling Helm release '$RELEASE' ..."
    helm uninstall "$RELEASE" --namespace "$NAMESPACE" --wait
    echo "  ✓ Helm release removed"
  else
    echo "  ⚠  No Helm release '$RELEASE' found in namespace $NAMESPACE — skipping"
  fi
else
  echo "  ⚠  helm not found — skipping Helm uninstall"
fi
echo ""

# ── 2. Delete namespace (removes all remaining resources) ────────────────────
if command -v kubectl &>/dev/null; then
  if kubectl get namespace "$NAMESPACE" &>/dev/null; then
    echo "► Deleting namespace '$NAMESPACE' ..."
    kubectl delete namespace "$NAMESPACE"
    echo "  ✓ Namespace '$NAMESPACE' deleted"
  else
    echo "  ⚠  Namespace '$NAMESPACE' not found — already cleaned up?"
  fi
else
  echo "  ⚠  kubectl not found — skipping namespace deletion"
fi
echo ""

# ── 3. Optional: delete Minikube profile ─────────────────────────────────────
if [ "$DELETE_MINIKUBE" -eq 1 ]; then
  if command -v minikube &>/dev/null; then
    if minikube status --profile "$MINIKUBE_PROFILE" &>/dev/null; then
      echo "► Deleting Minikube profile '$MINIKUBE_PROFILE' ..."
      minikube delete --profile "$MINIKUBE_PROFILE"
      echo "  ✓ Minikube profile '$MINIKUBE_PROFILE' deleted"
    else
      echo "  ⚠  Minikube profile '$MINIKUBE_PROFILE' not found"
    fi
  else
    echo "  ⚠  minikube not found"
  fi
  echo ""
fi

# ── 4. Optional: delete kind cluster ─────────────────────────────────────────
if [ "$DELETE_KIND" -eq 1 ]; then
  if command -v kind &>/dev/null; then
    if kind get clusters 2>/dev/null | grep -q "^${KIND_CLUSTER}$"; then
      echo "► Deleting kind cluster '$KIND_CLUSTER' ..."
      kind delete cluster --name "$KIND_CLUSTER"
      echo "  ✓ kind cluster '$KIND_CLUSTER' deleted"
    else
      echo "  ⚠  kind cluster '$KIND_CLUSTER' not found"
    fi
  else
    echo "  ⚠  kind not found"
  fi
  echo ""
fi

# ── 5. Local Docker images (optional) ────────────────────────────────────────
echo "  Note: Local Docker images (carvr/*) were NOT removed."
echo "  To remove them run:"
echo "    docker rmi carvr/analytics-api carvr/questionnaire carvr/dashboard carvr/bridge"
echo ""
echo "══════════════════════════════════════════════════════════"
echo "  ✓ CarVR cleanup complete."
echo "══════════════════════════════════════════════════════════"
echo ""
