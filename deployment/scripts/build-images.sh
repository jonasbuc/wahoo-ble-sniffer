#!/usr/bin/env bash
# ── build-images.sh ───────────────────────────────────────────────────────────
# Builds all four CarVR Docker images from the repository root.
#
# Usage:
#   ./deployment/scripts/build-images.sh                   # current platform
#   ./deployment/scripts/build-images.sh --tag v1.2.3      # custom tag
#   MINIKUBE_PROFILE=carvr ./deployment/scripts/build-images.sh --minikube
#   KIND_CLUSTER=carvr     ./deployment/scripts/build-images.sh --kind
#
# By default images are built for the current platform (linux/amd64 or linux/arm64).
# Use --platform linux/amd64 to force amd64 (required for some K8s setups).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAG="${TAG:-latest}"
PLATFORM=""
LOAD_MINIKUBE=0
LOAD_KIND=0
KIND_CLUSTER="${KIND_CLUSTER:-carvr}"
MINIKUBE_PROFILE="${MINIKUBE_PROFILE:-carvr}"

# ── Parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tag)         TAG="$2";    shift 2 ;;
        --platform)    PLATFORM="$2"; shift 2 ;;
        --minikube)    LOAD_MINIKUBE=1; shift ;;
        --kind)        LOAD_KIND=1; shift ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

PLATFORM_ARG=""
[ -n "$PLATFORM" ] && PLATFORM_ARG="--platform $PLATFORM"

echo ""
echo "══════════════════════════════════════════════════════════"
echo "  CarVR — Build Docker Images"
echo "  Tag: $TAG  |  Platform: ${PLATFORM:-auto}"
echo "══════════════════════════════════════════════════════════"
echo ""

cd "$REPO_ROOT"

# ── Build each image ──────────────────────────────────────────────────────────
build_image() {
    local name="$1"
    local dockerfile="deployment/${name}/Dockerfile"
    local image="carvr/${name}:${TAG}"

    echo "► Building $image ..."
    docker build $PLATFORM_ARG \
        -f "$dockerfile" \
        -t "$image" \
        .
    echo "  ✓ $image"
    echo ""
}

build_image analytics-api
build_image questionnaire
build_image dashboard
# Bridge is NOT built for K8s — it runs locally on the host with Unity.
# To build manually: docker build -f deployment/bridge/Dockerfile -t carvr/bridge:latest .

echo "──────────────────────────────────────────────────────────"
echo "  All images built successfully."
echo ""
echo "  Images:"
docker images --filter "reference=carvr/*" --format "  {{.Repository}}:{{.Tag}}  ({{.Size}})"
echo ""

# ── Load into Minikube ────────────────────────────────────────────────────────
if [ "$LOAD_MINIKUBE" -eq 1 ]; then
    echo "► Loading images into Minikube profile: $MINIKUBE_PROFILE"
    for img in analytics-api questionnaire dashboard; do
        echo "  ✓ carvr/${img}:${TAG} loaded"
    done
    echo ""
fi

# ── Load into kind ────────────────────────────────────────────────────────────
if [ "$LOAD_KIND" -eq 1 ]; then
    echo "► Loading images into kind cluster: $KIND_CLUSTER"
    for img in analytics-api questionnaire dashboard; do
      kind load docker-image "carvr/${img}:${TAG}" --name "$KIND_CLUSTER"
        echo "  ✓ carvr/${img}:${TAG} loaded"
    done
    echo ""
fi

echo "  Done."
echo ""
