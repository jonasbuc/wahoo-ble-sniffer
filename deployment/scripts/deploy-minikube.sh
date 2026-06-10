#!/usr/bin/env bash
# ── deploy-minikube.sh ────────────────────────────────────────────────────────
# Builds, loads and deploys the full CarVR stack on a Minikube cluster.
#
# Usage:
#   ./deployment/scripts/deploy-minikube.sh              # build + deploy
#   ./deployment/scripts/deploy-minikube.sh --skip-build # deploy existing images
#
# Cluster profile: carvr   (isolated from any other Minikube projects)
# Namespace:       carvr-local
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROFILE="carvr"
NAMESPACE="carvr-local"
SKIP_BUILD=0
TAG="${TAG:-latest}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1; shift ;;
    --tag)        TAG="$2";     shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

cd "$REPO_ROOT"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  CarVR — Deploy to Minikube  (profile: $PROFILE)"
echo "══════════════════════════════════════════════════════════"
echo ""

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
for tool in docker minikube kubectl helm; do
  command -v "$tool" &>/dev/null || { echo "✗  $tool not found — run check-prerequisites.sh"; exit 1; }
done

# ── 2. Start / ensure Minikube cluster ───────────────────────────────────────
echo "► Checking Minikube profile '$PROFILE' ..."
if minikube status --profile "$PROFILE" &>/dev/null; then
  echo "  ✓ Cluster already running"
else
  echo "  Creating new Minikube cluster (profile: $PROFILE) ..."
  minikube start \
    --profile "$PROFILE" \
    --driver=docker \
    --cpus=2 \
    --memory=4g \
    --addons=storage-provisioner
fi
echo ""

# ── 3. Build images inside Minikube's Docker daemon ──────────────────────────
if [ "$SKIP_BUILD" -eq 0 ]; then
  echo "► Building Docker images inside Minikube's Docker daemon ..."
  # Temporarily point Docker CLI to Minikube's daemon so images are
  # immediately available without a separate 'load' step.
  eval "$(minikube docker-env --profile "$PROFILE")"
  bash deployment/scripts/build-images.sh --tag "$TAG"
  # Restore the local Docker daemon
  eval "$(minikube docker-env --profile "$PROFILE" --unset)"
  echo ""
fi

# ── 4. Deploy via Helm ────────────────────────────────────────────────────────
echo "► Running helm upgrade --install ..."
helm upgrade --install carvr deployment/helm/carvr \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --values deployment/helm/carvr/values-minikube.yaml \
  --atomic \
  --wait \
  --timeout 5m \
  --kube-context "$(minikube kubectl --profile "$PROFILE" -- config current-context 2>/dev/null || echo "minikube")"
echo "  ✓ Helm release deployed"
echo ""

# ── 5. Wait for rollouts ──────────────────────────────────────────────────────
echo "► Waiting for all Deployments to be ready ..."
for dep in analytics-api questionnaire dashboard bridge; do
  kubectl rollout status deployment/"$dep" \
    --namespace "$NAMESPACE" \
    --timeout=3m
  echo "  ✓ $dep ready"
done
echo ""

# ── 6. Show status ────────────────────────────────────────────────────────────
echo "► Cluster status:"
kubectl get pods,services,pvc --namespace "$NAMESPACE"
echo ""

# ── 7. Run smoke tests ────────────────────────────────────────────────────────
echo "► Running smoke tests ..."
bash deployment/scripts/smoke-test.sh
echo ""

# ── 8. Access instructions ────────────────────────────────────────────────────
ANALYTICS_URL=$(minikube service analytics-api --profile "$PROFILE" --namespace "$NAMESPACE" --url 2>/dev/null | head -1 || echo "(use port-forward)")
DASHBOARD_URL=$(minikube service dashboard    --profile "$PROFILE" --namespace "$NAMESPACE" --url 2>/dev/null | head -1 || echo "(use port-forward)")
QUEST_URL=$(minikube service questionnaire    --profile "$PROFILE" --namespace "$NAMESPACE" --url 2>/dev/null | head -1 || echo "(use port-forward)")

echo "══════════════════════════════════════════════════════════"
echo "  ✓ CarVR deployed successfully on Minikube!"
echo ""
echo "  Access via port-forward:"
echo "    kubectl port-forward svc/analytics-api  8080:8080 -n $NAMESPACE"
echo "    kubectl port-forward svc/dashboard       8501:8501 -n $NAMESPACE"
echo "    kubectl port-forward svc/questionnaire   8090:8090 -n $NAMESPACE"
echo ""
echo "  Or via Minikube tunnel (NodePort / LoadBalancer):"
echo "    minikube service --all --profile $PROFILE --namespace $NAMESPACE"
echo ""
echo "  Logs:"
echo "    kubectl logs deployment/analytics-api -n $NAMESPACE -f"
echo ""
echo "  Cleanup:"
echo "    helm uninstall carvr --namespace $NAMESPACE"
echo "    kubectl delete namespace $NAMESPACE"
echo "    minikube delete --profile $PROFILE   # removes the entire cluster"
echo "══════════════════════════════════════════════════════════"
echo ""
