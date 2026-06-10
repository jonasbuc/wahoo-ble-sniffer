#!/usr/bin/env bash
# ── check-prerequisites.sh ────────────────────────────────────────────────────
# Checks that all required tools are installed for local K8s deployment.
# Does NOT install anything automatically.
# Run from the repository root.
set -euo pipefail

PASS=0
FAIL=0

check() {
    local tool="$1"
    local hint="$2"
    if command -v "$tool" &>/dev/null; then
        echo "  ✓  $tool  ($(command -v "$tool"))"
        PASS=$((PASS + 1))
    else
        echo "  ✗  $tool  — NOT FOUND"
        echo "     Install: $hint"
        FAIL=$((FAIL + 1))
    fi
}

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  CarVR — Prerequisite Check"
echo "══════════════════════════════════════════════════════════"
echo ""
echo "Required tools:"
check docker    "https://docs.docker.com/get-docker/"
check kubectl   "brew install kubectl  OR  https://kubernetes.io/docs/tasks/tools/"
check helm      "brew install helm     OR  https://helm.sh/docs/intro/install/"
echo ""
echo "Local cluster (at least one required):"
check minikube  "brew install minikube OR  https://minikube.sigs.k8s.io/docs/start/"
check kind      "brew install kind     OR  https://kind.sigs.k8s.io/docs/user/quick-start/"
echo ""
echo "Optional validation tools:"
check yamllint    "brew install yamllint"
check kubeconform "brew install kubeconform"
echo ""
echo "──────────────────────────────────────────────────────────"
echo "  Required: $PASS found"
if [ "$FAIL" -gt 0 ]; then
    echo "  Missing:  $FAIL tool(s) not found"
    echo ""
    echo "  Install missing tools before running deploy scripts."
    echo "──────────────────────────────────────────────────────────"
    echo ""
    exit 1
fi
echo "  All required tools are available."
echo "──────────────────────────────────────────────────────────"
echo ""
