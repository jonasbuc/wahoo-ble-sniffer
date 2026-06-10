#!/usr/bin/env bash
# ── validate.sh ───────────────────────────────────────────────────────────────
# Validates Kubernetes YAML and Helm chart without touching any cluster.
# Run from the repository root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ERRORS=0

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  CarVR — Static Validation"
echo "══════════════════════════════════════════════════════════"
echo ""

cd "$REPO_ROOT"

# ── kubectl dry-run ───────────────────────────────────────────────────────────
echo "► kubectl apply --dry-run=client (Kubernetes manifests)"
if command -v kubectl &>/dev/null; then
    if kubectl apply --dry-run=client -f deployment/kubernetes/ -R 2>&1; then
        echo "  ✓ kubectl dry-run passed"
    else
        echo "  ✗ kubectl dry-run FAILED"
        ERRORS=$((ERRORS + 1))
    fi
else
    echo "  ⚠  kubectl not found — skipping dry-run"
fi
echo ""

# ── kubectl kustomize ─────────────────────────────────────────────────────────
echo "► kubectl kustomize"
if command -v kubectl &>/dev/null; then
    if kubectl kustomize deployment/kubernetes/ > /dev/null 2>&1; then
        echo "  ✓ kustomize build passed"
    else
        echo "  ✗ kustomize build FAILED"
        ERRORS=$((ERRORS + 1))
    fi
else
    echo "  ⚠  kubectl not found — skipping kustomize"
fi
echo ""

# ── helm lint ─────────────────────────────────────────────────────────────────
echo "► helm lint"
if command -v helm &>/dev/null; then
    if helm lint deployment/helm/carvr --values deployment/helm/carvr/values-minikube.yaml; then
        echo "  ✓ helm lint (minikube values) passed"
    else
        echo "  ✗ helm lint FAILED"
        ERRORS=$((ERRORS + 1))
    fi
else
    echo "  ⚠  helm not found — skipping lint"
fi
echo ""

# ── helm template ─────────────────────────────────────────────────────────────
echo "► helm template (minikube values)"
if command -v helm &>/dev/null; then
    if helm template carvr deployment/helm/carvr \
            --namespace carvr-local \
            --values deployment/helm/carvr/values-minikube.yaml \
            > /dev/null 2>&1; then
        echo "  ✓ helm template (minikube) passed"
    else
        echo "  ✗ helm template (minikube) FAILED"
        ERRORS=$((ERRORS + 1))
    fi

    echo "► helm template (kind values)"
    if helm template carvr deployment/helm/carvr \
            --namespace carvr-local \
            --values deployment/helm/carvr/values-kind.yaml \
            > /dev/null 2>&1; then
        echo "  ✓ helm template (kind) passed"
    else
        echo "  ✗ helm template (kind) FAILED"
        ERRORS=$((ERRORS + 1))
    fi
else
    echo "  ⚠  helm not found — skipping template"
fi
echo ""

# ── yamllint ─────────────────────────────────────────────────────────────────
echo "► yamllint"
if command -v yamllint &>/dev/null; then
    if yamllint -d relaxed deployment/kubernetes/ deployment/helm/carvr/; then
        echo "  ✓ yamllint passed"
    else
        echo "  ✗ yamllint FAILED"
        ERRORS=$((ERRORS + 1))
    fi
else
    echo "  ⚠  yamllint not installed — skipping (brew install yamllint)"
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────"
if [ "$ERRORS" -gt 0 ]; then
    echo "  ✗ Validation FAILED with $ERRORS error(s)."
    echo ""
    exit 1
fi
echo "  ✓ All validation checks passed."
echo ""
