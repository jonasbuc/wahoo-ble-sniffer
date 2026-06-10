#!/usr/bin/env bash
# ── deploy-kind.sh ────────────────────────────────────────────────────────────
# Builds, loads and deploys the full CarVR stack on a kind (Kubernetes-in-Docker) cluster.
#
# Usage:
#   ./deployment/scripts/deploy-kind.sh              # build + deploy
#   ./deployment/scripts/deploy-kind.sh --skip-build # deploy existing images
#
# Cluster name: carvr   (isolated from any other kind projects)
# Namespace:    carvr-local
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLUSTER="carvr"
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
echo "  CarVR — Deploy to kind  (cluster: $CLUSTER)"
echo "══════════════════════════════════════════════════════════"
echo ""

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
for tool in docker kind kubectl helm; do
  command -v "$tool" &>/dev/null || { echo "✗  $tool not found — run check-prerequisites.sh"; exit 1; }
done

# ── 2. Write kind cluster config (idempotent) ─────────────────────────────────
KIND_CONFIG="$(mktemp /tmp/carvr-kind-config.XXXXXX.yaml)"
trap 'rm -f "$KIND_CONFIG"' EXIT

cat > "$KIND_CONFIG" <<'EOF'
# kind cluster configuration for CarVR
# One control-plane node is sufficient for local testing.
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: carvr
nodes:
  - role: control-plane
    # Extra port mappings allow localhost access without needing a LoadBalancer.
    extraPortMappings:
      - containerPort: 30080  # analytics-api (NodePort)
        hostPort: 30080
        protocol: TCP
      - containerPort: 30090  # questionnaire (NodePort)
        hostPort: 30090
        protocol: TCP
      - containerPort: 30501  # dashboard (NodePort)
        hostPort: 30501
        protocol: TCP
      - containerPort: 30765  # bridge (NodePort)
        hostPort: 30765
        protocol: TCP
EOF

# ── 3. Create cluster if it doesn't exist ────────────────────────────────────
echo "► Checking kind cluster '$CLUSTER' ..."
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER}$"; then
  echo "  ✓ Cluster already exists"
else
  echo "  Creating kind cluster '$CLUSTER' ..."
  kind create cluster --config "$KIND_CONFIG" --name "$CLUSTER"
  echo "  ✓ Cluster created"
fi
echo ""

# ── 4. Build images ───────────────────────────────────────────────────────────
if [ "$SKIP_BUILD" -eq 0 ]; then
  echo "► Building Docker images (local daemon) ..."
  bash deployment/scripts/build-images.sh --tag "$TAG"
  echo ""
fi

# ── 5. Load images into kind cluster ─────────────────────────────────────────
echo "► Loading images into kind cluster '$CLUSTER' ..."
for img in analytics-api questionnaire dashboard bridge; do
  echo "  Loading carvr/${img}:${TAG} ..."
  kind load docker-image "carvr/${img}:${TAG}" --name "$CLUSTER"
  echo "  ✓ carvr/${img}:${TAG}"
done
echo ""

# ── 6. Deploy via Helm ────────────────────────────────────────────────────────
echo "► Running helm upgrade --install ..."
helm upgrade --install carvr deployment/helm/carvr \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --values deployment/helm/carvr/values-kind.yaml \
  --atomic \
  --wait \
  --timeout 5m \
  --kube-context "kind-${CLUSTER}"
echo "  ✓ Helm release deployed"
echo ""

# ── 7. Wait for rollouts ──────────────────────────────────────────────────────
echo "► Waiting for all Deployments to be ready ..."
for dep in analytics-api questionnaire dashboard bridge; do
  kubectl rollout status deployment/"$dep" \
    --namespace "$NAMESPACE" \
    --context "kind-${CLUSTER}" \
    --timeout=3m
  echo "  ✓ $dep ready"
done
echo ""

# ── 8. Show status ────────────────────────────────────────────────────────────
echo "► Cluster status:"
kubectl get pods,services,pvc --namespace "$NAMESPACE" --context "kind-${CLUSTER}"
echo ""

# ── 9. Run smoke tests ────────────────────────────────────────────────────────
echo "► Running smoke tests ..."
KUBE_CONTEXT="kind-${CLUSTER}" bash deployment/scripts/smoke-test.sh
echo ""

# ── 10. Access instructions ───────────────────────────────────────────────────
echo "══════════════════════════════════════════════════════════"
echo "  ✓ CarVR deployed successfully on kind!"
echo ""
echo "  Access via port-forward:"
echo "    kubectl port-forward svc/analytics-api  8080:8080 -n $NAMESPACE --context kind-$CLUSTER"
echo "    kubectl port-forward svc/dashboard       8501:8501 -n $NAMESPACE --context kind-$CLUSTER"
echo "    kubectl port-forward svc/questionnaire   8090:8090 -n $NAMESPACE --context kind-$CLUSTER"
echo ""
echo "  Analytics API:  http://localhost:8080"
echo "  Dashboard:      http://localhost:8501"
echo "  Questionnaire:  http://localhost:8090"
echo ""
echo "  Cleanup:"
echo "    helm uninstall carvr --namespace $NAMESPACE --context kind-$CLUSTER"
echo "    kubectl delete namespace $NAMESPACE  --context kind-$CLUSTER"
echo "    kind delete cluster --name $CLUSTER   # removes the entire cluster"
echo "══════════════════════════════════════════════════════════"
echo ""
